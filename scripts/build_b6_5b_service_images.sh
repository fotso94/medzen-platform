#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: build_b6_5b_service_images.sh SOURCE_COMMIT OUTPUT_DIR" >&2
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

mkdir -p "$output_dir"
for service in rag-index llm-gateway speech-orchestrator speech-tts-gateway; do
  case "$service" in
    speech-orchestrator) repository=medzen-orchestrator ;;
    *) repository=medzen-$service ;;
  esac
  tag="$repository:b6-5b-${source_commit%?????????????????????????????????}"
  archive="$output_dir/$repository.oci.tar"
  docker buildx build \
    --file "services/$service/Dockerfile" \
    --platform linux/amd64 \
    --provenance=false \
    --sbom=false \
    --build-arg "SOURCE_COMMIT=$source_commit" \
    --tag "$tag" \
    --output "type=oci,dest=$archive" \
    .
  docker load --input "$archive"
  .venv/bin/python scripts/check_b6_service_image.py \
    "$tag" "$service" "$source_commit" \
    > "$output_dir/$repository.runtime.json"
  docker scout cves --exit-code --only-severity critical,high \
    --format sarif --output "$output_dir/$repository.scout.sarif.json" \
    "local://$tag"
done
