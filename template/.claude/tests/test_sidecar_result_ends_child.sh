#!/usr/bin/env bash
# Proof that a terminal result ends the child (lib/sidecar_common.sh sc_run_watched):
#   1. a child that wrote its success result and stays alive (a background task would re-wake it, each
#      wake a billed turn) is ended SC_RESULT_GRACE_SEC after that result: the run returns rc=0 with the
#      first result, the re-wake never reaches OUTPUT, and the stub's process is gone;
#   2. the same with an error result returns rc=1, the code claude -p exits with on an error result;
#   3. a child that exits by itself right after its result keeps its own rc and no end notice prints;
#   4. a child silent past the grace with NO result is never ended: it reaches its own late result;
#   5. SC_RESULT_GRACE_SEC=0 turns the end off: the lingering child runs to its own exit;
#   6. a usage-limit result with no reset time keeps the watch fields in place;
#   7. `-R` with an explicit `-o json` (and no `-P`) watches a stream like `-R` alone, and the caller still
#      gets the json contract: one result object, the first result, never the re-wake or stream rows;
#      `-o text` keeps its refusal and a run without `-R` stays unwatched.
# The child is a stub function; no model is launched. Every case must print OK or FAIL.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
# shellcheck source=../scripts/lib/sidecar_common.sh
. "$LIB"
sc_check_launch_memory() { :; }
SC_FORMAT="stream-json"
fail=0
d="$(mktemp -d)"
say() { echo "$*"; }

# A result, then the wait claude -p does while a background task runs, then the re-wake that task causes.
lingering_ok() {
  printf '%s' "$BASHPID" > "$d/stub.pid"
  printf '{"type":"system","subtype":"init","session_id":"s1"}\n{"type":"assistant","n":1}\n'
  printf '{"type":"result","subtype":"success","is_error":false,"result":"first","session_id":"s1"}\n'
  sleep 40
  printf '{"type":"system","subtype":"init","session_id":"s1"}\n{"type":"assistant","n":2}\n'
  printf '{"type":"result","subtype":"success","is_error":false,"result":"rewake","session_id":"s1"}\n'
}
lingering_err() {
  printf '{"type":"assistant","n":1}\n'
  printf '{"type":"result","subtype":"success","is_error":true,"result":"Credit balance is too low","session_id":"s1"}\n'
  sleep 40
  printf '{"type":"result","subtype":"success","is_error":true,"result":"rewake","session_id":"s1"}\n'
}
prompt_exit() { printf '{"type":"assistant","n":1}\n{"type":"result","is_error":false,"result":"done3"}\n'; return 0; }
late_result() { printf '{"type":"system","subtype":"init"}\n'; sleep 5; printf '{"type":"assistant","n":1}\n{"type":"result","is_error":false,"result":"late"}\n'; }
short_linger() { printf '{"type":"assistant","n":1}\n{"type":"result","is_error":false,"result":"first5"}\n'; sleep 5; printf '{"type":"result","is_error":false,"result":"rewake5"}\n'; }

# run_case <name> <fn> <grace> -> $d/<name>.{rc,out,err,secs}
run_case() {
  local name="$1" fn="$2" grace="$3" start
  : > "$d/$name.stream"; start=$(date +%s)
  (
    SC_RECORD="$d/$name.record.json" SC_STALL_SEC=0 SC_WATCH_POLL_SEC=1 SC_RESULT_GRACE_SEC="$grace" OUTPUT="" rc=""
    sc_run_watched "$fn" "$d/$name.stream" 2> "$d/$name.err"
    printf '%s' "$rc" > "$d/$name.rc"; printf '%s' "$OUTPUT" > "$d/$name.out"
  )
  echo $(( $(date +%s) - start )) > "$d/$name.secs"
}
field() { cat "$d/$1.$2" 2>/dev/null; }

# --- case 1
run_case c1 lingering_ok 2
stub="$(cat "$d/stub.pid" 2>/dev/null)"
alive=no; [ -n "$stub" ] && kill -0 "$stub" 2>/dev/null && alive=yes
if [ "$(field c1 rc)" = 0 ] && [ "$(field c1 secs)" -lt 20 ] && grep -q '"first"' "$d/c1.out" \
   && ! grep -q rewake "$d/c1.out" && [ "$alive" = no ] && grep -q 'terminal result' "$d/c1.err"; then
  say "OK   a child alive past its success result is ended after the grace: rc=0, first result kept, stub gone ($(field c1 secs)s)"
else
  say "FAIL lingering success: rc=$(field c1 rc) secs=$(field c1 secs) stub_alive=$alive out=$(tail -c 160 "$d/c1.out" 2>/dev/null) err=$(cat "$d/c1.err" 2>/dev/null)"
  fail=1
fi
[ "$alive" = yes ] && kill "$stub" 2>/dev/null

# --- case 2
run_case c2 lingering_err 2
if [ "$(field c2 rc)" = 1 ] && [ "$(field c2 secs)" -lt 20 ] && ! grep -q rewake "$d/c2.out"; then
  say "OK   an error result lingering past the grace ends with rc=1"
else
  say "FAIL lingering error: rc=$(field c2 rc) secs=$(field c2 secs) out=$(tail -c 160 "$d/c2.out" 2>/dev/null)"; fail=1
fi

# --- case 3
run_case c3 prompt_exit 2
if [ "$(field c3 rc)" = 0 ] && grep -q done3 "$d/c3.out" && ! grep -q 'terminal result' "$d/c3.err"; then
  say "OK   a child that exits after its result keeps its own rc and prints no end notice"
