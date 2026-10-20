#!/usr/bin/env bash
# Proof that the sidecar stall watchdog (lib/sidecar_common.sh sc_run_watched) SIGNALS and never kills:
#   1. a child silent for longer than SC_STALL_SEC gets <record>.stall and is still alive; the next
#      work event deletes the marker and the run reaches its own end with rc=0 (never 9);
#   2. SC_STALL_SEC=0 writes no marker at all;
#   3. SIDECAR_TIMEOUT unset leaves the wall-clock kill OFF (SC_TIMEOUT empty); the opt-in still reaches
#      the launcher's `timeout` guard — the kill itself is proved end-to-end by
#      test_opencode_run_sidecar.py::test_killed_run_writes_durable_record (SIDECAR_TIMEOUT=2, rc 124).
#   4. `-R` without `-P` defaults the progress stream to <record>.stream so the watchdog runs; an
#      explicit `-P` is kept and neither flag keeps the unwatched run.
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
# Source only the function bodies: the lib has no top-level side effects beyond variable defaults.
# shellcheck source=../scripts/lib/sidecar_common.sh
. "$LIB"
sc_check_launch_memory() { :; }
SC_FORMAT="stream-json"
fail=0
say() { echo "$*"; }

# init is not a work event; the assistant line is, so the marker must appear and then be deleted.
stalling_child() { printf '{"type":"system","subtype":"init"}\n'; sleep 6; printf '{"type":"assistant","n":1}\n'; sleep 1; printf '{"type":"result","subtype":"success","result":"recovered"}\n'; }

# --- case 1: the stall writes a marker, kills nothing, and the next work event clears it
d="$(mktemp -d)"; P="$d/progress.jsonl"; : > "$P"
REC="$d/stall.record.json"
rcfile="$d/rc"; outfile="$d/out"
(
  SC_RECORD="$REC" SC_STALL_SEC=3 SC_WATCH_POLL_SEC=1 OUTPUT="" rc=""
  sc_run_watched stalling_child "$P"
  printf '%s' "$rc" > "$rcfile"; printf '%s' "$OUTPUT" > "$outfile"
) &
bg=$!
alive_when_marked=0; waited=0
while [ "$waited" -lt 12 ]; do
  if [ -f "$REC.stall" ]; then
    kill -0 "$bg" 2>/dev/null && alive_when_marked=1
    break
  fi
  sleep 1; waited=$((waited + 1))
done
if wait "$bg" 2>/dev/null; then :; fi
rc="$(cat "$rcfile" 2>/dev/null)"
if [ "$alive_when_marked" = 1 ] && [ "$rc" = 0 ] && [ ! -f "$REC.stall" ] \
   && grep -q 'recovered' "$outfile" && ! grep -q '"terminal_reason":"stall"' "$P"; then
  say "OK   stall signals: marker seen at ${waited}s with the child alive, deleted by the next work event, rc=0"
else
  say "FAIL stall: marked=$alive_when_marked waited=${waited}s rc=${rc:-none} marker_now=$( [ -f "$REC.stall" ] && echo present || echo gone ) out=$(tail -c 120 "$outfile" 2>/dev/null)"
  [ -f "$REC.stall" ] && say "     marker: $(cat "$REC.stall")"
  fail=1
fi

# --- case 2: SC_STALL_SEC=0 disables the signal entirely
P2="$d/progress0.jsonl"; : > "$P2"
REC2="$d/off.record.json"
RC2="$d/rc0" OUT2="$d/out0"
(
  SC_RECORD="$REC2" SC_STALL_SEC=0 SC_WATCH_POLL_SEC=1 OUTPUT="" rc=""
  sc_run_watched stalling_child "$P2"
  printf '%s' "$rc" > "$RC2"; printf '%s' "$OUTPUT" > "$OUT2"
) &
bg2=$!
sleep 5
if [ -f "$REC2.stall" ]; then
  say "FAIL disabled: SC_STALL_SEC=0 still wrote a marker"; fail=1
  kill "$bg2" 2>/dev/null
else
  wait "$bg2" 2>/dev/null
  if [ "$(cat "$RC2" 2>/dev/null)" = 0 ] && grep -q 'recovered' "$OUT2"; then
    say "OK   disabled: SC_STALL_SEC=0 writes no marker and runs the child to its own end"
  else
    say "FAIL disabled: rc=$(cat "$RC2" 2>/dev/null)"; fail=1
  fi
fi

# --- case 3: the wall-clock kill is OFF unless a caller asks for it
default="$(unset SIDECAR_TIMEOUT; . "$LIB"; printf '%s' "${SC_TIMEOUT-UNSET}")"
opted="$(SIDECAR_TIMEOUT=1800; . "$LIB"; printf '%s' "$SC_TIMEOUT")"
if [ -z "$default" ] && [ "$opted" = 1800 ]; then
  say "OK   timeout: default is OFF (SC_TIMEOUT empty); SIDECAR_TIMEOUT/-T still reaches the launcher's kill guard"
else
  say "FAIL timeout: default='${default}' (want empty) opted='${opted}' (want 1800)"; fail=1
fi

# --- case 4: -R alone watches <record>.stream, and so does -R with -o json (which converts back at the end); an explicit -P and the no-flag run are unchanged
parsed() {  # $1.. launcher flags -> "<SC_PROGRESS>|<SC_FORMAT>"
  ( sc_check_duplicate_launch() { :; }
    sc_parse_flags "$@" >/dev/null 2>&1; printf '%s|%s' "$SC_PROGRESS" "$SC_FORMAT"; [ -z "$SC_CALLER_JSON" ] || printf '|json' )
}
REC4="$d/r4.json"
got_r="$(parsed -R "$REC4" prompt)"
got_p="$(parsed -R "$REC4" -P "$d/explicit.stream" prompt)"
got_n="$(parsed prompt)"
got_o="$(parsed -R "$REC4" -o json prompt)"
if [ "$got_r" = "$REC4.stream|stream-json" ] && [ "$got_p" = "$d/explicit.stream|stream-json" ]    && [ "${got_n%%|*}" = "" ] && [ "$got_o" = "$REC4.stream|stream-json|json" ]; then
  say "OK   -R defaults -P to <record>.stream, with -o json too; explicit -P kept; no flags leave it empty"
else
  say "FAIL progress default: -R='$got_r' -R+-P='$got_p' -R+-o='$got_o' none='$got_n'"; fail=1
fi
P4="${got_r%%|*}"; : > "$P4"
short_stall_child() { printf '{"type":"system","subtype":"init"}
'; sleep 5; printf '{"type":"assistant","n":1}
'; printf '{"type":"result","subtype":"success","result":"done4"}
'; }
err4="$d/err4"; seen4=0
(
  SC_RECORD="$REC4" SC_STALL_SEC=2 SC_WATCH_POLL_SEC=1 OUTPUT="" rc=""
  sc_run_watched short_stall_child "$P4" 2> "$err4"
) &
bg4=$!
for _ in 1 2 3 4 5 6 7 8; do
  if [ -f "$REC4.stall" ]; then seen4=1; break; fi
  sleep 1
done
wait "$bg4" 2>/dev/null || :
if [ "$seen4" = 1 ] && ! grep -q 'watchdog is OFF' "$err4"; then
  say "OK   a -R run with no -P is watched: the stall marker appears and no OFF notice prints"
else
  say "FAIL -R without -P unwatched: marker_seen=$seen4 stderr=$(cat "$err4" 2>/dev/null)"; fail=1
fi

rm -rf "$d"
exit $fail
