#!/usr/bin/env bash
# Proof that a paid launch never re-pays finished work
# (lib/sidecar_common.sh sc_gate_repay, sc_write_record; tools/work_key.py).
#   A  the gate computes the work key, a record and its ledger row carry it
#   B  a ledger row that finished the same work refuses the launch (exit 15) before anything spawns;
#      different work (another label), a resume, a --check and an unfinished row launch
#   C  a real launcher exits 15 on a finished row and never starts its child
# Planted children and ledgers only; nothing reaches a provider. Every case must produce output.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/.."
LIB="$R/scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "CANNOT RUN: lib missing at $LIB"; exit 2; }
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
unset SIDECAR_WORK_KEY
export SIDECAR_LEDGER_PATH="$TMPD/ledger.jsonl"
export SIDECAR_EXHAUSTED_DIR="$TMPD/exhausted"
SC_TRANSPORT="anthropic"
# shellcheck source=../scripts/lib/sidecar_common.sh
. "$LIB"
sc_check_launch_memory() { :; }
SC_ROOT="$R"
SC_FORMAT="stream-json"
EXTRA_ARGS=()
SC_RESUME=''
SC_WATCH_POLL_SEC=1
SC_STALL_SEC=900
SC_WORKDIR="$(cd "$R/.." && pwd)"
# A priced model from the live registry, so a roster change never breaks this proof.
SC_MODEL="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import model_registry as r; print(next(m["id"] for m in r.load()["models"] if m["id"].startswith("claude") and r.list_price(m["id"], fresh=1)[0]))' "$(sc_to_native "$R/tools")")"
SC_COST_MODEL="plan-quota"; SC_EFFORT="low"; SC_DISCLOSURE="pointer"
SC_SCHEMA_FILE=""; SC_RECORD_PARSER="claude"; SC_CONTEXT_TOKENS=""
BRIEF="$TMPD/brief.md"; printf 'Review the plan and return findings.\n' > "$BRIEF"
PASS=0; FAIL=0
ok()  { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; FAIL=$((FAIL+1)); }
CNT="$TMPD/spawned"
spawned() { [ -f "$CNT" ] && wc -l < "$CNT" | tr -d ' ' || echo 0; }
reset_run() { # <label>
  SC_USAGE_LIMITED=0; OUTPUT=""; rc=""; SC_RESUME_SID=""; SC_RESUME=""; SC_RESUMED_FROM=""
  unset SC_LAUNCH_OFF SC_WORK_KEY SIDECAR_WORK_KEY
  SC_DISPATCH_PENDING=1
  SC_LAUNCH_ID=""; SC_LAUNCH_PREPARED=0; SC_INVENTORY=""
  SC_LABEL="$1"; SC_PROMPT_FILE="$BRIEF"; SC_PROMPT="$(cat "$BRIEF")"
  SC_RECORD="$TMPD/$1.record.json"; SC_LEDGER="$TMPD/$1.ledger.jsonl"; : > "$CNT"
  P="$TMPD/$1.stream"; : > "$P"
}
rec_field() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1],encoding="utf-8")).get(sys.argv[2]); print("" if v is None else v)' "$(sc_to_native "$1")" "$2" 2>/dev/null; }
row_field() { python3 -c 'import json,sys; rows=[json.loads(l) for l in open(sys.argv[1],encoding="utf-8") if l.strip()]; v=rows[-1].get(sys.argv[2]) if rows else None; print("" if v is None else v)' "$(sc_to_native "$1")" "$2" 2>/dev/null; }
gate() { ( sc_gate_repay ) 2>"$TMPD/gate.err"; }
plant_row() { # <ledger> <key> <label> <exit> <cost>
  printf '{"timestamp":"2026-10-20T10:00:00Z","label":"%s","launchId":"planted-launch","workKey":"%s","exitCode":%s,"costUSD":%s}\n' "$3" "$2" "$4" "$5" >> "$1"
}
key_of() { ( sc_gate_repay 2>/dev/null; printf '%s' "${SC_WORK_KEY:-}" ); }
use_line() { # <n> <session> <tool> <path> : one assistant tool call
  printf '{"type":"assistant","session_id":"%s","message":{"id":"msg_%s","content":[{"type":"tool_use","id":"tu_%s","name":"%s","input":{"file_path":"%s"}}]}}\n' "$2" "$1" "$1" "$3" "$4"
}
result_line() { # <n> <session> <text>
  printf '{"type":"user","session_id":"%s","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_%s","content":"%s"}]}}\n' "$2" "$1" "$3"
}
worker() { # a different file each time, then a result
  printf '{"type":"system","subtype":"init","session_id":"S-W"}\n'
  local i; for i in 1 2 3 4 5 6 7 8; do use_line "$i" S-W Read "/f$i.md"; result_line "$i" S-W "text $i"; sleep 0.5; done
  printf '{"type":"result","session_id":"S-W","is_error":false,"result":"done"}\n'
}

echo "re-pay refusal — firing proof"

reset_run rp-a
key_a="$(key_of)"
[[ "$key_a" =~ ^[0-9a-f]{64}$ ]] && ok "A1 the gate computes a 64-hex work key" || bad "A1 the gate computes a 64-hex work key" "key=[$key_a]"
sc_gate_repay 2>/dev/null
sc_run_watched worker "$P" 2>/dev/null
sc_write_record "$OUTPUT" "$rc" 2>/dev/null
[ -n "${SC_WORK_KEY:-}" ] && [ "$(rec_field "$SC_RECORD" workKey)" = "$SC_WORK_KEY" ] \
  && ok "A2 the record carries workKey" || bad "A2 the record carries workKey" "record=$(rec_field "$SC_RECORD" workKey) key=${SC_WORK_KEY:-unset}"
