#!/usr/bin/env bash
# test_sidecar_detach.sh — RED/GREEN proof for the -X foreground->background detach path added
# to sc_reexec_snapshot() in lib/sidecar_common.sh (plan: .claude/plans/sidecar-detach.md, D1).
#
# RED RECORD (run once against HEAD's lib before implementing; see d1_report.md for the full
# transcript). Controls are cases that ALREADY pass against the pre-D1 lib because they never
# touch the new -X trigger at all — a case is a genuine red/green distinguisher only when it
# fails old and passes new.
#   1  -X, no -R                                 RED  (old: exit 2, generic "bad usage" message,
#                                                       not "-X needs -R" — message asserted)
#   2  -AX -R <rec> (malformed cluster)           RED  (old: exit 2, generic message, not
#                                                       "-X must be a standalone argument")
#   3  -l -X -R <rec> (X is -l's VALUE)          CONTROL (old lib has no -X awareness at all,
#                                                       so -l simply consumes the next token
#                                                       exactly as it does today)
#   4  -X -R <rec>, unwritable snapshot dir       RED  (old: exit 2 via invalid option, not the
#                                                       required exit 1 "cannot snapshot")
#   5  -A -X -R <rec> -P <prog> -l <label>        RED  (old: exit 2, no DETACHED lines at all)
#   6  sleeping/exiting detached child             RED  (old: never spawns; no .exit ever appears)
#   7  live snapshot pid while child runs          RED  (old: no snapshot claim/rename exists)
#   8  parent killed right after the launch returns RED (old: nothing survives — there is
#                                                       nothing to survive)
#   9  reused record (.exit pre-exists)            RED  (old: exit 2 via invalid option, not the
#                                                       required exit 1; files untouched by
#                                                       accident, not by the reuse guard)
#  10  two concurrent -X launches, one record      RED  (old: zero spawns, not exactly one)
#  11  resume through the detach path              RED  (old: never spawns; sc_resume_loop never
#                                                       runs inside a detached job)
#  12  no -X, ordinary foreground dispatch         CONTROL (path is untouched by this change)
#  13  every lib launcher calls sc_reexec_snapshot CONTROL until opencode_sidecar.sh is edited,
#      "$@" right after sourcing the lib                then RED->GREEN on that one file
#  14  detached launch releases the caller's pipe RED  (D1 first build: a 12 s child held
#                                                       $(...) for the whole run, because the
#                                                       job subshell kept fd 1 and 2)
#
# Proof contract: exit 0 pass, 1 fail, 2 only when the proof cannot bind its target (lib/launcher
# missing, scratch dir uncreatable). Fixtures and the fake launcher are written under the system
# temp directory at run time, never inside the repo. Kill only a pid this proof itself recorded.
#
# Override SC_LIB_UNDER_TEST to point at a different lib copy (used for the RED run against
# HEAD's pre-D1 lib); it defaults to the real lib next to this test.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LIB="${SC_LIB_UNDER_TEST:-$ROOT/scripts/lib/sidecar_common.sh}"
[ -f "$LIB" ] || { echo "CANNOT RUN: lib not found at $LIB"; exit 2; }

TMPBASE="${TEMP:-${TMPDIR:-/tmp}}"
[ -d "$TMPBASE" ] || { echo "CANNOT RUN: no usable temp dir (TEMP/TMPDIR unset and /tmp missing)"; exit 2; }
WORK="$(mktemp -d "$TMPBASE/sidecar-detach-test.XXXXXX" 2>/dev/null)" || { echo "CANNOT RUN: mktemp -d failed under $TMPBASE"; exit 2; }
KILLED_PIDS_FILE="$WORK/.owned_pids"
: > "$KILLED_PIDS_FILE"

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
# fake_launcher.sh — D1 test double for a real *_sidecar.sh launcher: source the lib, call
# sc_reexec_snapshot "$@" immediately (the one line every real launcher adds), then behave
# exactly as test_sidecar_detach.sh's env vars direct.
set -uo pipefail
SC_TRANSPORT="faketest"
# Every real launcher sets this before sourcing (anthropic_sidecar.sh:51 etc.) — the lib's
# snapshot re-exec passes it through, and an unset value is an unbound-variable crash under -u.
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# shellcheck disable=SC1090
. "${SC_LIB_UNDER_TEST:?SC_LIB_UNDER_TEST must point at the lib to test}"
sc_reexec_snapshot "$@"

