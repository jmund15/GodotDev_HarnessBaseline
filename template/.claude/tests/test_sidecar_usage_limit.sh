#!/usr/bin/env bash
# Proof that a provider usage limit ends a sidecar run and blocks new launches
# (lib/sidecar_common.sh sc_run_watched, sc_resume_loop, sc_write_record, sc_gate_availability).
#   A  a child retrying 429 forever is stopped: rc 10, stopReason recorded, marker written, no resume
#   B  the terminal usage-limit result is not resumed, even when the child lingers after it
#   C  a transient 429 that later succeeds keeps running and finishes normally
#   D  api_retry lines are not progress: the stall marker still fires, and nothing is killed
#   E  a live marker refuses a launch even with -A; an expired marker allows it
#   F  the first 429 retry writes <record>.usage (the session's wake signal) while the run keeps
#      retrying; later retries leave it as written, and a stale marker from an earlier launch is replaced
# Planted children only; nothing reaches a provider. Every case must produce output.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/.."
LIB="$R/scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "CANNOT RUN: lib missing at $LIB"; exit 2; }
SC_TRANSPORT="codex"
# shellcheck source=../scripts/lib/sidecar_common.sh
. "$LIB"
sc_check_launch_memory() { :; }
SC_ROOT="$R"
SC_FORMAT="stream-json"
EXTRA_ARGS=()
SC_RESUME=''
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
export SIDECAR_EXHAUSTED_DIR="$TMPD/exhausted"
MARKER="$SIDECAR_EXHAUSTED_DIR/codex.json"
SC_WATCH_POLL_SEC=1
SC_USAGE_LIMIT_RETRIES=3
SC_WORKDIR="$(cd "$R/.." && pwd)"
SC_MODEL="gpt-5.6-luna"; SC_COST_MODEL="plan-quota"; SC_EFFORT="low"; SC_DISCLOSURE="pointer"
SC_SCHEMA_FILE=""; SC_RECORD_PARSER="claude"; SC_CONTEXT_TOKENS=""
PASS=0; FAIL=0
ok()  { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; FAIL=$((FAIL+1)); }
CNT="$TMPD/calls"
resume_stub() { echo x >> "$CNT"; printf '{"type":"result","session_id":"S-X","result":"resumed"}\n'; }
calls() { [ -f "$CNT" ] && wc -l < "$CNT" | tr -d ' ' || echo 0; }

reset_run() { # <label>
  SC_USAGE_LIMITED=0; SC_USAGE_LIMIT_RESET=""; OUTPUT=""; rc=""; SC_RESUME_SID=""
  SC_LAUNCH_ID=""; SC_LAUNCH_PREPARED=0; SC_INVENTORY=""
  SC_LABEL="$1"; SC_RECORD="$TMPD/$1.record.json"; SC_LEDGER="$TMPD/$1.ledger.jsonl"
  P="$TMPD/$1.stream"; : > "$P"; : > "$CNT"
}
retry_line() { # <attempt> <status> <sid>
  printf '{"type":"system","subtype":"api_retry","attempt":%s,"max_retries":10,"retry_delay_ms":500,"error_status":%s,"error":"rate_limit","session_id":"%s"}\n' "$1" "$2" "$3"
}
init_line() { printf '{"type":"system","subtype":"init","session_id":"%s"}\n' "$1"; }
work_line() { printf '{"type":"assistant","session_id":"%s","message":{"content":[{"type":"tool_use","name":"Read","input":{}}]}}\n' "$1"; }
rec_field() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1],encoding="utf-8")).get(sys.argv[2]); print("" if v is None else v)' "$(sc_to_native "$1")" "$2" 2>/dev/null; }
marker_field() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1],encoding="utf-8")).get(sys.argv[2]); print("" if v is None else v)' "$(sc_to_native "$MARKER")" "$1" 2>/dev/null; }
ledger_rows() { [ -f "$1" ] && wc -l < "$1" | tr -d ' ' || echo 0; }

echo "provider usage limit — firing proof"

