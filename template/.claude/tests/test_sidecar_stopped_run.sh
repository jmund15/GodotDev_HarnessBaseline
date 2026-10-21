#!/usr/bin/env bash
# Proof that a sidecar run is never stopped by a dollar figure, stays resumable when it ends without a
# result event, and hands the child every context file (lib/sidecar_common.sh sc_run_watched,
# sc_write_record, sc_build_disclosure).
#   A  a run that ends before its result event records the session id every stream event carries
#   B  a run whose priced usage far exceeds its model's past calls runs to its end: no dollar stop
#   C  every context file reaches the child: the CLI keeps only the last --append-system-prompt-file,
#      so the launcher passes one file holding them all
# Planted children only; nothing reaches a provider. The case A events are cut from a captured codex-proxy
# stream, content trimmed.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/.."
LIB="$R/scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "CANNOT RUN: lib missing at $LIB"; exit 2; }
SC_TRANSPORT="anthropic"
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
SC_WATCH_POLL_SEC=1
SC_STALL_SEC=900
SC_WORKDIR="$(cd "$R/.." && pwd)"
SC_MODEL="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import model_registry as r; print(next(m["id"] for m in r.load()["models"] if r.list_price(m["id"], fresh=1)[0]))' "$(sc_to_native "$R/tools")")"
SC_COST_MODEL="plan-quota"; SC_EFFORT="low"; SC_DISCLOSURE="full"
SC_SCHEMA_FILE=""; SC_RECORD_PARSER="claude"; SC_CONTEXT_TOKENS=""
PASS=0; FAIL=0
ok()  { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; FAIL=$((FAIL+1)); }
reset_run() { # <label>
  SC_USAGE_LIMITED=0; OUTPUT=""; rc=""; SC_RESUME_SID=""
  SC_LAUNCH_ID=""; SC_LAUNCH_PREPARED=0; SC_INVENTORY=""
  SC_LABEL="$1"; SC_RECORD="$TMPD/$1.record.json"; SC_LEDGER="$TMPD/$1.ledger.jsonl"
  P="$TMPD/$1.stream"; : > "$P"
}
rec_field() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1],encoding="utf-8")).get(sys.argv[2]); print("" if v is None else v)' "$(sc_to_native "$1")" "$2" 2>/dev/null; }
SID="3f687832-cdf4-4df8-96b3-9de89478ee62"
killed() { # the captured stream's first events, then the child dies with no result event
  printf '%s\n' '{"type":"system","subtype":"hook_started","hook_id":"1892aa5f-521a-4fdf-a8e8-0ac9dcd5bb47","hook_name":"SessionStart:startup","hook_event":"SessionStart","uuid":"fa726b3e-1846-4e4d-83b9-b48f01265ca9","session_id":"3f687832-cdf4-4df8-96b3-9de89478ee62"}'
  printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"x"}],"id":"msg_98362d63429e4b9d8fef3ca4f9609198","model":"gpt-6.1-sol","role":"assistant","stop_reason":null,"stop_sequence":null,"type":"message","usage":{"input_tokens":17876,"output_tokens":0},"context_management":null},"parent_tool_use_id":null,"session_id":"3f687832-cdf4-4df8-96b3-9de89478ee62","uuid":"501bc8bf-638b-40e5-bba4-6168771b14b0","timestamp":"2026-10-20T16:14:39.264Z"}'
  return 1
}
use_line() { # <n> : one assistant request of 200k fresh input and 20k output
  printf '{"type":"assistant","session_id":"S-B","message":{"id":"msg_%s","model":"%s","content":[{"type":"text","text":"x"}],"usage":{"input_tokens":200000,"output_tokens":20000}}}\n' "$1" "$SC_MODEL"
}
spender() { # 24 large requests over 12 s, then its result: a dollar stop would end it before the result line
  printf '{"type":"system","subtype":"init","session_id":"S-B"}\n'
  local i; for i in $(seq 1 24); do use_line "$i"; sleep 0.5; done
  printf '{"type":"result","session_id":"S-B","is_error":false,"result":"finished"}\n'
}

echo "stopped run — firing proof"

# B runs first: it is a fresh launch, with no state an earlier run in this shell could carry.
reset_run sr-b
SC_LEDGER_DEFAULT="$TMPD/history.jsonl"
for i in $(seq 1 25); do printf '{"requestedModel":"%s","costUSD":0.0001}\n' "$SC_MODEL" >> "$SC_LEDGER_DEFAULT"; done
sc_run_watched spender "$P" 2>"$TMPD/b.err"
[ "$rc" = 0 ] && printf '%s' "$OUTPUT" | grep -q '"result":"finished"' && ! grep -qi 'budget' "$TMPD/b.err" \
  && ok "B1 no dollar figure stops a run, however far past its model's history" \
  || bad "B1 no dollar figure stops a run, however far past its model's history" "rc=$rc err=$(tail -c 300 "$TMPD/b.err")"

reset_run sr-a
sc_run_watched killed "$P" 2>"$TMPD/a.err"
sc_write_record "$OUTPUT" "$rc" 2>/dev/null
[ "$(rec_field "$SC_RECORD" sessionId)" = "$SID" ] \
  && ok "A1 a run with no result event keeps its session id in the record" \
  || bad "A1 a run with no result event keeps its session id in the record" "sessionId=$(rec_field "$SC_RECORD" sessionId)"

printf 'Codeword ALPHA-7731.\n' > "$TMPD/c1.md"; printf 'Codeword BRAVO-4409.\n' > "$TMPD/c2.md"
SC_CONTEXT_FILES=("$TMPD/c1.md" "$TMPD/c2.md")
sc_build_disclosure 2>/dev/null
n=0; f=""; prev=""
for a in ${SC_APPEND_ARGS[@]+"${SC_APPEND_ARGS[@]}"}; do
  [ "$prev" = "--append-system-prompt-file" ] && { n=$((n + 1)); f="$a"; }
  prev="$a"
done
[ "$n" = 1 ] && grep -q ALPHA-7731 "$f" && grep -q BRAVO-4409 "$f" && grep -q 'You run UNATTENDED' "$f" \
  && ok "C1 one append file carries the autonomy rail and every context file" \
  || bad "C1 one append file carries the autonomy rail and every context file" "files=$n last=$f"

echo "test_sidecar_stopped_run: $PASS pass, $FAIL fail"
[ "$FAIL" = 0 ] && [ "$PASS" -ge 3 ]
