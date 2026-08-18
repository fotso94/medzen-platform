# B6 serving deployment runbook (task F — authored dark, deploy gated)

Executes Base v5 §B6 rows 1–4. A step is DONE when its exit condition
is demonstrably true, not when its commands have run. Deployment is an
owner-gated act: this runbook does not self-execute.

## 0. Preconditions

- B6-ASR-BASE-MODEL-2026-001 PUBLISHED (done 2026-08-17).
- All five images pushed at immutable digests; every PLACEHOLDER_TAG in
  platform/k8s/base/*.yaml replaced by digest — `grep -r PLACEHOLDER
  platform/k8s/base/` MUST return empty before any apply.
- ACM certificate + the backend-only security group created and bound
  into ingress-internal.yaml (PLACEHOLDER_ACM_CERT_ARN,
  PLACEHOLDER_BACKEND_ONLY_SG).
- AWS Load Balancer Controller installed (medzen-aws-load-balancer-
  controller image already in ECR).
- GPU capacity: the serving ASR pod needs nvidia.com/gpu:1 — raise the
  serving node group (NOT the eval ASG; they are separate lifecycles).

## 1. Deploy in dependency order (B6 row 1)

Order matters: each service must be Ready before its dependents start.

    kubectl apply -f platform/k8s/base/rag-index.yaml
    kubectl -n medzen rollout status deploy/rag-index --timeout=600s
    kubectl apply -f platform/k8s/base/asr-runtime.yaml
    kubectl -n medzen rollout status deploy/asr-runtime --timeout=900s
    # ASR is the long pole: model-loader init (pull -> SHA-256 -> smoke
    # -> ready) plus startupProbe 30x10s
    kubectl apply -f platform/k8s/base/tts-gateway.yaml
    kubectl -n medzen rollout status deploy/tts-gateway --timeout=600s
    kubectl apply -f platform/k8s/base/llm-gateway.yaml
    kubectl -n medzen rollout status deploy/llm-gateway --timeout=600s
    kubectl apply -f platform/k8s/base/speech-orchestrator.yaml
    kubectl -n medzen rollout status deploy/speech-orchestrator --timeout=600s

EXIT: all pods Ready AND one real transcription served end to end
(`scripts/b6_live_contract_probe.py --smoke` against a port-forward).

## 2. Internal ALB, orchestrator-only (B6 row 2)

    kubectl apply -f platform/k8s/base/ingress-internal.yaml
    kubectl -n medzen get ingress speech-orchestrator  # ADDRESS appears

EXIT: orchestrator reachable from the MedZen backend security group;
ASR/TTS/LLM/rag DENIED externally — prove the negative:
`curl` each model service's ClusterIP from outside the cluster fails,
and the ALB serves ONLY orchestrator routes.

## 3. Live contract suite (B6 row 3)

    MEDZEN_ORCHESTRATOR_URL=https://<alb-dns> \
      python3 scripts/b6_live_contract_probe.py --full

EXIT: probe reports PASS_LIVE_CONTRACTS — §7-v4 file + WebSocket
streaming contracts, every response carrying all model versions, and
the request id traceable through OTel.

## 4. Live failure drills (B6 row 4)

Follow platform/runbooks/B6-LIVE-DRILL-RUNBOOK.md. EXIT: every drill
degrades per §A6 with no 500 cascade — the live twin of the committed
local drill suite (tests/test_b6_7_drills.py).

## Rollback

Any step failing its exit condition: `kubectl rollout undo` the
affected deployment (previous ReplicaSet retained), or delete the
ingress to detach the ALB. No data-plane state lives in the cluster;
rollback is stateless.