# --- A: 429 retries forever (bounded at 60 lines so a broken watchdog fails instead of hanging)
retry_forever() {
  init_line S-A; work_line S-A
  local i; for i in $(seq 1 60); do retry_line "$i" 429 S-A; sleep 0.5; done
  printf '{"type":"result","session_id":"S-A","is_error":false,"result":"never stopped"}\n'
}
reset_run ul-a; rm -rf "$SIDECAR_EXHAUSTED_DIR"; SC_STALL_SEC=900
t0=$(date +%s); sc_run_watched retry_forever "$P" 2>"$TMPD/a.err"; t1=$(date +%s)
[ "$rc" = 10 ] && [ "${SC_USAGE_LIMITED:-0}" = 1 ] && [ $((t1 - t0)) -lt 25 ] \
  && ok "A1 a run of 429 retries stops the child (rc=10 after $((t1 - t0))s)" \
  || bad "A1 a run of 429 retries stops the child" "rc=$rc limited=${SC_USAGE_LIMITED:-unset} elapsed=$((t1 - t0))s"
grep -q '"terminal_reason":"provider-usage-limit"' "$P" && ok "A2 the stream ends on a provider-usage-limit event" \
  || bad "A2 the stream ends on a provider-usage-limit event" "tail: $(tail -c 160 "$P")"
sc_resume_loop resume_stub "$P" 2>/dev/null
[ "$(calls)" = 0 ] && ok "A3 the stopped run is not resumed" || bad "A3 the stopped run is not resumed" "calls=$(calls)"
sc_write_record "$OUTPUT" "$rc" 2>/dev/null
[ "$(rec_field "$SC_RECORD" stopReason)" = "provider-usage-limit" ] && [ "$(rec_field "$SC_RECORD" exitCode)" = 10 ] \
  && ok "A4 the record carries stopReason provider-usage-limit and exitCode 10" \
  || bad "A4 the record carries stopReason provider-usage-limit and exitCode 10" "stopReason=$(rec_field "$SC_RECORD" stopReason) exit=$(rec_field "$SC_RECORD" exitCode)"
[ "$(ledger_rows "$SC_LEDGER")" = 1 ] && ok "A5 the ledger row is appended once" || bad "A5 the ledger row is appended once" "rows=$(ledger_rows "$SC_LEDGER")"
if [ -f "$MARKER" ]; then
  _left=$(( $(marker_field expiresAt) - $(date +%s) ))
  [ "$_left" -gt 1700 ] && [ "$_left" -le 1800 ] && [ -z "$(marker_field resetsAt)" ] \
    && ok "A6 the marker is written with a 30-minute expiry when no reset time is known" \
    || bad "A6 the marker is written with a 30-minute expiry when no reset time is known" "left=${_left}s resetsAt=$(marker_field resetsAt)"
else
  bad "A6 the marker is written with a 30-minute expiry when no reset time is known" "no marker at $MARKER"
fi

# --- B: the terminal usage-limit result, with a child that lingers after it
RESET=$(( $(date +%s) + 3600 ))
terminal_linger() {
  init_line S-B; work_line S-B
  printf '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":%s}}\n' "$RESET"
  retry_line 1 429 S-B; retry_line 2 429 S-B
  printf '{"type":"assistant","session_id":"S-B","message":{"model":"<synthetic>","content":[{"type":"text","text":"%s"}]}}\n' "You've hit your weekly limit · resets 1pm"
  printf '{"type":"result","subtype":"success","is_error":true,"terminal_reason":"api_error","api_error_status":429,"session_id":"S-B","num_turns":5,"usage":{"input_tokens":7,"output_tokens":3},"result":"%s"}\n' "You've hit your weekly limit · resets 1pm"
  sleep 60
}
reset_run ul-b; rm -rf "$SIDECAR_EXHAUSTED_DIR"
t0=$(date +%s); sc_run_watched terminal_linger "$P" 2>/dev/null; t1=$(date +%s)
[ "$rc" = 10 ] && [ $((t1 - t0)) -lt 25 ] && ok "B1 the terminal usage-limit result ends a lingering child (rc=10 after $((t1 - t0))s)" \
  || bad "B1 the terminal usage-limit result ends a lingering child" "rc=$rc elapsed=$((t1 - t0))s"
