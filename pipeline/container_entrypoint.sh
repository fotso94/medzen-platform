#!/bin/bash
# Container entrypoint for medzen-trainer.
#
# Verifies the environment, then execs the trainer with whatever arguments the
# caller passed. `exec` matters: the trainer must be PID 1's process so it
# receives SIGTERM directly. On a Spot reclaim that signal is the two-minute
# warning, and a shell sitting in between would swallow it.
set -o pipefail

ROOT=/opt/medzen
cd "$ROOT" || exit 1

# Same gates as the build and as the EC2 venv path. ~15s against a multi-hour
# run, and it catches a host whose driver cannot actually reach the GPU.
MODE=verify bash "$ROOT/pipeline/bootstrap_trainer.sh" "$ROOT"
RC=$?
[ $RC -eq 0 ] || { echo "FATAL: environment verification failed rc=$RC"; exit $RC; }

# CUDA is not asserted above on purpose: the build host has no GPU, so the
# shared gate cannot require it. Assert it here, where a GPU must exist.
# train_asr.py refuses non-smoke training without CUDA as well; this simply
# fails a second earlier and says so more plainly.
python - <<'PY' || exit 30
import sys, torch
ok = torch.cuda.is_available()
print(f"CUDA available={ok} device={torch.cuda.get_device_name(0) if ok else None}")
sys.exit(0 if ok else 1)
PY

exec python -m pipeline.train_asr "$@"