sc_parse_flags "$@"
shift "$SC_SHIFT"

if [ "${FAKE_MODE:-}" = "resume" ]; then
  : "${FAKE_CNT:?FAKE_CNT required for FAKE_MODE=resume}"
  : "${FAKE_FIXTURE:?FAKE_FIXTURE required for FAKE_MODE=resume}"
  SC_FORMAT=stream-json
  EXTRA_ARGS=()
  run_fn() {
    if [ ! -s "$FAKE_CNT" ]; then
      echo x >> "$FAKE_CNT"
      cat "$FAKE_FIXTURE"
    else
      printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}' \
        '{"type":"result","subtype":"success","num_turns":9,"terminal_reason":"completed","session_id":"sid-resumed","result":"[{\"finding\":\"real\"}]"}'
    fi
  }
  OUTPUT="$(run_fn)"; rc=0
  sc_resume_loop run_fn ""
  printf '%s\n' "$OUTPUT"
  exit "${rc:-0}"
fi

echo "PARSED_MODEL=$SC_MODEL"
echo "PARSED_AUTHORIZED=$SC_AUTHORIZED"
echo "PARSED_LABEL=$SC_LABEL"
echo "PARSED_RECORD=$SC_RECORD"
echo "PARSED_PROGRESS=$SC_PROGRESS"
echo "PARSED_REST=$*"

[ -n "${FAKE_SLEEP:-}" ] && sleep "$FAKE_SLEEP"
[ -n "${FAKE_JSON:-}" ] && printf '%s\n' "$FAKE_JSON"
exit "${FAKE_EXIT:-0}"
FAKE_LAUNCHER_EOF

# The resume fixture is EXECUTED, not retyped, from the existing resume proof's own compaction
# ending — .claude/tests/sidecar_resume_loop_test.sh, function summary_ending() (its three
# printf'd JSON lines, cited in d1_report.md by line number). The function's exact source lines
# are sliced out and sourced, then called, so the fixture text can never drift from what that
# proof actually exercises.
RESUME_FIXTURE_SRC="$HERE/sidecar_resume_loop_test.sh"
FAKE_FIXTURE="$WORK/compaction_fixture.jsonl"
if [ -f "$RESUME_FIXTURE_SRC" ]; then
  start_ln="$(grep -n '^summary_ending() {' "$RESUME_FIXTURE_SRC" | head -1 | cut -d: -f1)"
  if [ -n "$start_ln" ]; then
    end_ln="$(awk -v s="$start_ln" 'NR>=s && /^}/{print NR; exit}' "$RESUME_FIXTURE_SRC")"
    if [ -n "$end_ln" ]; then
      sed -n "${start_ln},${end_ln}p" "$RESUME_FIXTURE_SRC" > "$WORK/.summary_ending_fn.sh"
      # shellcheck disable=SC1090
      ( source "$WORK/.summary_ending_fn.sh"; summary_ending ) > "$FAKE_FIXTURE" 2>/dev/null || true
    fi
  fi
fi
if [ ! -s "$FAKE_FIXTURE" ]; then
  echo "CANNOT RUN: could not extract the compaction fixture from $RESUME_FIXTURE_SRC (function summary_ending)"
  exit 2
fi

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

run_launcher() { # runs the fake launcher with the given args, foreground, capturing stdout/stderr/rc
  local outv="$1" errv="$2" rcv="$3"; shift 3
  local o e r
  o="$(SC_LIB_UNDER_TEST="$LIB" bash "$LAUNCHER" "$@" 2>"$WORK/.stderr_tmp")"; r=$?
  e="$(cat "$WORK/.stderr_tmp")"
  printf -v "$outv" '%s' "$o"
  printf -v "$errv" '%s' "$e"
  printf -v "$rcv" '%s' "$r"
}

echo "test_sidecar_detach — lib under test: $LIB"
echo

# --- 1. -X without -R: exit 2, spawns nothing -------------------------------------------------
REC1="$WORK/case1.record.json"
run_launcher out1 err1 rc1 -X
if [ "$rc1" = "2" ] && [[ "$err1" == *"-X needs -R"* ]] && [ ! -e "${REC1}.pid" ]; then
  pass "1 -X without -R exits 2 with '-X needs -R', spawns nothing"
