#!/usr/bin/env bash
# Proof that the sidecar stall watchdog (lib/sidecar_common.sh sc_run_watched) BITES:
#   1. a child that emits nothing for longer than SC_STALL_SEC is killed, rc=9, and the progress
#      stream ends on a synthetic result event with terminal_reason "stall";
#   2. a child that keeps emitting is left alone (clean case, rc=0, no stall marker);
#   3. SC_STALL_SEC=0 disables the watch (a stalling child runs to its own end).
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
# Source only the function bodies: the lib has no top-level side effects beyond variable defaults.
# shellcheck source=../scripts/lib/sidecar_common.sh
. "$LIB"
SC_FORMAT="stream-json"
fail=0
say() { echo "$*"; }

stalling_child() { printf '{"type":"system","subtype":"init"}\n'; sleep 40; printf '{"type":"result","subtype":"success","result":"late"}\n'; }
talking_child()  { local i; for i in 1 2 3; do printf '{"type":"assistant","n":%s}\n' "$i"; sleep 1; done; printf '{"type":"result","subtype":"success","result":"done"}\n'; }

# --- case 1: planted stall fires
P="$(mktemp)"; : > "$P"
SC_STALL_SEC=12; SC_STALLED=0; OUTPUT=""; rc=""
t0=$(date +%s); sc_run_watched stalling_child "$P"; t1=$(date +%s)
if [ "$rc" = 9 ] && grep -q '"terminal_reason":"stall"' "$P" && printf '%s' "$OUTPUT" | grep -q '"terminal_reason":"stall"' && [ $((t1 - t0)) -lt 35 ]; then
  say "OK   stall: child killed after $((t1 - t0))s, rc=9, stall event in stream and OUTPUT"
else
  say "FAIL stall: rc=$rc elapsed=$((t1 - t0))s stream=$(tail -c 200 "$P")"; fail=1
fi
rm -f "$P"

# --- case 2: clean run untouched
P="$(mktemp)"; : > "$P"
SC_STALL_SEC=12; SC_STALLED=0; OUTPUT=""; rc=""
sc_run_watched talking_child "$P"
if [ "$rc" = 0 ] && [ "$SC_STALLED" = 0 ] && ! grep -q '"terminal_reason":"stall"' "$P" && printf '%s' "$OUTPUT" | grep -q '"result":"done"'; then
  say "OK   clean: rc=0, no stall marker, deliverable captured"
else
  say "FAIL clean: rc=$rc stalled=$SC_STALLED stream=$(tail -c 200 "$P")"; fail=1
fi
rm -f "$P"

# --- case 3: disabled watch lets a slow child finish (use a shorter sleeper to keep the suite fast)
slow_child() { printf '{"type":"system","subtype":"init"}\n'; sleep 3; printf '{"type":"result","subtype":"success","result":"slow-done"}\n'; }
P="$(mktemp)"; : > "$P"
SC_STALL_SEC=0; SC_STALLED=0; OUTPUT=""; rc=""
sc_run_watched slow_child "$P"
if [ "$rc" = 0 ] && printf '%s' "$OUTPUT" | grep -q 'slow-done'; then
  say "OK   disabled: SC_STALL_SEC=0 runs the child to its own end"
else
  say "FAIL disabled: rc=$rc output=$(printf '%s' "$OUTPUT" | tail -c 120)"; fail=1
fi
rm -f "$P"

exit $fail