_saved_limited="$SC_USAGE_LIMITED"
SC_USAGE_LIMITED=0; sc_resume_loop resume_stub "$P" 2>/dev/null; SC_USAGE_LIMITED="$_saved_limited"
[ "$(calls)" = 0 ] && ok "B2 the classifier alone does not resume a usage-limit result" || bad "B2 the classifier alone does not resume a usage-limit result" "calls=$(calls)"
sc_write_record "$OUTPUT" "$rc" 2>/dev/null
[ "$(rec_field "$SC_RECORD" stopReason)" = "provider-usage-limit" ] && [ "$(rec_field "$SC_RECORD" usageLimitResetsAt)" = "$RESET" ] \
  && [ "$(rec_field "$SC_RECORD" inputTokens)" = 7 ] && [ -n "$(rec_field "$SC_RECORD" rateLimitInfo)" ] \
  && ok "B3 the record keeps the real result's tokens, the reset time and rateLimitInfo" \
  || bad "B3 the record keeps the real result's tokens, the reset time and rateLimitInfo" "stop=$(rec_field "$SC_RECORD" stopReason) reset=$(rec_field "$SC_RECORD" usageLimitResetsAt) in=$(rec_field "$SC_RECORD" inputTokens)"
[ -f "$MARKER" ] && [ "$(marker_field expiresAt)" = "$RESET" ] && ok "B4 the marker expires at the stream's reset time" \
  || bad "B4 the marker expires at the stream's reset time" "expiresAt=$(marker_field expiresAt) want=$RESET"

usage_field() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1],encoding="utf-8")).get(sys.argv[2]); print("" if v is None else v)' "$(sc_to_native "${SC_RECORD}.usage")" "$1" 2>/dev/null; }
[ -f "${SC_RECORD}.usage" ] && [ "$(usage_field transport)" = codex ] && [ "$(usage_field resetsAt)" = "$RESET" ] \
  && [ -n "$(usage_field firstSeen)" ] \
  && ok "B5 the usage-limit hit writes <record>.usage with the transport and the stream's reset time" \
  || bad "B5 the usage-limit hit writes <record>.usage" "exists=$([ -f "${SC_RECORD}.usage" ] && echo yes || echo no) transport=$(usage_field transport) resetsAt=$(usage_field resetsAt) want=$RESET"

# --- F: the FIRST retry writes <record>.usage while the run keeps going; later retries leave it
slow_retries() {
  init_line S-F; work_line S-F
  retry_line 1 429 S-F; sleep 3
  retry_line 2 429 S-F; sleep 3
  work_line S-F
  printf '{"type":"result","subtype":"success","is_error":false,"session_id":"S-F","result":"the deliverable"}\n'
}
reset_run ul-f; rm -rf "$SIDECAR_EXHAUSTED_DIR"
printf '{"firstSeen":"stale"}\n' > "${SC_RECORD}.usage"
( sc_run_watched slow_retries "$P" 2>/dev/null; printf '%s' "$rc" > "$TMPD/ul-f.rc" ) &
bg=$!
first=""; waited=0
while kill -0 "$bg" 2>/dev/null && [ "$waited" -lt 80 ]; do
  # The launch removes the stale marker before the first retry writes the new one; an empty read is that gap.
  seen="$(usage_field firstSeen)"
  if [ -n "$seen" ] && [ "$seen" != stale ]; then first="$(cat "${SC_RECORD}.usage")"; break; fi
  sleep 0.25; waited=$((waited + 1))
done
alive=no; kill -0 "$bg" 2>/dev/null && alive=yes
wait "$bg"; rc="$(cat "$TMPD/ul-f.rc" 2>/dev/null)"
[ -n "$first" ] && [ "$alive" = yes ] && [ "$(usage_field retries)" = 1 ] && [ -z "$(usage_field resetsAt)" ] \
  && ok "F1 the first 429 retry writes <record>.usage (retries=1, reset unknown) while the run is live" \
  || bad "F1 the first 429 retry writes <record>.usage while the run is live" "first=${first:-none} alive=$alive retries=$(usage_field retries)"
[ -n "$first" ] && [ "$(cat "${SC_RECORD}.usage" 2>/dev/null)" = "$first" ] \
  && ok "F2 the second retry does not rewrite the marker" \
  || bad "F2 the second retry does not rewrite the marker" "first=${first:-none} now=$(cat "${SC_RECORD}.usage" 2>/dev/null)"
