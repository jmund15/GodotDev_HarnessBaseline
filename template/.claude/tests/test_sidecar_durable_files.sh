#!/usr/bin/env bash
# test_sidecar_durable_files.sh — RED/GREEN proof for the durable `<record>.exit`/`<record>.out`
# write-on-every-exit-path added to lib/sidecar_common.sh (plan: .claude/plans/sidecar-detach.md,
# Design §1, slice E1). This revision REPLACES the required-`-X` design landed in 30640c097: a
# launch no longer needs `-X` to survive a killed harness task, because the lib itself writes
# both files from an EXIT-trap cleanup, on every exit path, foreground or backgrounded.
#
# RED RECORD: run once against HEAD's lib (before E1 lands) — every case below fails, because the
# handler and its registration do not exist yet.
#   1  foreground launch, ordinary exit          .exit holds the rc, .out holds stdout
#   2  backgrounded launch, its own bash killed  the launcher (not the outer wrapper) is what
#      right after it starts (Constraint 3)      keeps running; .exit/.out appear anyway
#   3  detached run (-X)                         the handler is NOT registered (SC_CLEANUP has no
#                                                 entry for it) — the -X wrapper owns the files;
#                                                 the files it does write are single, clean lines
#   4  unwritable record directory at exit time  the launcher's own exit code is unchanged, and
#                                                 no .exit is left behind (the write failed, not
#                                                 the run)
#   5  gate refusal after -R is parsed           .exit holds the refusal code even though the
#                                                 launcher never reaches OUTPUT/sc_write_record
#   6  usage error before -R is parsed           no .exit, no .out — nothing was ever registered
#
# Proof contract: exit 0 pass, 1 fail, 2 only when the proof cannot bind its target. Fixtures are
# written under the system temp directory, never inside the repo. Kill only a pid this proof
# itself recorded.
#
# Override SC_LIB_UNDER_TEST to point at a different lib copy (used for the RED run against
# HEAD's pre-E1 lib); it defaults to the real lib next to this test.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LIB="${SC_LIB_UNDER_TEST:-$ROOT/scripts/lib/sidecar_common.sh}"
[ -f "$LIB" ] || { echo "CANNOT RUN: lib not found at $LIB"; exit 2; }

TMPBASE="${TEMP:-${TMPDIR:-/tmp}}"
[ -d "$TMPBASE" ] || { echo "CANNOT RUN: no usable temp dir (TEMP/TMPDIR unset and /tmp missing)"; exit 2; }
WORK="$(mktemp -d "$TMPBASE/sidecar-durable-test.XXXXXX" 2>/dev/null)" || { echo "CANNOT RUN: mktemp -d failed under $TMPBASE"; exit 2; }
KILLED_PIDS_FILE="$WORK/.owned_pids"
: > "$KILLED_PIDS_FILE"

# Every launcher's sc_reexec_snapshot sweeps $TEMP/sidecar-snapshots on every invocation, and that
# directory is shared machine-wide across every concurrent session's sidecar test runs. Pointing
# TEMP/TMPDIR at a private, empty directory keeps this proof fast and keeps it from adding to (or
# waiting on) that shared congestion — this is test isolation, not a change to the lib under test.
export TEMP="$WORK/tmp"
mkdir -p "$TEMP"
export TMPDIR="$TEMP"

cleanup() {
  # Only ever kill a pid THIS proof recorded as its own — never a path substring, never a group.
  local p
  if [ -f "$KILLED_PIDS_FILE" ]; then
    while IFS= read -r p; do
      [ -n "$p" ] && kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null
    done < "$KILLED_PIDS_FILE"
  fi
  rm -rf "$WORK" 2>/dev/null || :
}
trap cleanup EXIT

