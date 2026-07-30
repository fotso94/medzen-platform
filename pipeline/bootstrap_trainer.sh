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

# --- 1. remove the DLAMI's torch-coupled packages --------------------------
# torchvision/torchaudio ship compiled against the image's torch (2.7). Under
# the pinned torch they still IMPORT but register no ops, so any consumer hits
# "operator torchvision::nms does not exist". transformers touches
# torchvision.io while lazily loading Bloom, and peft imports Bloom at module
# scope -- so a stale torchvision breaks every peft import. Nothing in this
# pipeline uses either package: absent is safe, stale-and-broken is not.
echo "--- removing torch-coupled image packages ---"
pip uninstall -y torchvision torchaudio 2>/dev/null || true

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
