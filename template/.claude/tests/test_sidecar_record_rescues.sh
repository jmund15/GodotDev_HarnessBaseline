#!/usr/bin/env bash
# Proof for sc_write_record (lib/sidecar_common.sh): the opencode launcher's `rescues` list (one resume per
# empty final turn or schema failure, D8) lands on the run record unchanged. The board counts a rescue rate
# per model from it, so a launcher that reports no list leaves the record without the key: unmeasured, never
# a zero count.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
source "$ROOT/.claude/scripts/lib/sidecar_common.sh"
SC_LEDGER=""
SC_LEDGER_DEFAULT="$TMP/default.jsonl"
SC_WORKDIR="$ROOT"
SC_EFFORT="none"
SC_MODEL="cli-rescue-fixture"
SC_SCHEMA_FILE=""
SC_LABEL="rescue-proof"
SC_DISCLOSURE="full"
SC_TRANSPORT="opencode-cli"
SC_COST_MODEL="plan-quota"
SC_REGISTRY_CLI="$ROOT/.claude/tools/model_registry.py"
SC_RECORD_PARSER="claude"
SC_PERM_MODE="bypassPermissions"; SC_STALL_SEC="900"
USAGE='"usage":{"input_tokens":10,"output_tokens":4}'

# 1. one empty-turn rescue and one schema repair: the list arrives in the order the launcher wrote it
SC_RECORD="$TMP/rescued.record.json"
sc_write_record '{"type":"result","subtype":"success","is_error":false,"result":"{\"a\":1}","structured_output":{"a":1},"engagement":"proven","rescues":[{"kind":"empty-turn","detail":"0 output tokens on the final turn"},{"kind":"schema-repair","detail":"Extra data: line 2 column 1"}],'"$USAGE"'}' 0
# 2. a launcher result with no rescues key: the record carries no key (unmeasured), not an empty list
SC_RECORD="$TMP/plain.record.json"
sc_write_record '{"type":"result","subtype":"success","is_error":false,"result":"{\"a\":1}","engagement":"unproven",'"$USAGE"'}' 0

python3 - "$TMP" <<'PY'
import json, os, sys
tmp = sys.argv[1]
rescued = json.load(open(os.path.join(tmp, "rescued.record.json"), encoding="utf-8"))
plain = json.load(open(os.path.join(tmp, "plain.record.json"), encoding="utf-8"))
want = [{"kind": "empty-turn", "detail": "0 output tokens on the final turn"},
        {"kind": "schema-repair", "detail": "Extra data: line 2 column 1"}]
assert rescued.get("rescues") == want, rescued.get("rescues")
assert "rescues" not in plain, plain.get("rescues")
print("PASS sc_write_record copies the rescues list unchanged and omits it when the launcher reports none")
PY