else
  fail "1 -X without -R" "rc=$rc1 (want 2)" "stderr=$err1 (want '-X needs -R')"
fi

# --- 2. -AX -R <rec>: malformed cluster, exit 2, spawns nothing -------------------------------
REC2="$WORK/case2.record.json"
run_launcher out2 err2 rc2 -AX -R "$REC2"
if [ "$rc2" = "2" ] && [[ "$err2" == *"-X must be a standalone argument"* ]] && [ ! -e "${REC2}.pid" ]; then
  pass "2 -AX -R <rec> exits 2 with '-X must be a standalone argument', spawns nothing"
else
  fail "2 -AX -R <rec>" "rc=$rc2 (want 2)" "stderr=$err2 (want '-X must be a standalone argument')" "pid file exists=$( [ -e "${REC2}.pid" ] && echo yes || echo no )"
fi

# --- 3. -l -X -R <rec>: -X is -l's VALUE, runs in the foreground, no detach files -------------
REC3="$WORK/case3.record.json"
run_launcher out3 err3 rc3 -l -X -R "$REC3"
if [ "$rc3" = "0" ] && [[ "$out3" == *"PARSED_LABEL=-X"* ]] && [ ! -e "${REC3}.pid" ] && [ ! -e "${REC3}.out" ]; then
  pass "3 -l -X -R <rec> treats -X as -l's value, foreground, no detach files (CONTROL)"
else
  fail "3 -l -X -R <rec>" "rc=$rc3 (want 0)" "stdout=$out3" "pid file exists=$( [ -e "${REC3}.pid" ] && echo yes || echo no )"
fi

# --- 4. -X -R <rec>, unwritable snapshot dir: exit 1, spawns nothing --------------------------
REC4="$WORK/case4.record.json"
BADTEMP="$WORK/case4.not_a_dir"
: > "$BADTEMP"   # a FILE, so mkdir -p "$BADTEMP/sidecar-snapshots" fails
out4="$(TEMP="$BADTEMP" SC_LIB_UNDER_TEST="$LIB" bash "$LAUNCHER" -X -R "$REC4" 2>"$WORK/.stderr_tmp")"; rc4=$?
err4="$(cat "$WORK/.stderr_tmp")"
if [ "$rc4" = "1" ] && [[ "$err4" == *"cannot snapshot; detach refused"* ]] && [ ! -e "${REC4}.out" ]; then
  pass "4 unwritable snapshot dir exits 1 'cannot snapshot; detach refused', spawns nothing"
else
  fail "4 unwritable snapshot dir" "rc=$rc4 (want 1)" "stderr=$err4"
fi

# --- 5. -A -X -R <rec> -P <prog> -l <label>: returns fast, prints paths, fields match ---------
REC5="$WORK/case5.record.json"
PROG5="$WORK/case5.progress"
T0=$(date +%s)
run_launcher out5 err5 rc5 -A -X -R "$REC5" -P "$PROG5" -l case5label
T1=$(date +%s)
ELAPSED5=$((T1 - T0))
if [ "$rc5" = "0" ] && [ "$ELAPSED5" -lt 5 ] \
   && [[ "$out5" == *"DETACHED pid="* ]] && [[ "$out5" == *"OUT=${REC5}.out"* ]] \
   && [[ "$out5" == *"ERR=${REC5}.err"* ]] && [[ "$out5" == *"EXIT=${REC5}.exit"* ]] \
   && [[ "$out5" == *"WAIT="* ]]; then
  if poll_until 5 "${REC5}.exit"; then
    child_out5="$(cat "${REC5}.out" 2>/dev/null)"
    if [[ "$child_out5" == *"PARSED_AUTHORIZED=1"* ]] && [[ "$child_out5" == *"PARSED_LABEL=case5label"* ]] \
       && [[ "$child_out5" == *"PARSED_RECORD=$REC5"* ]] && [[ "$child_out5" == *"PARSED_PROGRESS=$PROG5"* ]]; then
      pass "5 detach returns in <5s, prints the 4 paths, child's echoed fields match every flag"
    else
      fail "5 detach child fields" "out=$child_out5"
    fi
  else
    fail "5 detach child never wrote ${REC5}.exit"
  fi
