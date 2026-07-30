#!/bin/bash
# Build-time audit of the INSTALLED Python dependencies (OSV + PyPI advisories).
#
# Why this exists: ECR basic scanning covers OS packages ONLY. It found the perl
# CVEs in the Debian layer and said nothing whatsoever about torch, transformers,
# or the Flask/gunicorn/alembic tail mlflow installs -- the larger part of this
# image. Coverage should not depend on a registry setting.
#
# Why it runs at build time only: pip-audit downloads an advisory database over
# the network. The trainer deliberately has no guarantee of internet -- the base
# checkpoint comes from the S3 cache and the image sets HF_HUB_OFFLINE=1 -- so
# the same check at container start would fail closed on a healthy host.
#
# THE TARGET ENVIRONMENT IS NEVER MUTATED.
#
# An earlier version installed pip-audit into the environment it was auditing
# and then removed what it had added, matched by package name. That was wrong:
# name matching does not undo a VERSION UPGRADE to a package that was already
# present, so pip-audit could silently bump a transitive dependency and leave the
# shipped environment different from the pinned one it had been verified as. `pip
# check` would not catch it either -- it validates mutual compatibility, not
# exact versions.
#
# So: the installed set is exported to a freeze file, pip-audit runs from its own
# venv against that file with --no-deps, and the freeze is compared byte-for-byte
# before and after. The first measure means nothing can be mutated; the second
# proves it.
set -o pipefail

PIP_AUDIT_VERSION="${PIP_AUDIT_VERSION:-2.10.1}"
AUDIT_VENV="${AUDIT_VENV:-/tmp/medzen-audit-venv}"
FREEZE_BEFORE=/tmp/freeze-before.txt
FREEZE_AFTER=/tmp/freeze-after.txt

# --all so pip/setuptools/wheel are audited too -- pip itself was the only thing
# with open advisories when this gate was written.
pip freeze --all > "$FREEZE_BEFORE" || { echo "FATAL: cannot export freeze"; exit 50; }
echo "installed set: $(wc -l < "$FREEZE_BEFORE" | tr -d ' ') packages"

# A freeze containing editable installs or direct URLs cannot be audited by
# pinned version, and silently auditing less than the whole set is worse than
# failing here.
if grep -qE '^-e |@ file://|^#' "$FREEZE_BEFORE"; then
  echo "FATAL: freeze contains editable or local installs; cannot audit by pin"
  grep -nE '^-e |@ file://' "$FREEZE_BEFORE" | head
  exit 50
fi

# Isolated venv: no --system-site-packages, so pip-audit and its dependencies
# land nowhere near the trainer's environment.
rm -rf "$AUDIT_VENV"
python -m venv "$AUDIT_VENV" || { echo "FATAL: cannot create audit venv"; exit 50; }
"$AUDIT_VENV/bin/pip" install -q --upgrade pip || { echo "FATAL: audit venv pip"; exit 50; }
"$AUDIT_VENV/bin/pip" install -q "pip-audit==$PIP_AUDIT_VERSION" \
  || { echo "FATAL: cannot install pip-audit"; exit 50; }

echo "--- auditing $FREEZE_BEFORE with pip-audit $PIP_AUDIT_VERSION (isolated) ---"
# --no-deps: the freeze already IS the full transitive set, so resolution would
# add nothing and could reach the network for metadata we do not need.
"$AUDIT_VENV/bin/pip-audit" --progress-spinner off --strict --no-deps -r "$FREEZE_BEFORE"
RC=$?

rm -rf "$AUDIT_VENV"

# Prove the invariant rather than assuming it. If these differ, something in this
# script touched the environment and the image must not be trusted as the pinned
# one, regardless of what the audit said.
pip freeze --all > "$FREEZE_AFTER" || { echo "FATAL: cannot re-export freeze"; exit 52; }
if ! diff -q "$FREEZE_BEFORE" "$FREEZE_AFTER" >/dev/null; then
  echo "FATAL: the environment changed during the audit."
  echo "  The image would ship a different dependency set than the one verified."
  diff "$FREEZE_BEFORE" "$FREEZE_AFTER" | head -20
  exit 52
fi
echo "FREEZE UNCHANGED: environment byte-identical before and after the audit"

if [ $RC -ne 0 ]; then
  echo "FATAL: pip-audit reported vulnerable Python dependencies."
  echo "  Bump the offending pin in requirements.txt and rebuild. Do not waive:"
  echo "  ECR basic scanning does not cover these packages, so this gate is the"
  echo "  only thing looking at them."
  exit 51
fi
echo "PIP AUDIT OK: no known vulnerabilities in the installed set"
