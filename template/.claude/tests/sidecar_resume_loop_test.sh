#!/usr/bin/env bash
# Prove sc_resume_loop FIRES. The classifier had a selftest; the loop that consumes it had never
# executed once, and registration is not matching (instruction_quality §14).
#
# Runs the real function from lib/sidecar_common.sh against a stub run_fn, so it costs nothing and
# reaches no provider. Four cases, each asserting a different half of the contract:
#   1 transient fault -> resumes with the detected sid, and OUTPUT becomes the RESUMED result
#   2 clean run       -> does not resume (a loop that fires on success re-buys every good run)
#   3 never-resume    -> does not resume (auth/quota faults loop forever if retried)
#   4 budget exhausted-> stops at the cap AND says the result is the fault, not the deliverable
#
#   bash .claude/tests/sidecar_resume_loop_test.sh
set -u

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$R/scripts/lib/sidecar_common.sh"
SC_ROOT="$R"
SC_FORMAT="stream-json"
EXTRA_ARGS=()
SC_RESUME=''
PASS=0; FAIL=0
ERRF="$(mktemp)"
trap 'rm -f "$ERRF"' EXIT

# Never wrap the call in $( ) -- that is a subshell and the caller-scope writes this
# function exists to make (OUTPUT, rc) would be thrown away.
runloop() { sc_resume_loop "$@" 2>"$ERRF" >/dev/null; ERR="$(cat "$ERRF")"; }

# The loop captures the child with $(...), so the child ALWAYS runs in a subshell and a
# shell-variable counter is structurally unable to see it. Count on disk.
CNT="$(mktemp)"
trap 'rm -f "$ERRF" "$CNT"' EXIT
calls() { wc -l < "$CNT" | tr -d " "; }
reset_calls() { : > "$CNT"; }

# Source the library's complete function dependencies; its flags are parsed only when called.
declare -F sc_resume_loop >/dev/null || { echo "CANNOT RUN: sc_resume_loop not found in sidecar_common.sh"; exit 2; }
declare -F sc_run_watched >/dev/null || { echo "CANNOT RUN: sc_run_watched not found in sidecar_common.sh"; exit 2; }
# Keep the watchdog off: a stub answers instantly and the stream is unset.
SC_STALL_SEC=0; SC_STALLED=0

SID="sess-abc-123"
transient() {
  printf '%s\n' \
    "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"$SID\"}" \
    "{\"type\":\"assistant\",\"session_id\":\"$SID\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"working\"}]}}" \
    "{\"type\":\"result\",\"session_id\":\"$SID\",\"is_error\":true,\"result\":\"API Error: 500 {\\\"error\\\":{\\\"type\\\":\\\"api_error\\\",\\\"message\\\":\\\"websocket stream error: Connection reset without closing handshake\\\"}}\"}"
}
clean() {
  printf '%s\n' \
    "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"$SID\"}" \
    "{\"type\":\"result\",\"session_id\":\"$SID\",\"is_error\":false,\"result\":\"the deliverable\"}"
}
never() {
  printf '%s\n' \
    "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"$SID\"}" \
    "{\"type\":\"result\",\"session_id\":\"$SID\",\"is_error\":true,\"result\":\"API Error: 401 {\\\"error\\\":{\\\"type\\\":\\\"authentication_error\\\",\\\"message\\\":\\\"invalid x-api-key\\\"}}\"}"
}

check() { # name expected_substring actual
  if printf '%s' "$3" | grep -qF -- "$2"; then echo "  PASS  $1"; PASS=$((PASS+1))
  else echo "  FAIL  $1"; echo "        wanted: $2"; echo "        got:    $(printf '%s' "$3" | tr '\n' ' ' | cut -c1-160)"; FAIL=$((FAIL+1)); fi
}
check_not() {
  if printf '%s' "$3" | grep -qF -- "$2"; then echo "  FAIL  $1 (should NOT contain: $2)"; FAIL=$((FAIL+1))
  else echo "  PASS  $1"; PASS=$((PASS+1)); fi
}

echo "sc_resume_loop — firing proof"

# --- 1. transient fault resumes, and the RESUMED output replaces the fault -------------------
reset_calls
run_stub() { echo x >> "$CNT"; clean; }
OUTPUT="$(transient)"; rc=1; SC_RESUME_SID=''
runloop run_stub ''
check "1a resumes on a transient fault"        "resuming session $SID" "$ERR"
check "1b OUTPUT becomes the resumed result"   "the deliverable"       "$OUTPUT"
check "1c passes the detected sid to the child" "$SID"                 "${SC_RESUME_SID:-}"
[ "$(calls)" = "1" ] && { echo "  PASS  1d resumed exactly once"; PASS=$((PASS+1)); } \
                   || { echo "  FAIL  1d resumed $(calls) times, expected 1"; FAIL=$((FAIL+1)); }

