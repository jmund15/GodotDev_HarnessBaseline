#!/usr/bin/env bash
# Proof for sc_write_record (lib/sidecar_common.sh): the opencode launcher's `rescues` list (one resume per
# empty final turn or schema failure, D8) lands on the run record unchanged. The board counts a rescue rate
# per model from it, so a launcher that reports no list leaves the record without the key: unmeasured, never
# a zero count. It also proves the record never reports a LiteLLM-estimated count as the provider's:
# a chat-mode Go row through a LiteLLM below the usage floor records null token counts and a reason, never the
# estimate (measured 2026-11-18: LiteLLM 1.99.0 dropped Go's usage chunk, so cache read read 0 and output
# omitted reasoning), while the same row on a good LiteLLM and a messages-mode row keep the counts.
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

# 3-5. usage trust: the result event is cut from the captured child output of a glm-5.3 Go run
# (.claude/scratch/loops_review2/out/loops-pair-glm.out.json, trimmed in fixtures/go_usage/glm_go_result.json)
result_for() { python3 -c "
import json,sys
d=json.load(open(sys.argv[1], encoding='utf-8'))
mu=d['modelUsage']; (k,v), = mu.items(); v['canonicalModel']=sys.argv[2]; d['modelUsage']={sys.argv[2]: v}
print(json.dumps(d))" "$ROOT/.claude/tests/fixtures/go_usage/glm_go_result.json" "$1"; }
rowof() { python3 -c "
import json,sys
d=json.load(open(sys.argv[1], encoding='utf-8'))
print(next(m['id'] for m in d['models'] if m['transport']=='go' and m.get('apiMode')==sys.argv[2]))" "$ROOT/.claude/reference/external_models.json" "$1"; }
CHAT_ID="$(rowof chat)"; MESSAGES_ID="$(rowof messages)"
SC_TRANSPORT="go"
SC_MODEL="$CHAT_ID"; SC_PROXY_VERSION=""
SC_RECORD="$TMP/chat_old.record.json"; sc_write_record "$(result_for "$CHAT_ID")" 0
SC_PROXY_VERSION="1.110.0"
SC_RECORD="$TMP/chat_new.record.json"; sc_write_record "$(result_for "$CHAT_ID")" 0
SC_MODEL="$MESSAGES_ID"; SC_PROXY_VERSION=""
SC_RECORD="$TMP/messages.record.json"; sc_write_record "$(result_for "$MESSAGES_ID")" 0

python3 - "$TMP" <<'PY'
import json, os, sys
tmp = sys.argv[1]
rec = lambda n: json.load(open(os.path.join(tmp, n + ".record.json"), encoding="utf-8"))
old, new, msg = rec("chat_old"), rec("chat_new"), rec("messages")
counts = ("inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens")
assert all(old[k] is None for k in counts), {k: old[k] for k in counts}
assert old["costUSD"] is None and old.get("usageUnmeasured"), (old["costUSD"], old.get("usageUnmeasured"))
assert old["proxyVersion"] is None
assert (new["inputTokens"], new["cacheReadTokens"], new["outputTokens"]) == (3971660, 0, 9875), new
assert new["usageUnmeasured"] is None and new["proxyVersion"] == "1.110.0"
assert (msg["inputTokens"], msg["outputTokens"]) == (3971660, 9875) and msg["usageUnmeasured"] is None, msg
print("PASS a chat-mode Go record on a LiteLLM below the usage floor carries null counts and a reason; a good LiteLLM and a messages row keep them")
PY
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