else
  say "FAIL prompt exit: rc=$(field c3 rc) err=$(cat "$d/c3.err" 2>/dev/null)"; fail=1
fi

# --- case 4
run_case c4 late_result 1
if [ "$(field c4 rc)" = 0 ] && grep -q '"late"' "$d/c4.out" && ! grep -q 'terminal result' "$d/c4.err"; then
  say "OK   a child silent past the grace with no result runs to its own result"
else
  say "FAIL no-result silence: rc=$(field c4 rc) out=$(tail -c 160 "$d/c4.out" 2>/dev/null) err=$(cat "$d/c4.err" 2>/dev/null)"; fail=1
fi

# --- case 5
run_case c5 short_linger 0
if [ "$(field c5 rc)" = 0 ] && grep -q rewake5 "$d/c5.out" && ! grep -q 'terminal result' "$d/c5.err"; then
  say "OK   SC_RESULT_GRACE_SEC=0 leaves a lingering child to its own exit"
else
  say "FAIL grace off: rc=$(field c5 rc) out=$(tail -c 160 "$d/c5.out" 2>/dev/null) err=$(cat "$d/c5.err" 2>/dev/null)"; fail=1
fi

# --- case 6: an empty --watch field (no reset time) keeps every later field in place
printf '{"type":"assistant","session_id":"s1"}\n{"type":"result","is_error":true,"api_error_status":429,"result":"usage limit","session_id":"s1"}\n' > "$d/c6.stream"
got6="$(SC_RECORD="" _sc_off=0 _sc_run=0 SC_USAGE_LIMIT_RESET="" SC_USAGE_LIMIT_SID="" SC_USAGE_MARKED=1
        sc_watch_step "$d/c6.stream"; printf '%s|%s|%s' "$SC_USAGE_LIMITED" "$SC_USAGE_LIMIT_RESET" "$SC_USAGE_LIMIT_SID")"
if [ "$got6" = "1||s1" ]; then
  say "OK   a usage limit with no reset time keeps resets empty and the session id in its own field"
else
  say "FAIL empty field shifted the watch fields: limited|reset|sid='$got6' (want 1||s1)"; fail=1
fi

# --- case 7: -R with an explicit -o json is watched as a stream and still answers in the json contract
settle() { # <record> <format-set> <format> -> "<progress>|<format>|<caller_json>"
  ( SC_RECORD="$1" SC_PROGRESS="" SC_FORMAT_SET="$2" SC_FORMAT="$3" SC_CALLER_JSON=""
    sc_settle_format; printf '%s|%s|%s' "$SC_PROGRESS" "$SC_FORMAT" "$SC_CALLER_JSON" )
}
got="$(settle "$d/r7" 1 json)"
[ "$got" = "$d/r7.stream|stream-json|1" ] && say "OK   -R with an explicit -o json watches <record>.stream and remembers the json contract" \
  || { say "FAIL -R -o json: '$got' (want $d/r7.stream|stream-json|1)"; fail=1; }
got="$(settle "$d/r7" "" json)"
[ "$got" = "$d/r7.stream|stream-json|" ] && say "OK   -R alone streams with no json conversion owed" \
  || { say "FAIL -R alone: '$got'"; fail=1; }
got="$(settle "" 1 json)"
[ "$got" = "|json|" ] && say "OK   a run without -R stays unwatched in its own format" \
  || { say "FAIL no -R: '$got'"; fail=1; }
got="$(settle "$d/r7" 1 text)"
[ "$got" = "|text|" ] && say "OK   -R with -o text is left for its refusal" \
  || { say "FAIL -R -o text: '$got'"; fail=1; }

json_run() { # <caller-json> -> converted OUTPUT on stdout
  : > "$d/j.stream"
  ( SC_RECORD="$d/j.record.json" SC_STALL_SEC=0 SC_WATCH_POLL_SEC=1 SC_RESULT_GRACE_SEC=2 OUTPUT="" rc="" \
      SC_CALLER_JSON="$1" SC_COMPACT_RESUMES=0
    sc_run_watched lingering_ok "$d/j.stream" 2> "$d/j.err"
    sc_resume_loop lingering_ok "$d/j.stream" 2>> "$d/j.err"
    printf '%s\n' "$OUTPUT" )
}
out7="$(json_run 1)"
kind7="$(printf '%s' "$out7" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print(d.get("type"), d.get("result"))' 2>&1)"
if [ "$kind7" = "result first" ] && ! grep -q rewake <<< "$out7" && grep -q 'terminal result' "$d/j.err"; then
  say "OK   a json-contract run ends at the grace and prints one result object, not stream rows"
else
  say "FAIL json contract: parsed='$kind7' out=$(printf '%s' "$out7" | tail -c 200) err=$(cat "$d/j.err" 2>/dev/null)"; fail=1
fi
out7b="$(json_run "")"
if [ "$(printf '%s\n' "$out7b" | grep -c '"type"')" -ge 3 ]; then
  say "OK   control: a stream run with no json contract keeps its stream rows"
else
  say "FAIL stream control converted: $(printf '%s' "$out7b" | tail -c 200)"; fail=1
fi
printf '{"type":"system","session_id":"s9"}\n' > "$d/j2.out"
out7c="$( SC_CALLER_JSON=1 SC_FORMAT=stream-json OUTPUT="$(cat "$d/j2.out")" rc=0; sc_resume_loop true "" 2>/dev/null; printf '%s' "$OUTPUT" )"
[ "$out7c" = "$(cat "$d/j2.out")" ] && say "OK   a stream with no result is left as written, never invented into one" \
  || { say "FAIL no-result stream was rewritten: $out7c"; fail=1; }

rm -rf "$d"
exit $fail