else
  fail "5 -A -X -R <rec> -P <prog> -l <label>" "rc=$rc5 elapsed=${ELAPSED5}s" "stdout=$out5"
fi

# --- 6/7. sleeping child: exit code + JSON captured, and the live snapshot pid is real --------
REC67="$WORK/case67.record.json"
OUT67F="$WORK/case67.parent.out"; ERR67F="$WORK/case67.parent.err"
: > "$OUT67F"
( SC_LIB_UNDER_TEST="$LIB" FAKE_SLEEP=4 FAKE_JSON='{"case":"six-seven"}' FAKE_EXIT=3 \
  bash "$LAUNCHER" -A -X -R "$REC67" -l case67 >"$OUT67F" 2>"$ERR67F" ) &
PARENT67_PID=$!
record_owned_pid "$PARENT67_PID"
wait "$PARENT67_PID" 2>/dev/null
out67="$(cat "$OUT67F")"
if [[ "$out67" != *"DETACHED pid="* ]]; then
  fail "6/7 setup: detach launch itself failed" "out=$out67 err=$(cat "$ERR67F")"
else
  CHILD_PID="$(printf '%s\n' "$out67" | sed -n 's/^DETACHED pid=//p')"
  SNAPDIR="${TEMP:-${TMPDIR:-/tmp}}/sidecar-snapshots"
  CLAIM_FILE="$SNAPDIR/$(basename "$LAUNCHER").claim-${PARENT67_PID}.sh"
  LIVE_SNAP="$SNAPDIR/$(basename "$LAUNCHER").${CHILD_PID}.sh"
  sleep 1   # let the job rename its claim before looking — case 7 checks it WHILE still sleeping
  if [ -e "$LIVE_SNAP" ] && kill -0 "$CHILD_PID" 2>/dev/null && [ ! -e "$CLAIM_FILE" ]; then
    pass "7 live snapshot names the running job's pid (kill -0 OK), and its claim copy is gone"
  else
    fail "7 live snapshot pid" \
      "live_snap_exists=$([ -e "$LIVE_SNAP" ] && echo yes || echo no)" \
      "kill0=$(kill -0 "$CHILD_PID" 2>/dev/null && echo alive || echo dead)" \
      "claim_left=$([ -e "$CLAIM_FILE" ] && echo yes || echo no)"
  fi
  if poll_until 8 "${REC67}.exit"; then
    ex="$(cat "${REC67}.exit")"; outj="$(cat "${REC67}.out" 2>/dev/null)"
    if [ "$ex" = "3" ] && [[ "$outj" == *'{"case":"six-seven"}'* ]]; then
      pass "6 detached child: exit file holds 3, out file holds the JSON line"
    else
      fail "6 detached child result" "exit=$ex out=$outj"
    fi
  else
    fail "6 detached child never finished (no ${REC67}.exit within 8s)"
  fi
fi

# --- 8. parent killed right after the launch returns: exit file still appears ----------------
REC8="$WORK/case8.record.json"
PARENT_OUT8="$WORK/case8.parent.out"
: > "$PARENT_OUT8"
( SC_LIB_UNDER_TEST="$LIB" FAKE_SLEEP=2 FAKE_EXIT=0 FAKE_JSON='{"case":"eight"}' \
  bash "$LAUNCHER" -A -X -R "$REC8" -l case8 > "$PARENT_OUT8" 2>"$WORK/case8.parent.err" ) &
PARENT_PID=$!
record_owned_pid "$PARENT_PID"
# Wait for the parent to have printed DETACHED (it exits ~immediately after), then kill it -
# whether it is still alive at that instant or has already exited on its own is the point: the
# detached job (already forked, still 2s from finishing) must survive either way.
waited=0
while ! grep -q "^DETACHED pid=" "$PARENT_OUT8" 2>/dev/null; do
  sleep 0.1; waited=$((waited+1))
  [ "$waited" -ge 30 ] && break
done
if [[ "$(uname -s 2>/dev/null)" == MINGW* || "$(uname -s 2>/dev/null)" == MSYS* ]]; then
  WIN_PARENT_PID="$(win_pid_of "$PARENT_PID")"
  if [ -n "$WIN_PARENT_PID" ]; then
    MSYS_NO_PATHCONV=1 taskkill /PID "$WIN_PARENT_PID" /T /F >/dev/null 2>&1
  fi
else
  kill -KILL "$PARENT_PID" 2>/dev/null
