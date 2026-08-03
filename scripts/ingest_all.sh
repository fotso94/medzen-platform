#!/usr/bin/env bash
# Bulk ingest driver.
#
# Termination discipline: each corpus runs in its OWN PROCESS GROUP so a
# timeout can signal the whole tree. kill -9 on the parent alone orphans
# download workers and multiprocessing resource-trackers, which is how the
# first run left debris behind. Order is TERM -> grace -> KILL.
#
# Resume-safe: the eval-immutability guard makes finished corpora skip.
# Run under tmux/screen for anything long — not as a transient background job.
cd "$(dirname "$0")/.." || exit 1

LIMIT="${LIMIT:-300}"
PER_CORPUS_TIMEOUT="${PER_CORPUS_TIMEOUT:-1800}"   # 30 min: 913 MB worst shard
GRACE="${GRACE:-15}"                               # seconds between TERM and KILL
LOG="${LOG:-/tmp/medzen_ingest.log}"
ONLY="${ONLY:-}"                                   # e.g. "acholi:tts ewe:tts"
PY=.venv/bin/python

export PYTHONUNBUFFERED=1
export HF_HUB_DOWNLOAD_TIMEOUT=60
export HF_HUB_DISABLE_XET=1        # measured ~40% faster than the Xet backend here
export AWS_PROFILE=medzen

: > "$LOG"
say() { echo "$@" | tee -a "$LOG"; }

# Signal a whole process group: negative PID = the group.
stop_group() {
  local pgid=$1
  kill -TERM "-$pgid" 2>/dev/null
  for _ in $(seq 1 "$GRACE"); do
    kill -0 "-$pgid" 2>/dev/null || return 0
    sleep 1
  done
  say "    group $pgid ignored TERM after ${GRACE}s — sending KILL"
  kill -KILL "-$pgid" 2>/dev/null
}

if [ -n "$ONLY" ]; then
  ORDER="$ONLY"
else
  ORDER=$($PY -c "
import sys; sys.path.insert(0,'.')
from pipeline.adapters.waxalnlp import CONFIGS
pairs=[(l,t) for l,ts in CONFIGS.items() for t in ts]
pairs.sort(key=lambda p:(p[1]!='tts', p[0]))
print('\n'.join(f'{l}:{t}' for l,t in pairs))")
fi

declare -a OK=() FAIL=() SKIP=() SLOW=()
say "bulk ingest $(date '+%H:%M:%S')  limit=$LIMIT  timeout=${PER_CORPUS_TIMEOUT}s  grace=${GRACE}s"

for c in $ORDER; do
  lang=${c%%:*}; task=${c##*:}
  say ""
  say "═══ $lang / $task  ($(date '+%H:%M:%S')) ═══"
  mark=$(wc -l < "$LOG" | tr -d " ")   # macOS wc pads: strip or tail -n + breaks

  # setsid puts the child in a new process group; fall back to plain exec if
  # unavailable (then the group is the child's own pid anyway under set -m).
  if command -v setsid >/dev/null 2>&1; then
    setsid $PY -m pipeline.ingest --source waxalnlp --language "$lang" \
      --task "$task" --limit "$LIMIT" >>"$LOG" 2>&1 &
  else
    set -m
    $PY -m pipeline.ingest --source waxalnlp --language "$lang" \
      --task "$task" --limit "$LIMIT" >>"$LOG" 2>&1 &
    set +m
  fi
  pid=$!
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  pgid=${pgid:-$pid}

  waited=0; timed_out=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 10; waited=$((waited+10))
    if [ "$waited" -ge "$PER_CORPUS_TIMEOUT" ]; then
      say "  TIMEOUT after ${waited}s — terminating process group $pgid"
      stop_group "$pgid"; timed_out=1; break
    fi
  done
  wait "$pid" 2>/dev/null; rc=$?
  tail_out=$(tail -n "+${mark:-1}" "$LOG" 2>/dev/null)
  [ -z "$tail_out" ] && tail_out=$(tail -60 "$LOG")   # never classify on empty

  if [ "$timed_out" = "1" ]; then
    SLOW+=("$lang/$task")
  elif grep -q "REFUSING" <<<"$tail_out"; then
    say "  already ingested — skipped"; SKIP+=("$lang/$task")
  elif grep -q "wrote s3.*curated" <<<"$tail_out"; then
    grep -E 'built [0-9]+|split by|uploaded [0-9]+ raw' <<<"$tail_out" | sed 's/^/  /' | tee -a "$LOG"
    say "  DONE in ${waited}s (rc=$rc)"; OK+=("$lang/$task")
  else
    grep -E 'FAIL|REJECTED|Error|no usable|Traceback' <<<"$tail_out" | head -4 | sed 's/^/  /'
    say "  FAILED rc=$rc after ${waited}s"; FAIL+=("$lang/$task")
  fi

  # no orphans should survive a corpus
  strays=$(pgrep -g "$pgid" 2>/dev/null | wc -l | tr -d ' ')
  [ "$strays" != "0" ] && say "  WARNING: $strays stray process(es) in group $pgid"
done

say ""
say "════════ SUMMARY $(date '+%H:%M:%S') ════════"
say "  ingested (${#OK[@]}):  ${OK[*]:-none}"
say "  skipped  (${#SKIP[@]}): ${SKIP[*]:-none}"
say "  timeout  (${#SLOW[@]}): ${SLOW[*]:-none}"
say "  failed   (${#FAIL[@]}): ${FAIL[*]:-none}"
