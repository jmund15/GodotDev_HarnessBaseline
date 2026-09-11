#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
source "$ROOT/.claude/scripts/lib/sidecar_common.sh"
SC_RECORD="$TMP/run.record.json"
SC_LEDGER="$TMP/ledger.jsonl"
SC_LEDGER_DEFAULT="$TMP/default.jsonl"
SC_WORKDIR="$ROOT"
SC_EFFORT="low"
SC_MODEL="muse-spark"
SC_SCHEMA_FILE=""
SC_LABEL="quota-proof"
SC_DISCLOSURE="pointer"
SC_TRANSPORT="anthropic"
SC_COST_MODEL="plan-quota"
SC_REGISTRY_CLI="$ROOT/.claude/tools/model_registry.py"
SC_CONTEXT_TOKENS=""
SC_RECORD_PARSER="claude"
RAW='{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","unifiedWindows":{"seven_day":{"utilization":0.98,"resetsAt":1800086400}}}}
{"type":"result","subtype":"success","result":"ok","usage":{"input_tokens":1,"output_tokens":1}}'
sc_write_record "$RAW" 0
python3 - "$SC_RECORD" "$SC_LEDGER" <<'PY'
import json, sys
record = json.load(open(sys.argv[1], encoding="utf-8"))
ledger = json.loads(open(sys.argv[2], encoding="utf-8").readline())
expected = record.get("rateLimitInfo", {}).get("unifiedWindows", {}).get("seven_day", {}).get("utilization") == 0.98
mirrored = ledger.get("rateLimitInfo") == record.get("rateLimitInfo")
for name, ok in (("record preserves rateLimitInfo", expected), ("ledger preserves rateLimitInfo", mirrored)):
    print(("ok   " if ok else "FAIL ") + name)
if not expected or not mirrored:
    raise SystemExit(1)
PY