fi
wait "$PARENT_PID" 2>/dev/null
if poll_until 6 "${REC8}.exit"; then
  pass "8 the detached job's exit file appears even though the parent was killed right after launch"
else
  fail "8 parent-killed survival" "no ${REC8}.exit within 6s of killing pid $PARENT_PID"
fi

# --- 9. reused record (.exit pre-exists): exit 1, every existing file byte-identical ----------
REC9="$WORK/case9.record.json"
printf 'PRE-EXISTING\n' > "${REC9}.exit"
SUM_BEFORE="$(sha1sum "${REC9}.exit" 2>/dev/null || cksum "${REC9}.exit")"
out9="$(SC_LIB_UNDER_TEST="$LIB" bash "$LAUNCHER" -A -X -R "$REC9" -l case9 2>"$WORK/.stderr_tmp")"; rc9=$?
err9="$(cat "$WORK/.stderr_tmp")"
SUM_AFTER="$(sha1sum "${REC9}.exit" 2>/dev/null || cksum "${REC9}.exit")"
if [ "$rc9" = "1" ] && [[ "$err9" == *"record path already used"* ]] && [ "$SUM_BEFORE" = "$SUM_AFTER" ] \
   && [ ! -e "${REC9}.out" ] && [ ! -e "${REC9}.pid" ]; then
  pass "9 reused record (.exit pre-exists) exits 1, files left byte-identical"
else
  fail "9 reused record" "rc=$rc9 (want 1)" "stderr=$err9" "before=$SUM_BEFORE after=$SUM_AFTER"
fi

# --- 10. two concurrent -X launches, one new record: exactly one spawns ----------------------
REC10="$WORK/case10.record.json"
( SC_LIB_UNDER_TEST="$LIB" bash "$LAUNCHER" -A -X -R "$REC10" -l case10a >"$WORK/case10a.out" 2>"$WORK/case10a.err"; echo $? > "$WORK/case10a.rc" ) &
P10A=$!
( SC_LIB_UNDER_TEST="$LIB" bash "$LAUNCHER" -A -X -R "$REC10" -l case10b >"$WORK/case10b.out" 2>"$WORK/case10b.err"; echo $? > "$WORK/case10b.rc" ) &
P10B=$!
record_owned_pid "$P10A"; record_owned_pid "$P10B"
wait "$P10A" 2>/dev/null; wait "$P10B" 2>/dev/null
RC10A="$(cat "$WORK/case10a.rc" 2>/dev/null)"; RC10B="$(cat "$WORK/case10b.rc" 2>/dev/null)"
OUT10A="$(cat "$WORK/case10a.out" 2>/dev/null)"; OUT10B="$(cat "$WORK/case10b.out" 2>/dev/null)"
DETACHED_COUNT=0
[[ "$OUT10A" == *"DETACHED pid="* ]] && DETACHED_COUNT=$((DETACHED_COUNT+1))
[[ "$OUT10B" == *"DETACHED pid="* ]] && DETACHED_COUNT=$((DETACHED_COUNT+1))
REFUSED_COUNT=0
[ "$RC10A" = "1" ] && REFUSED_COUNT=$((REFUSED_COUNT+1))
[ "$RC10B" = "1" ] && REFUSED_COUNT=$((REFUSED_COUNT+1))
if [ "$DETACHED_COUNT" = "1" ] && [ "$REFUSED_COUNT" = "1" ]; then
  pass "10 two concurrent -X launches on one record: exactly one spawns, the other exits 1"
else
  fail "10 concurrent claim" "detached_count=$DETACHED_COUNT refused_count=$REFUSED_COUNT" "10a: rc=$RC10A out=$OUT10A" "10b: rc=$RC10B out=$OUT10B"
fi

# --- 11. resume through the detach path -------------------------------------------------------
REC11="$WORK/case11.record.json"
CNT11="$WORK/case11.cnt"
: > "$CNT11"
out11="$(SC_LIB_UNDER_TEST="$LIB" FAKE_MODE=resume FAKE_CNT="$CNT11" FAKE_FIXTURE="$FAKE_FIXTURE" \
  bash "$LAUNCHER" -A -X -R "$REC11" -l case11 2>"$WORK/.stderr_tmp")"; rc11=$?