[ "$rc" = 0 ] && ok "F3 the run keeps retrying and finishes normally (rc=0)" || bad "F3 the run keeps retrying and finishes normally" "rc=$rc"

# --- C: transient 429s that clear keep running
transient_then_success() {
  init_line S-C
  retry_line 1 429 S-C; retry_line 2 429 S-C; work_line S-C
  retry_line 1 429 S-C; retry_line 2 429 S-C; work_line S-C
  printf '{"type":"result","subtype":"success","is_error":false,"session_id":"S-C","result":"the deliverable"}\n'
}
reset_run ul-c; rm -rf "$SIDECAR_EXHAUSTED_DIR"
sc_run_watched transient_then_success "$P" 2>/dev/null
[ "$rc" = 0 ] && [ "${SC_USAGE_LIMITED:-0}" = 0 ] && printf '%s' "$OUTPUT" | grep -q 'the deliverable' && [ ! -f "$MARKER" ] \
  && ok "C1 transient 429s separated by work finish normally with no marker" \
  || bad "C1 transient 429s separated by work finish normally with no marker" "rc=$rc limited=${SC_USAGE_LIMITED:-unset} marker=$([ -f "$MARKER" ] && echo yes || echo no)"

# --- D: retry lines are not progress — the marker fires and the run keeps going
retry_non429() {
  init_line S-D; work_line S-D
  local i; for i in $(seq 1 20); do retry_line "$i" 529 S-D; printf '{"type":"system","subtype":"thinking_tokens","session_id":"S-D"}\n'; sleep 0.5; done
  printf '{"type":"result","session_id":"S-D","is_error":false,"result":"never stalled"}\n'
}
reset_run ul-d; SC_STALL_SEC=4
# The run must be observed WHILE it is silent, so it goes in a subshell that reports its own rc.
( sc_run_watched retry_non429 "$P" 2>/dev/null; printf '%s' "$rc" > "$TMPD/ul-d.rc"; printf '%s' "$OUTPUT" > "$TMPD/ul-d.out" ) &
bg=$!
# Watch for the whole run: the mark lands one poll after SC_STALL_SEC idle, plus each poll's python start.
marked=no; waited=0
while kill -0 "$bg" 2>/dev/null; do
  if [ -f "${SC_RECORD}.stall" ]; then marked=yes; break; fi
  sleep 0.25; waited=$((waited + 1))
done
wait "$bg"; rc="$(cat "$TMPD/ul-d.rc" 2>/dev/null)"
[ "$marked" = yes ] && [ "$rc" = 0 ] && grep -q 'never stalled' "$TMPD/ul-d.out" \
  && [ ! -f "${SC_RECORD}.stall" ] && [ ! -s "$CNT" ] \
  && ok "D1 api_retry and system lines do not reset the stall marker (marked, then the run finished rc=0)" \
  || bad "D1 api_retry and system lines do not reset the stall marker" "marked=$marked waited=${waited} rc=$rc"
SC_STALL_SEC=900

# --- E: the marker gate
write_marker() { # <expiresAt>
  mkdir -p "$SIDECAR_EXHAUSTED_DIR"
  printf '{"schemaVersion":1,"transport":"codex","writtenAt":%s,"expiresAt":%s,"resetsAt":null}\n' "$(date +%s)" "$1" > "$MARKER"
}
write_marker $(( $(date +%s) + 1200 ))
_out="$( (SC_MODEL=luna; SC_AUTHORIZED=1; SC_UNSUSPEND=1; sc_check_gates; echo GATES-PASSED) 2>&1 )"; _rc=$?
[ "$_rc" = 10 ] && ! printf '%s' "$_out" | grep -q GATES-PASSED && printf '%s' "$_out" | grep -q 'codex' \
  && printf '%s' "$_out" | grep -qi 'another provider' && printf '%s' "$_out" | grep -q 'expires' \
  && ok "E1 a live marker refuses a launch even with -A and -U, naming the provider, expiry and reroute" \
  || bad "E1 a live marker refuses a launch even with -A and -U" "rc=$_rc out=$(printf '%s' "$_out" | tr '\n' ' ' | cut -c1-240)"
