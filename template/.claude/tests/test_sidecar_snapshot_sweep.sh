#!/usr/bin/env bash
# Proof for lib/sidecar_common.sh's sc_snapshot_sweep (the sidecar-snapshots cleanup called from
# sc_reexec_snapshot before every dispatch/--check):
#   1. a fresh (<24h) snapshot survives regardless of pid liveness (too new to judge);
#   2. an old (24h-7d) snapshot with a DEAD pid is swept;
#   3. an old (24h-7d) snapshot with a LIVE pid survives -- the core safety property, never delete
#      a snapshot a real run may still be reading;
#   4. an ancient (>7d) snapshot is swept even with a LIVE pid -- the hard ceiling that bounds the
#      pile even when `kill -0` false-positives "alive" for a reused pid number;
#   5. the sweep costs a CONSTANT number of `find` spawns, not one per file. Regression for the
#      2026-09-15 incident: 1021 accumulated snapshots (never swept, because Windows pid reuse made
#      `kill -0` false-positive "alive" often enough) cost ~30s of `find` spawns on EVERY single
#      dispatch and --check, growing worse every day.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
# shellcheck source=../scripts/lib/sidecar_common.sh
. "$LIB" >/dev/null 2>&1
fail=0
ran=0

WORK="$(mktemp -d)"
BULK="$(mktemp -d)"
trap 'rm -rf "$WORK" "$BULK"' EXIT

touch_at() { touch -d "-${2} minutes" "$1" 2>/dev/null || touch "$1"; }  # $1 path, $2 minutes-ago

check() {  # $1 label, $2 expect(exists|gone), $3 path
  ran=$((ran + 1))
  if [ "$2" = "exists" ]; then
    if [ -e "$3" ]; then echo "OK   $1 (survived)"; else echo "FAIL $1: expected to survive, was swept"; fail=1; fi
  else
    if [ -e "$3" ]; then echo "FAIL $1: expected to be swept, still present"; fail=1; else echo "OK   $1 (swept)"; fi
  fi
}

DEAD_PID=999999   # not a real pid on any dev box; kill -0 must report it dead
LIVE_PID=$$       # this test process, guaranteed alive for the sweep's duration

fresh_dead="$WORK/anthropic_sidecar.sh.${DEAD_PID}.sh";  touch_at "$fresh_dead" 5      # <24h
old_dead="$WORK/opencode_sidecar.sh.$((DEAD_PID+1)).sh"; touch_at "$old_dead" 2000     # 24h-7d
old_live="$WORK/codex_proxy_sidecar.sh.${LIVE_PID}.sh";  touch_at "$old_live" 2000     # 24h-7d
ancient_live="$WORK/deepseek_sidecar.sh.${LIVE_PID}.sh"; touch_at "$ancient_live" 10200 # >7d

sc_snapshot_sweep "$WORK"

check "fresh + dead pid survives (too new to judge)" exists "$fresh_dead"
check "old(24h-7d) + dead pid is swept"               gone   "$old_dead"
check "old(24h-7d) + live pid survives"               exists "$old_live"
check "ancient(>7d) + live pid swept regardless"      gone   "$ancient_live"

# ---- perf regression: find-call count must be CONSTANT, not O(files-in-dir) ----
i=0
while [ "$i" -lt 50 ]; do
  touch_at "$BULK/fake_launcher.sh.$((700000 + i)).sh" 5   # fresh; must survive without costing a find-per-file
  i=$((i + 1))
done
TRACE="$(mktemp)"
( set -x; sc_snapshot_sweep "$BULK" ) 2> "$TRACE"
find_calls="$(grep -c ' find ' "$TRACE" || true)"
ran=$((ran + 1))
if [ "$find_calls" -le 3 ]; then
  echo "OK   find-call count is constant ($find_calls calls for 50 files)"
else
  echo "FAIL find-call count scales with file count: $find_calls calls for 50 files"
  fail=1
fi
rm -f "$TRACE"

[ "$ran" -eq 5 ] || { echo "FAIL expected 5 cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_snapshot_sweep" || echo "FAIL test_sidecar_snapshot_sweep"
exit "$fail"