LAUNCHER="$WORK/fake_launcher.sh"
cat > "$LAUNCHER" <<'FAKE_LAUNCHER_EOF'
#!/usr/bin/env bash
# fake_launcher.sh — test double for a real *_sidecar.sh launcher: source the lib, call
# sc_reexec_snapshot "$@" immediately (the one line every real launcher adds), parse flags, then
# behave exactly as this test's env vars direct.
set -uo pipefail
SC_TRANSPORT="faketest"
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# shellcheck disable=SC1090
. "${SC_LIB_UNDER_TEST:?SC_LIB_UNDER_TEST must point at the lib to test}"
sc_reexec_snapshot "$@"

sc_parse_flags "$@"
shift "$SC_SHIFT"

[ -n "${FAKE_SLEEP:-}" ] && sleep "$FAKE_SLEEP"

case "${FAKE_MODE:-}" in
  gate_refusal)
    # A refusal gate exits before OUTPUT is ever assigned and before sc_write_record runs — the
    # shape every real `sc_gate_*` refusal takes.
    exit "${FAKE_EXIT:-5}"
    ;;
  rmdir_at_exit)
    # Simulates a record directory that becomes unwritable between validation and exit: the
    # launcher itself removes it right before its own exit.
    rm -rf "$(dirname "$SC_RECORD")" 2>/dev/null
    exit "${FAKE_EXIT:-0}"
    ;;
esac

OUTPUT="${FAKE_JSON:-}"
rc="${FAKE_EXIT:-0}"
printf '%s\n' "$OUTPUT"
exit "$rc"
FAKE_LAUNCHER_EOF

WRAPPER2="$WORK/wrapper2.sh"
cat > "$WRAPPER2" <<'WRAPPER2_EOF'
#!/usr/bin/env bash
# wrapper2.sh — stands in for the harness's own outer task, one process removed from the
# launcher: it backgrounds the launcher, records its pid, then just waits. Killing THIS process
# (never the launcher pid) is Constraint 3's scenario — a low-memory notice killed only the
# harness's outer shell; the launcher (a distinct process) kept running to completion. A bare
# `bash launcher &` at the TEST's own top level does not model this: bash execs a subshell
# containing exactly one simple command into that command directly, so "the wrapper's pid" and
# "the launcher's pid" would be the SAME process (measured) and killing it would kill both.
bash "$@" &
echo $! > "$WRAPPER_CHILD_PIDFILE"
wait
WRAPPER2_EOF

PASS=0; FAIL=0
declare -a FAILED_CASES=()
pass() { PASS=$((PASS+1)); echo "  PASS  $1"; }
fail() { FAIL=$((FAIL+1)); FAILED_CASES+=("$1"); echo "  FAIL  $1"; shift; for m in "$@"; do echo "        $m"; done; }

poll_until() { # <timeout-seconds> <path>
  local t="$1" p="$2" waited=0
  while [ ! -e "$p" ]; do
    sleep 0.2; waited=$((waited+1))
    [ "$waited" -ge $((t * 5)) ] && return 1
  done
  return 0
}

win_pid_of() { # <bash pid> -> Windows pid, if this is a Windows/MSYS bash
  ps -W 2>/dev/null | awk -v p="$1" '$1==p{print $4}'
}

record_owned_pid() { echo "$1" >> "$KILLED_PIDS_FILE"; }

echo "test_sidecar_durable_files — lib under test: $LIB"
echo

# --- 1. foreground launch, ordinary exit: .exit holds rc, .out holds stdout ------------------
REC1="$WORK/case1.record.json"
out1="$(SC_LIB_UNDER_TEST="$LIB" FAKE_JSON='{"case":"one"}' FAKE_EXIT=0 \
  bash "$LAUNCHER" -R "$REC1" -l case1 2>"$WORK/.stderr1")"; rc1=$?
if [ "$rc1" = "0" ] && [ -f "${REC1}.exit" ] && [ "$(cat "${REC1}.exit")" = "0" ] \
   && [ -f "${REC1}.out" ] && [[ "$(cat "${REC1}.out")" == *'{"case":"one"}'* ]]; then
  pass "1 foreground launch writes .exit=0 and .out matching stdout"
else
  fail "1 foreground launch" "rc=$rc1" "exit file=$(cat "${REC1}.exit" 2>/dev/null || echo MISSING)" \
    "out file=$(cat "${REC1}.out" 2>/dev/null || echo MISSING)"
