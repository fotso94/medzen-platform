#!/usr/bin/env bash
set -euo pipefail

# Rebuild the exact upstream NVIDIA DRA v0.4.1 sources locally with only the
# three fixable vulnerable build inputs updated. This script never pushes or
# deploys the resulting image.

DRA_REPOSITORY="https://github.com/NVIDIA/k8s-dra-driver.git"
DRA_COMMIT="764900e7833798a1528bd52a2af3e1b2d5f7a616"
TOOLKIT_REPOSITORY="https://github.com/NVIDIA/nvidia-container-toolkit.git"
TOOLKIT_COMMIT="09ceee5dde66ba9ce25c7cc69b1ebd5e6e3266fa"
GO_IMAGE="golang:1.26.4@sha256:f92b729f5f76b045df75ee1cb324ea68658bbc82feecd286c6ce08bf339fd74d"
EXPECTED_HOOK_SHA256="ebbe5839a703c5aa16796bdbadbea68439831f832660de49fc5c0fb70863c5b6"
IMAGE_TAG="${1:-medzen-nvidia-dra:v0.4.1-medzen.2}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH_FILE="$REPO_ROOT/platform/dependencies/nvidia-dra-v0.4.1/medzen-security.patch"

if [[ "$IMAGE_TAG" != medzen-nvidia-dra:* ]]; then
  echo "REFUSING: local image tag must start with medzen-nvidia-dra:." >&2
  exit 2
fi

for command_name in docker git shasum; do
  command -v "$command_name" >/dev/null || {
    echo "REFUSING: required command is missing: $command_name" >&2
    exit 2
  }
done

build_root="$(mktemp -d "${TMPDIR:-/tmp}/medzen-dra-build.XXXXXX")"
cleanup() {
  rm -rf -- "$build_root"
}
trap cleanup EXIT

dra_source="$build_root/dra"
toolkit_source="$build_root/toolkit"

git clone --quiet "$DRA_REPOSITORY" "$dra_source"
git -C "$dra_source" checkout --quiet --detach "$DRA_COMMIT"
[[ "$(git -C "$dra_source" rev-parse HEAD)" == "$DRA_COMMIT" ]] || {
  echo "REFUSING: DRA source commit mismatch." >&2
  exit 2
}

docker run --rm --platform linux/amd64 \
  -v "$dra_source:/src" -w /src "$GO_IMAGE" \
  go get golang.org/x/net@v0.55.0 google.golang.org/grpc@v1.82.1
docker run --rm --platform linux/amd64 \
  -v "$dra_source:/src" -w /src "$GO_IMAGE" go mod vendor
docker run --rm --platform linux/amd64 \
  -v "$dra_source:/src" -w /src "$GO_IMAGE" go test -mod=vendor ./...

git clone --quiet "$TOOLKIT_REPOSITORY" "$toolkit_source"
git -C "$toolkit_source" checkout --quiet --detach "$TOOLKIT_COMMIT"
[[ "$(git -C "$toolkit_source" rev-parse HEAD)" == "$TOOLKIT_COMMIT" ]] || {
  echo "REFUSING: container-toolkit source commit mismatch." >&2
  exit 2
}
mkdir -p "$toolkit_source/out"
docker run --rm --platform linux/amd64 \
  -e CGO_ENABLED=1 -e GOOS=linux -e GOARCH=amd64 \
  -v "$toolkit_source:/src" -w /src "$GO_IMAGE" \
  go build -trimpath \
  -ldflags="-s -w -X github.com/NVIDIA/nvidia-container-toolkit/internal/info.gitCommit=${TOOLKIT_COMMIT}+medzen-go1.26.4 -X github.com/NVIDIA/nvidia-container-toolkit/internal/info.version=v1.19.1-medzen.1" \
  -o /src/out/nvidia-cdi-hook ./cmd/nvidia-cdi-hook

hook_sha256="$(shasum -a 256 "$toolkit_source/out/nvidia-cdi-hook" | awk '{print $1}')"
if [[ "$hook_sha256" != "$EXPECTED_HOOK_SHA256" ]]; then
  echo "REFUSING: rebuilt nvidia-cdi-hook hash is $hook_sha256, expected $EXPECTED_HOOK_SHA256." >&2
  exit 2
fi

mkdir -p "$dra_source/medzen"
cp "$toolkit_source/out/nvidia-cdi-hook" "$dra_source/medzen/nvidia-cdi-hook"
git -C "$dra_source" apply "$PATCH_FILE"
git -C "$dra_source" diff --check

docker buildx build --platform linux/amd64 --load \
  --tag "$IMAGE_TAG" \
  --build-arg GOLANG_VERSION=1.26.4 \
  --build-arg VERSION=v0.4.1-medzen.2 \
  --build-arg GIT_COMMIT="${DRA_COMMIT}+medzen-security-2" \
  --build-arg GOCACHE=/go/build-cache \
  --build-arg GOMODCACHE=/go/pkg/mod \
  --provenance=false --sbom=false \
  --file "$dra_source/deployments/container/Dockerfile" "$dra_source"

docker run --rm --platform linux/amd64 \
  --entrypoint /usr/bin/gpu-kubelet-plugin "$IMAGE_TAG" --version
docker run --rm --platform linux/amd64 \
  --entrypoint /usr/bin/nvidia-cdi-hook "$IMAGE_TAG" --version
docker scout cves --exit-code --only-severity critical,high \
  "local://$IMAGE_TAG"
docker image inspect "$IMAGE_TAG" \
  --format 'LOCAL_ONLY_IMAGE id={{.Id}} architecture={{.Architecture}} bytes={{.Size}}'
