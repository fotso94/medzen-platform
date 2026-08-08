#!/usr/bin/env bash
set -euo pipefail

image_ref="${1:-}"
expected_commit="${2:-}"

if [[ ! "$image_ref" =~ ^medzen-asr-runtime:b6a-003c-[0-9a-f]{7}$ ]]; then
  echo "REFUSING: exact local B6A 003C ASR tag is required." >&2
  exit 2
fi
if [[ ! "$expected_commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "REFUSING: exact 40-character source commit is required." >&2
  exit 2
fi

architecture="$(docker image inspect "$image_ref" --format '{{.Architecture}}')"
runtime_user="$(docker image inspect "$image_ref" --format '{{.Config.User}}')"
source_commit="$(docker image inspect "$image_ref" \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
descriptor="$(docker image inspect "$image_ref" --format '{{json .Descriptor}}')"

[[ "$architecture" == amd64 ]] || {
  echo "REFUSING: final image architecture is $architecture, expected amd64." >&2
  exit 2
}
[[ "$runtime_user" == 10001:10001 ]] || {
  echo "REFUSING: final image user is $runtime_user, expected 10001:10001." >&2
  exit 2
}
[[ "$source_commit" == "$expected_commit" ]] || {
  echo "REFUSING: source label is $source_commit, expected $expected_commit." >&2
  exit 2
}

docker run --rm --platform linux/amd64 --entrypoint /bin/sh "$image_ref" -ec '
  for pkg in python3-pip-whl python3-setuptools-whl python3.12-venv; do
    if dpkg-query -s "$pkg" >/dev/null 2>&1; then
      echo "REFUSING: forbidden installed package: $pkg" >&2
      exit 2
    fi
  done
  test ! -e /opt/venv/bin/pip
  test ! -e /opt/venv/bin/pip3
  if python -m pip --version >/dev/null 2>&1; then
    echo "REFUSING: pip module remains importable." >&2
    exit 2
  fi
  echo FINAL_RUNTIME_BUILD_TOOLS_ABSENT_PASS
'

docker run --rm --platform linux/amd64 --entrypoint python "$image_ref" -c '
import ctypes
import ctranslate2
import faster_whisper
for library in ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9"):
    ctypes.CDLL(library)
print("ASR_RUNTIME_IMPORT_LINK_SMOKE_PASS")
'

docker scout cves --exit-code --only-severity critical,high "local://$image_ref"

echo "PASS_B6A_003C_LOCAL_IMAGE image=$image_ref commit=$expected_commit descriptor=$descriptor"