if [[ "$out11" == *"DETACHED pid="* ]] && poll_until 8 "${REC11}.exit"; then
  ex11="$(cat "${REC11}.exit")"; child_err11="$(cat "${REC11}.err" 2>/dev/null)"; child_out11="$(cat "${REC11}.out" 2>/dev/null)"
  if [ "$ex11" = "0" ] && [[ "$child_err11" == *"resuming session sid-compacted"* ]] && [[ "$child_out11" == *"sid-resumed"* ]]; then
    pass "11 detached run resumes once through sc_resume_loop and exits 0"
  else
    fail "11 resume through detach" "exit=$ex11" "child stderr=$child_err11" "child stdout=$child_out11"
  fi
else
  fail "11 resume through detach setup" "rc=$rc11 out=$out11"
fi

# --- 12. no -X: returns the child's exit code in the foreground, unchanged (CONTROL) ---------
REC12="$WORK/case12.record.json"
out12="$(SC_LIB_UNDER_TEST="$LIB" FAKE_EXIT=7 bash "$LAUNCHER" -R "$REC12" -l case12 2>"$WORK/.stderr_tmp")"; rc12=$?
if [ "$rc12" = "7" ] && [[ "$out12" == *"PARSED_RECORD=$REC12"* ]] && [ ! -e "${REC12}.pid" ]; then
  pass "12 no -X returns the child's exit code (7) unchanged, no detach files (CONTROL)"
else
  fail "12 no -X foreground" "rc=$rc12 (want 7)" "out=$out12"
fi

# --- 13. every lib launcher calls sc_reexec_snapshot "$@" right after sourcing the lib --------
SCRIPTS_DIR="$ROOT/scripts"
CASE13_OK=1
CASE13_DETAIL=()
for s in "$SCRIPTS_DIR"/*.sh; do
  [ -f "$s" ] || continue
  grep -q 'lib/sidecar_common\.sh"' "$s" || continue
  src_line="$(grep -n 'lib/sidecar_common\.sh"' "$s" | tail -1 | cut -d: -f1)"
  next_line=""
  ln=$((src_line + 1))
  total="$(wc -l < "$s")"
  while [ "$ln" -le "$total" ]; do
    text="$(sed -n "${ln}p" "$s")"
    trimmed="$(printf '%s' "$text" | sed 's/^[[:space:]]*//')"
    case "$trimmed" in
      ""|"#"*) ln=$((ln+1)); continue ;;
      "set "*) ln=$((ln+1)); continue ;;
      [A-Za-z_]*=*) ln=$((ln+1)); continue ;;
      *) next_line="$trimmed"; break ;;
    esac
  done
  case "$next_line" in
    'sc_reexec_snapshot "$@"'*) : ;;
    *) CASE13_OK=0; CASE13_DETAIL+=("$s: next substantive line after sourcing the lib was: $next_line") ;;
  esac
done
if [ "$CASE13_OK" = 1 ]; then
  pass "13 every *_sidecar.sh sourcing the lib calls sc_reexec_snapshot \"\$@\" immediately after"
else
  fail "13 sc_reexec_snapshot placement" "${CASE13_DETAIL[@]}"
fi

echo
# --- 14. a detached launch releases the caller's output pipe ----------------------------------
REC14="$WORK/case14.record.json"
c14_start=$(date +%s)
FAKE_SLEEP=12 run_launcher out14 err14 rc14 -X -R "$REC14"
c14_elapsed=$(( $(date +%s) - c14_start ))
c14_pid="$(printf '%s\n' "$out14" | sed -n 's/^DETACHED pid=//p')"
[ -n "$c14_pid" ] && record_owned_pid "$c14_pid"
if [ "$rc14" = "0" ] && [ -n "$c14_pid" ] && [ "$c14_elapsed" -lt 5 ] && poll_until 30 "${REC14}.exit"; then
  pass "14 detached launch released the caller's pipe after ${c14_elapsed}s with a 12 s child"
else
  fail "14 detached launch releases the caller's pipe" "rc=$rc14 (want 0)" "elapsed=${c14_elapsed}s (want < 5)" "DETACHED pid=${c14_pid:-none}" "exit file present=$( [ -e "${REC14}.exit" ] && echo yes || echo no )"
fi

echo "test_sidecar_detach: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "failed: ${FAILED_CASES[*]}"
  exit 1
fi
exit 0
