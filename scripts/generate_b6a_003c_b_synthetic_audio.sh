#!/usr/bin/env bash
# Reproduce the only permitted B6A transcription input on the bound macOS toolchain.
set -euo pipefail

expected_sha="3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3"
phrase="This is a synthetic MedZen platform test. No patient data is present."

if [[ $# -ne 1 ]]; then
  echo "usage: $0 OUTPUT.wav" >&2
  exit 2
fi
output="$1"
[[ ! -e "$output" ]] || { echo "REFUSING: output already exists" >&2; exit 2; }
command -v say >/dev/null || { echo "REFUSING: macOS say is absent" >&2; exit 2; }
command -v ffmpeg >/dev/null || { echo "REFUSING: ffmpeg is absent" >&2; exit 2; }

temporary="$(mktemp -d)"
cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT

say -v Samantha -r 145 -o "$temporary/source.aiff" "$phrase"
ffmpeg -hide_banner -loglevel error -y \
  -i "$temporary/source.aiff" \
  -map_metadata -1 -ac 1 -ar 16000 -c:a pcm_s16le \
  -fflags +bitexact -flags:a +bitexact "$output"

actual_sha="$(shasum -a 256 "$output" | awk '{print $1}')"
if [[ "$actual_sha" != "$expected_sha" ]]; then
  rm -f -- "$output"
  echo "REFUSING: synthesized WAV differs from the packet-bound bytes" >&2
  exit 1
fi
printf 'PASS_B6A_SYNTHETIC_AUDIO sha256=%s\n' "$actual_sha"