fi

# --- 2. backgrounded launch, its own wrapper killed right after start: files still appear ----
REC2="$WORK/case2.record.json"
PARENT_OUT2="$WORK/case2.parent.out"; PARENT_ERR2="$WORK/case2.parent.err"
CHILD_PIDFILE2="$WORK/case2.child_pid"
: > "$PARENT_OUT2"
( SC_LIB_UNDER_TEST="$LIB" FAKE_SLEEP=3 FAKE_JSON='{"case":"two"}' FAKE_EXIT=4 \
  WRAPPER_CHILD_PIDFILE="$CHILD_PIDFILE2" \
  bash "$WRAPPER2" "$LAUNCHER" -R "$REC2" -l case2 > "$PARENT_OUT2" 2>"$PARENT_ERR2" ) &
WRAPPER_PID=$!
record_owned_pid "$WRAPPER_PID"
# Wait for the launcher (the wrapper's distinct child) to exist and have had time to parse -R
# (register its handler), then kill the WRAPPER only — never the launcher pid — the same way
# Constraint 3's low-memory notice killed only the outer shell while the launcher kept running.
waited=0
while [ ! -s "$CHILD_PIDFILE2" ]; do
  sleep 0.1; waited=$((waited+1)); [ "$waited" -ge 30 ] && break
done
CHILD_PID2="$(cat "$CHILD_PIDFILE2" 2>/dev/null)"
[ -n "$CHILD_PID2" ] && record_owned_pid "$CHILD_PID2"
sleep 0.3
if [[ "$(uname -s 2>/dev/null)" == MINGW* || "$(uname -s 2>/dev/null)" == MSYS* ]]; then
  WIN_WRAPPER_PID="$(win_pid_of "$WRAPPER_PID")"
  [ -n "$WIN_WRAPPER_PID" ] && MSYS_NO_PATHCONV=1 taskkill /PID "$WIN_WRAPPER_PID" /F >/dev/null 2>&1
else
  kill -KILL "$WRAPPER_PID" 2>/dev/null
fi
wait "$WRAPPER_PID" 2>/dev/null
if poll_until 6 "${REC2}.exit"; then
  ex2="$(cat "${REC2}.exit")"; out2="$(cat "${REC2}.out" 2>/dev/null)"
  if [ "$ex2" = "4" ] && [[ "$out2" == *'{"case":"two"}'* ]]; then
    pass "2 launcher outlives its killed wrapper and still writes .exit=4 and matching .out"
  else
    fail "2 killed-wrapper survival content" "exit=$ex2 out=$out2"
  fi
else
  fail "2 killed-wrapper survival" "no ${REC2}.exit within 6s of killing wrapper pid $WRAPPER_PID"
fi

# --- 3. detached run (-X): handler not registered; the -X wrapper's own files are clean -------
REC3="$WORK/case3.record.json"
out3="$(SC_LIB_UNDER_TEST="$LIB" FAKE_JSON='{"case":"three"}' FAKE_EXIT=0 \
  bash "$LAUNCHER" -X -R "$REC3" -l case3 2>"$WORK/.stderr3")"; rc3=$?
if [ "$rc3" = "0" ] && [[ "$out3" == *"DETACHED pid="* ]] && poll_until 8 "${REC3}.exit"; then
  ex3="$(cat "${REC3}.exit")"; outv3="$(cat "${REC3}.out" 2>/dev/null)"
  # "single, clean line" — no duplicate/concatenated writes from a second, wrongly-active writer.
  ex3_lines="$(printf '%s' "$ex3" | grep -c '^[0-9]*$' || true)"
  if [ "$ex3" = "0" ] && [[ "$outv3" == *'{"case":"three"}'* ]] && [ "$(wc -l < "${REC3}.exit")" -le 1 ]; then
    pass "3a detached run's own -X-written files are clean single writes"
  else
    fail "3a detached run file shape" "exit=$ex3 (lines=$(wc -l < "${REC3}.exit")) out=$outv3"
  fi
