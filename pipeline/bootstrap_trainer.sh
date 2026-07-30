#!/bin/bash
# Prepare a trainer host's Python environment. Version-controlled on purpose.
#
# This procedure was discovered the expensive way (B4 preflight attempts 4-5)
# and must not live only in throwaway EC2 user-data: the same steps have to run
# in the training container at B7 and on SageMaker at B10. User-data now fetches
# the code bundle and execs this file, so what runs on the box is what is
# reviewed here.
#
# Contract: run from the extracted bundle root (requirements.txt beside it).
# Exits non-zero with a named reason on any failure. Callers should not
# continue past a failure -- an environment that is wrong here produces
# checkpoints that cannot be trusted.
#
#   bash pipeline/bootstrap_trainer.sh
#
# Exit codes are distinct so a launcher can report which stage failed:
#   15 pip install   16 torch pin   17 imports   18 pip check   19 stale pkgs
set -o pipefail

ROOT="${1:-$(pwd)}"
REQ="$ROOT/requirements.txt"
[ -f "$REQ" ] || { echo "FATAL: no requirements.txt at $REQ"; exit 15; }

# --- 1. build a CLEAN venv instead of mutating the image's ------------------
# Every package the DLAMI ships is compiled against ITS torch (2.7). Under the
# pinned torch each becomes importable-but-broken, and the failures surface far
# from the cause: torchvision as "operator torchvision::nms does not exist" via
# transformers' lazy Bloom import, transformer_engine as a missing
# libcudnn_graph.so.9 via peft's optional-backend probe. Uninstalling them one
# per launch is whack-a-mole against an unknown list.
#
# A venv with no system site-packages removes the entire class: only the pinned
# set exists. The GPU driver still comes from the AMI, which is the one thing
# pip cannot supply; the CUDA runtime rides along in the torch wheels.
VENV="${VENV:-/opt/medzen/venv}"
BASEPY=""
for c in /opt/pytorch/bin/python python3.12 python3; do
  command -v "$c" >/dev/null 2>&1 && { BASEPY=$(command -v "$c"); break; }
done
[ -n "$BASEPY" ] || { echo "FATAL: no python to build a venv from"; exit 15; }
echo "--- creating clean venv at $VENV from $BASEPY ($("$BASEPY" -V 2>&1)) ---"
rm -rf "$VENV"
"$BASEPY" -m venv "$VENV" || { echo "FATAL: venv creation"; exit 15; }
# shellcheck disable=SC1091
source "$VENV/bin/activate" || { echo "FATAL: venv activate"; exit 15; }
python -m pip install -q --upgrade pip || { echo "FATAL: pip upgrade"; exit 15; }
echo "venv python: $(which python) $(python -V 2>&1)"

# Prove the venv is actually isolated before trusting anything installed in it.
python - <<'PYISO' || { echo "FATAL: venv is not isolated"; exit 19; }
import sys, importlib.util as u
leaked = [m for m in ("torch", "torchvision", "torchaudio", "transformer_engine",
                      "transformers", "peft") if u.find_spec(m) is not None]
print("pre-install importable (must be empty):", leaked or "none")
sys.exit(1 if leaked else 0)
PYISO
echo "VENV ISOLATED"

# --- 2. install the pinned set ---------------------------------------------
echo "--- installing pinned requirements ---"
pip install -q -r "$REQ" || { echo "FATAL: pip install"; exit 15; }

echo "--- resolved environment ---"
pip list 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio|transformers|peft|accelerate|mlflow|datasets|numpy|pyarrow) ' || true

# --- 3. the removed packages must still be gone ----------------------------
# A transitive dependency can quietly reinstall them; that would restore the
# exact breakage this script exists to prevent.
if pip list 2>/dev/null | grep -qiE '^(torchvision|torchaudio) '; then
  echo "FATAL: torchvision/torchaudio reappeared after install"
  exit 19
fi
echo "STALE PACKAGES ABSENT"

# --- 4. metadata consistency ------------------------------------------------
# Cheap, and it catches version conflicts that only bite much later.
pip check || { echo "FATAL: pip check reports an inconsistent environment"; exit 18; }
echo "PIP CHECK OK"

# --- 5. the pin must be the thing that actually loaded ----------------------
# A recorded version that does not match the pin is worse than no pin.
python - "$REQ" <<'PYCHK' || { echo "FATAL: torch pin not satisfied"; exit 16; }
import re, sys, pathlib, torch
want = re.search(r"^torch==(\S+)", pathlib.Path(sys.argv[1]).read_text(), re.M).group(1)
got = torch.__version__.split("+")[0]
print(f"torch pinned={want} loaded={got} cuda_build={torch.version.cuda} avail={torch.cuda.is_available()}")
sys.exit(0 if got == want else 1)
PYCHK

# --- 6. import gate ---------------------------------------------------------
# Fails in ~30s. Attempt 4 spent 15 GPU-minutes and a 3 GB checkpoint download
# before discovering peft could not import at all.
python - <<'PYIMP' || { echo "FATAL: import gate"; exit 17; }
import torch, transformers, peft, accelerate, mlflow
from transformers import BloomPreTrainedModel      # the exact import peft performs
from peft import LoraConfig, get_peft_model
from transformers import WhisperForConditionalGeneration, WhisperProcessor
print("IMPORT GATE OK: torch", torch.__version__, "| transformers", transformers.__version__,
      "| peft", peft.__version__, "| cuda", torch.cuda.is_available())
PYIMP

echo "BOOTSTRAP OK"
