#!/bin/bash
# Build-time audit of the INSTALLED Python dependencies (OSV + PyPI advisories).
#
# Why this is separate from bootstrap_trainer.sh's verify gate: pip-audit
# downloads an advisory database over the network. The trainer runs with no
# guarantee of internet — deliberately so, since the base checkpoint comes from
# the S3 cache and the image sets HF_HUB_OFFLINE=1 — so an audit in the runtime
# path would either fail closed on a healthy host or pass vacuously.
#
# Why it exists at all: ECR basic scanning covers OS packages only. It found the
# perl CVEs in the Debian layer and said nothing whatsoever about torch,
# transformers or the Flask/gunicorn tail mlflow installs, which is the larger
# part of this image. Coverage should not depend on a registry setting.
#
# It audits the installed environment rather than requirements.txt, because the
# transitive set is what actually ships. pip-audit and anything it pulls in are
# removed afterwards by NAME DIFFERENCE, so the audit tool never lands in the
# image; a package it merely upgraded is deliberately left alone, and the verify
# gate that runs after this proves the environment is still intact.
set -o pipefail

PIP_AUDIT_VERSION="${PIP_AUDIT_VERSION:-2.10.1}"

before=$(mktemp)
after=$(mktemp)
pip list --format=freeze 2>/dev/null | cut -d= -f1 | sort > "$before"

pip install -q "pip-audit==$PIP_AUDIT_VERSION" || { echo "FATAL: cannot install pip-audit"; exit 50; }

echo "--- auditing installed python dependencies ---"
pip-audit --progress-spinner off --strict
RC=$?

# Remove exactly what the audit added, by name. Comparing names rather than
# name==version matters: if pip-audit upgraded a package that was already
# present, uninstalling it would strip a dependency the trainer needs.
pip list --format=freeze 2>/dev/null | cut -d= -f1 | sort > "$after"
added=$(comm -13 "$before" "$after")
if [ -n "$added" ]; then
  echo "removing audit-only packages: $(echo $added | tr '\n' ' ')"
  echo "$added" | xargs -r pip uninstall -y -q 2>/dev/null || true
fi
rm -f "$before" "$after"

if [ $RC -ne 0 ]; then
  echo "FATAL: pip-audit reported vulnerable Python dependencies."
  echo "  Bump the offending pin in requirements.txt and rebuild. Do not waive:"
  echo "  ECR basic scanning does not cover these packages, so this gate is the"
  echo "  only thing looking at them."
  exit 51
fi
echo "PIP AUDIT OK: no known vulnerabilities in the installed set"