write_marker $(( $(date +%s) - 10 ))
_out="$( (SC_TRANSPORT_STATE=available; SC_ALIAS=luna; sc_gate_availability; echo GATE-PASSED) 2>&1 )"; _rc=$?
[ "$_rc" = 0 ] && printf '%s' "$_out" | grep -q GATE-PASSED && ok "E2 an expired marker allows the launch" \
  || bad "E2 an expired marker allows the launch" "rc=$_rc out=$(printf '%s' "$_out" | tr '\n' ' ' | cut -c1-200)"
for _l in anthropic_sidecar.sh codex_proxy_sidecar.sh deepseek_sidecar.sh opencode_sidecar.sh; do
  _f="$R/scripts/$_l"
  # Assert the contract for every launcher THIS tree ships. anthropic_sidecar.sh is consumer-local
  # (lock status `local`), so the baseline template has no such file and grepping it would fail the
  # case for an absence that is correct there.
  [ -f "$_f" ] || continue
  _g=$(grep -n '^sc_gate_availability' "$_f" | head -1 | cut -d: -f1)
  _w=$(grep -n '^sc_run_watched' "$_f" | head -1 | cut -d: -f1)
  _c=$(grep -c '^  sc_check_gates' "$_f")
  [ -n "$_g" ] && [ -n "$_w" ] && [ "$_g" -lt "$_w" ] && [ "$_c" -ge 1 ] \
    && ok "E3 $_l gates the marker on dispatch and on --check" \
    || bad "E3 $_l gates the marker on dispatch and on --check" "gate=$_g run=$_w check=$_c"
done
_x=$(grep -n '^  sc_gate_availability' "$R/scripts/lib/sidecar_common.sh" | tail -1 | cut -d: -f1)
_q=$(grep -n '^  sc_gate_capacity' "$R/scripts/lib/sidecar_common.sh" | tail -1 | cut -d: -f1)
[ -n "$_x" ] && [ -n "$_q" ] && [ "$_x" -lt "$_q" ] \
  && ok "E4 --check refuses on a live exhausted marker before its live capacity probe" \
  || bad "E4 --check refuses on a live exhausted marker before its live capacity probe" "availability-gate=$_x capacity-gate=$_q"

# --- G: a login that fails to authenticate marks its plan, so the fan-out moves later jobs off it
rm -f "$MARKER"
_auth='{"type":"result","is_error":true,"api_error_status":null,"result":"Failed to authenticate: OAuth session expired and could not be refreshed"}'
(SC_LABEL=g1; sc_mark_auth_failure "$_auth" 1) 2>/dev/null
[ "$(marker_field reason)" = auth ] && ok "G1 an auth failure writes the plan's marker with reason auth" \
  || bad "G1 an auth failure writes the plan's marker with reason auth" "reason=$(marker_field reason)"
_out="$( (sc_gate_exhausted; echo GATE-PASSED) 2>&1 )"; _rc=$?
[ "$_rc" = 10 ] && printf '%s' "$_out" | grep -qi 'authenticate' && ! printf '%s' "$_out" | grep -qi 'usage limit' \
  && ok "G2 the marker refuses the next launch (exit 10) naming the login, not a usage limit" \
  || bad "G2 the marker refuses the next launch naming the login" "rc=$_rc out=$(printf '%s' "$_out" | tr '\n' ' ' | cut -c1-240)"
rm -f "$MARKER"
(sc_mark_auth_failure "$_auth" 0; sc_mark_auth_failure '{"type":"result","result":"done"}' 1) 2>/dev/null
[ ! -f "$MARKER" ] && ok "G3 a clean exit, or a failure that names no auth error, writes no marker" \
  || bad "G3 a clean exit, or a failure that names no auth error, writes no marker" "$(cat "$MARKER")"
rm -f "$MARKER"; reset_run g4
(sc_write_record "$_auth" 1) >/dev/null 2>&1
[ "$(marker_field reason)" = auth ] && ok "G4 every launcher's record write marks an auth failure (sc_write_record)" \
  || bad "G4 every launcher's record write marks an auth failure (sc_write_record)" "reason=$(marker_field reason)"
rm -f "$MARKER"

echo
echo "provider usage limit: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
