#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: build_b6_orchestrator_websocket_image.sh SOURCE_COMMIT OUTPUT_DIR" >&2
  exit 2
fi

source_commit=$1
output_dir=$2
case "$source_commit" in
  *[!0-9a-f]*|'') echo "source commit must be a lowercase Git SHA" >&2; exit 2 ;;
esac
if [ "${#source_commit}" -ne 40 ]; then
  echo "source commit must contain 40 hex characters" >&2
  exit 2
fi
if [ "$(git rev-parse HEAD)" != "$source_commit" ]; then
  echo "source commit is not the checked-out HEAD" >&2
  exit 2
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "source worktree must be clean before image qualification" >&2
  exit 2
fi

repository=medzen-orchestrator
short_commit=$(printf '%s' "$source_commit" | cut -c1-7)
tag="$repository:b6-ws-$short_commit"
archive="$output_dir/$repository.oci.tar"
runtime_receipt="$output_dir/$repository.runtime.json"
scan_receipt="$output_dir/$repository.scout.sarif.json"

mkdir -p "$output_dir"
for path in "$archive" "$runtime_receipt" "$scan_receipt"; do
  if [ -e "$path" ]; then
    echo "refusing to overwrite qualification output: $path" >&2
    exit 2
  fi
done

docker buildx build \
  --file services/speech-orchestrator/Dockerfile \
  --platform linux/amd64 \
  --provenance=false \
  --sbom=false \
  --build-arg "SOURCE_COMMIT=$source_commit" \
  --tag "$tag" \
  --output "type=oci,dest=$archive" \
  .
docker load --input "$archive"
.venv/bin/python scripts/check_b6_service_image.py \
  "$tag" speech-orchestrator "$source_commit" >"$runtime_receipt"
docker scout cves --exit-code --only-severity critical,high \
  --format sarif --output "$scan_receipt" "local://$tag"

printf '%s\n' "$tag"