[ "$(row_field "$SC_LEDGER" workKey)" = "${SC_WORK_KEY:-unset}" ] \
  && ok "A3 the ledger row carries workKey" || bad "A3 the ledger row carries workKey" "row=$(row_field "$SC_LEDGER" workKey)"

reset_run rp-b; L="$SC_LEDGER"
key_b="$(key_of)"
plant_row "$L" "$key_b" rp-b 0 0.42
gate; g=$?
[ "$g" = 15 ] && grep -q 'REFUSED' "$TMPD/gate.err" && grep -q 'rp-b' "$TMPD/gate.err" && grep -q 'planted-launch' "$TMPD/gate.err" \
  && ok "B1 a finished row with the same key refuses (exit 15) and names the row" \
  || bad "B1 a finished row with the same key refuses" "rc=$g err=$(tail -c 300 "$TMPD/gate.err")"
[ "$(spawned)" = 0 ] && ok "B2 the refused launch spawned nothing" || bad "B2 the refused launch spawned nothing" "spawned=$(spawned)"
SC_LABEL="rp-b-other"; gate; g=$?
[ "$g" = 0 ] && ok "B3 the same brief under another label is different work and launches" || bad "B3 another label launches" "rc=$g err=$(tail -c 200 "$TMPD/gate.err")"
SC_LABEL="rp-b"; SC_RESUME="S-OLD"; gate; g=$?
[ "$g" = 0 ] && ok "B4 a -r resume launch is not refused" || bad "B4 a -r resume launch is not refused" "rc=$g"
[ "$(SC_RESUME=S-OLD; sc_gate_repay 2>/dev/null; printf '%s' "${SC_WORK_KEY:-}")" = "$key_b" ] \
  && ok "B4b a resume still carries the work key into its record" || bad "B4b a resume still carries the work key"
SC_RESUME=""; SC_RESUME_SID="S-OLD"; gate; g=$?
[ "$g" = 0 ] && ok "B5 a resume-loop relaunch is not refused" || bad "B5 a resume-loop relaunch is not refused" "rc=$g"
SC_RESUME_SID=""; SC_DISPATCH_PENDING=0; gate; g=$?
[ "$g" = 0 ] && ok "B6 a --check (no launch flags parsed) is not refused" || bad "B6 a --check is not refused" "rc=$g"
SC_DISPATCH_PENDING=1
reset_run rp-c; L="$SC_LEDGER"
key_c="$(key_of)"
plant_row "$L" "$key_c" rp-c 1 0.42; plant_row "$L" "$key_c" rp-c 0 0
gate; g=$?
[ "$g" = 0 ] && ok "B7 a failed row and a zero-cost row do not refuse" || bad "B7 a failed row and a zero-cost row do not refuse" "rc=$g"
printf 'not json at all\n' >> "$L"; gate; g=$?
[ "$g" = 0 ] && ok "B8 a garbage line in the ledger does not refuse" || bad "B8 a garbage line does not refuse" "rc=$g"
mkdir "$TMPD/dir-ledger"; SC_LEDGER="$TMPD/dir-ledger"; gate; g=$?
[ "$g" = 15 ] && ok "B9 a ledger that cannot be read refuses" || bad "B9 a ledger that cannot be read refuses" "rc=$g"
reset_run rp-d; L="$SC_LEDGER"
export SIDECAR_WORK_KEY="cell-7"
key_d="$(key_of)"; plant_row "$L" "$key_d" rp-d 0 0.3
printf 'a different brief\n' > "$BRIEF"; gate; g=$?
[ "$g" = 15 ] && ok "B10 a key the starting tool declared decides, whatever the brief bytes" || bad "B10 a declared key decides" "rc=$g"
unset SIDECAR_WORK_KEY; printf 'Review the plan and return findings.\n' > "$BRIEF"

# C: a real launcher. The stub claude records every start; the planted ledger holds the finished work.
reset_run rp-e
cat > "$TMPD/claude_stub.sh" <<STUB
#!/usr/bin/env bash
echo started >> "$(sc_to_native "$CNT" | tr '\\\\' '/')"
printf '{"type":"result","session_id":"S-X","result":"done"}\n'
STUB
chmod +x "$TMPD/claude_stub.sh"
export SIDECAR_WORK_KEY="launcher-cell-1"
real_key="$(python3 "$(sc_to_native "$R/tools/work_key.py")" key --model "$SC_MODEL" --effort low --label rp-real)"
plant_row "$TMPD/ledger.jsonl" "$real_key" rp-real 0 0.5
: > "$CNT"
SIDECAR_DISPATCH_OWNER=test_sidecar_repay HARNESS_SIDECAR_PREFLIGHT=0 CLAUDE_BIN="$TMPD/claude_stub.sh" bash "$R/scripts/anthropic_sidecar.sh" -m "$SC_MODEL" -e low -l rp-real -f "$BRIEF" > "$TMPD/real.out" 2> "$TMPD/real.err"; g=$?
[ "$g" = 15 ] && grep -q 'REFUSED' "$TMPD/real.err" && [ "$(spawned)" = 0 ] \
  && ok "C1 a real launcher exits 15 on finished work and never starts its child" \
  || bad "C1 a real launcher refuses finished work" "rc=$g spawned=$(spawned) err=$(tail -c 400 "$TMPD/real.err")"
unset SIDECAR_WORK_KEY

echo "test_sidecar_repay: $PASS pass, $FAIL fail"
[ "$FAIL" = 0 ] && [ "$PASS" -ge 15 ]
