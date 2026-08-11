# MedZen runtime image hardening standard v2

Status: **ACTIVE FOR NEW SERVING IMAGE WORK**

This version supersedes `runtime-image-hardening-v1.md` prospectively. The v1
file and every historical decision or evidence hash bound to it remain
unchanged.

## Build boundary

- Pin every base image by SHA-256 digest.
- Use a named builder stage whenever dependency installation requires pip,
  venv bootstrap packages, compilers, headers, source-control clients or other
  build tooling.
- Copy only the completed runtime environment and application source into a
  clean final stage.
- Keep package installers, build caches, venv bootstrap wheels, compilers and
  source-control clients out of the final stage.
- Do not bake model weights, credentials or mutable remote references into a
  serving image.

## Runtime boundary

- Run under a fixed non-root UID and GID.
- Support a read-only root filesystem and write only to explicitly mounted
  paths.
- Install only the OS libraries required during service execution.
- Preserve offline model loading and content-addressed artifact verification.
- Pin Kubernetes workloads to the scan-passed deployable child digest, never a
  tag or an OCI index digest.

## Mandatory evidence

Before any deployment packet may be approved:

1. Assert the final OS package inventory excludes every known build-only
   package introduced by the builder.
2. Assert language package installers and their executables are absent when
   they are not runtime dependencies.
3. Run service import, dynamic-link and contract smokes from the final image.
   Any service exposing WebSocket routes must also start the final container and
   complete a real TCP/RFC 6455 upgrade against each qualifying route. A
   handshake alone is insufficient: the exact deployment-window client must
   also complete its full synthetic session through authentication, request
   frames, intermediate events, final-result delivery and orderly close
   against the containerized application with checksum-bound fake
   dependencies. Persist the event sequence and bind the passing client and
   in-image application sources by SHA-256. An in-process `TestClient`
   exchange is not a runtime-protocol qualification.
4. Run the repository's local critical/high security scan.
5. Under a separate owner-approved scan-only packet, push the immutable image
   and require the automatic ECR scan of the deployable child to reach
   `COMPLETE` with zero critical and zero high findings.
6. Record the local image identity, OCI index, deployable child digest, package
   inventory, scan result and source commit as immutable evidence.

Local scanning is a useful prerequisite but never overrides an automatic ECR
failure. A security exception requires its own prospective owner decision; it
must not be introduced during a deployment run.