# --- 2. a clean run must NOT resume ----------------------------------------------------------
reset_calls
OUTPUT="$(clean)"; rc=0; SC_RESUME_SID=''
runloop run_stub ''
check_not "2a does not resume a clean run" "resuming session" "$ERR"
[ "$(calls)" = "0" ] && { echo "  PASS  2b never called the child"; PASS=$((PASS+1)); } \
                   || { echo "  FAIL  2b called the child $(calls) times on a clean run"; FAIL=$((FAIL+1)); }

# --- 3. a never-resume fault must NOT resume -------------------------------------------------
reset_calls
OUTPUT="$(never)"; rc=1; SC_RESUME_SID=''
runloop run_stub ''
check_not "3a does not resume a 401" "resuming session" "$ERR"
[ "$(calls)" = "0" ] && { echo "  PASS  3b never called the child"; PASS=$((PASS+1)); } \
                   || { echo "  FAIL  3b retried an auth fault $(calls) times"; FAIL=$((FAIL+1)); }

# --- 4. budget exhausted announces that the result is the FAULT, not the deliverable ----------
reset_calls
run_always_fails() { echo x >> "$CNT"; transient; }
OUTPUT="$(transient)"; rc=1; SC_RESUME_SID=''
SC_COMPACT_RESUMES=2 runloop run_always_fails ''
check "4a announces the exhausted budget" "RESUME BUDGET EXHAUSTED" "$ERR"
check "4b says the result is NOT the deliverable" "NOT the deliverable" "$ERR"
[ "$(calls)" = "2" ] && { echo "  PASS  4c stopped at the cap (2)"; PASS=$((PASS+1)); } \
                   || { echo "  FAIL  4c ran $(calls) times, cap was 2"; FAIL=$((FAIL+1)); }

# --- 5. non-stream-json says so instead of failing silently ----------------------------------
OUTPUT="$(transient)"; rc=1
SC_FORMAT=json runloop run_stub ''
check "5a -o json states resume is unavailable" "resume is unavailable" "$ERR"

# --- 6/7. ported from the retired test_sidecar_compact_resume.sh (canned streams, same shape
# measured 2026-09-03: a summary_ending run must resume and end on the resumed result; a clean
# run must not resume) -------------------------------------------------------------------------
summary_ending() {
  printf '%s\n' \
    '{"type":"system","subtype":"compact_boundary","compact_metadata":{"trigger":"auto","pre_tokens":225557,"post_tokens":11611}}' \
    '{"type":"assistant","message":{"content":[{"type":"text","text":"<analysis>\nThe session began as an audit.\n</analysis>"}]}}' \
    '{"type":"result","subtype":"success","num_turns":81,"terminal_reason":"completed","session_id":"sid-compacted","result":"<analysis>\nThe session began as an audit.\n</analysis>\n<summary>\nNo findings were emitted.\n</summary>"}'
}
worked() {
  printf '%s\n' \
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}' \
    '{"type":"result","subtype":"success","num_turns":9,"terminal_reason":"completed","session_id":"sid-resumed","result":"[{\"finding\":\"real\"}]"}'
}
clean_run() {
  printf '%s\n' \
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}' \
    '{"type":"result","subtype":"success","num_turns":9,"terminal_reason":"completed","session_id":"sid-clean","result":"DONE"}'
}

reset_calls
run_worked() { echo x >> "$CNT"; worked; }
OUTPUT="$(summary_ending)"; rc=0; SC_RESUME_SID=''
runloop run_worked ''
check "6a positive_summary_ending resumes"                 "resuming session sid-compacted" "$ERR"
check "6b positive_summary_ending ends on the RESUMED result, not the compaction summary" "sid-resumed" "$OUTPUT"
[ "$(calls)" = "1" ] && { echo "  PASS  6c resumed exactly once"; PASS=$((PASS+1)); } \
                   || { echo "  FAIL  6c resumed $(calls) times, expected 1"; FAIL=$((FAIL+1)); }

reset_calls
OUTPUT="$(clean_run)"; rc=0; SC_RESUME_SID=''
runloop run_worked ''
check_not "7a negative_clean_run does not resume" "resuming session" "$ERR"
[ "$(calls)" = "0" ] && { echo "  PASS  7b negative_clean_run never called the child"; PASS=$((PASS+1)); } \
                   || { echo "  FAIL  7b called the child $(calls) times on a clean run"; FAIL=$((FAIL+1)); }

echo
echo "sc_resume_loop: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