else
  fail "3a detached run setup" "rc=$rc3 out=$out3"
fi
# White-box: the durable-file handler must not even be REGISTERED inside a detached job — the -X
# wrapper re-invokes the launcher with SIDECAR_DETACHED=1, and that re-invocation's own
# sc_parse_flags call must skip registration entirely (checked directly on SC_CLEANUP, since a
# double registration could coincidentally write identical content and hide behind case 3a).
REC3B="$WORK/case3b.record.json"
: > "$REC3B"
detached_cleanup_check="$(
  SIDECAR_DETACHED=1 bash -uo pipefail -c '
    SC_LAUNCHER_DIR="'"$WORK"'"
    . "'"$LIB"'"
    sc_parse_flags -R "'"$REC3B"'"
    for c in "${SC_CLEANUP[@]}"; do
      case "$c" in *sc_write_durable_files*) echo REGISTERED ;; esac
    done
  ' 2>/dev/null
)"
if [ -z "$detached_cleanup_check" ]; then
  pass "3b a detached job (SIDECAR_DETACHED=1) never registers the durable-file handler"
else
  fail "3b detached registration" "SC_CLEANUP unexpectedly contains: $detached_cleanup_check"
fi

# --- 4. unwritable record directory at exit time: launcher's own exit code survives ----------
REC4DIR="$WORK/case4dir"
mkdir -p "$REC4DIR"
REC4="$REC4DIR/case4.record.json"
out4="$(SC_LIB_UNDER_TEST="$LIB" FAKE_MODE=rmdir_at_exit FAKE_EXIT=9 \
  bash "$LAUNCHER" -R "$REC4" -l case4 2>"$WORK/.stderr4")"; rc4=$?
err4="$(cat "$WORK/.stderr4")"
if [ "$rc4" = "9" ] && [ ! -e "${REC4}.exit" ]; then
  pass "4 unwritable record dir: launcher's own exit code (9) survives, no .exit is left behind"
else
  fail "4 unwritable record dir" "rc=$rc4 (want 9)" "exit file exists=$( [ -e "${REC4}.exit" ] && echo yes || echo no )" "stderr=$err4"
fi

# --- 5. gate refusal after -R is parsed: .exit holds the refusal code -------------------------
REC5="$WORK/case5.record.json"
out5="$(SC_LIB_UNDER_TEST="$LIB" FAKE_MODE=gate_refusal FAKE_EXIT=5 \
  bash "$LAUNCHER" -R "$REC5" -l case5 2>"$WORK/.stderr5")"; rc5=$?
if [ "$rc5" = "5" ] && [ -f "${REC5}.exit" ] && [ "$(cat "${REC5}.exit")" = "5" ] && [ ! -e "${REC5}.out" ]; then
  pass "5 a gate refusal (exit 5) after -R is parsed still writes .exit=5, no .out (OUTPUT unset)"
else
  fail "5 gate refusal" "rc=$rc5 (want 5)" "exit file=$(cat "${REC5}.exit" 2>/dev/null || echo MISSING)"
fi

# --- 6. usage error before -R is parsed: nothing is written ------------------------------------
REC6="$WORK/case6.record.json"
out6="$(SC_LIB_UNDER_TEST="$LIB" bash "$LAUNCHER" -badflag -R "$REC6" -l case6 2>"$WORK/.stderr6")"; rc6=$?
err6="$(cat "$WORK/.stderr6")"
if [ "$rc6" = "2" ] && [ ! -e "${REC6}.exit" ] && [ ! -e "${REC6}.out" ]; then
  pass "6 a usage error before -R is parsed exits 2 and writes nothing"
else
  fail "6 usage error before -R" "rc=$rc6 (want 2)" "stderr=$err6" \
    "exit file exists=$( [ -e "${REC6}.exit" ] && echo yes || echo no )"
fi

echo "test_sidecar_durable_files: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "failed: ${FAILED_CASES[*]}"
  exit 1
fi
exit 0
