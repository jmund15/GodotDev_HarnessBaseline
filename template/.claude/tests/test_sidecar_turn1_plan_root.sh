#!/usr/bin/env bash
# Proof for sc_write_record's turn-1 context (lib/sidecar_common.sh `_turn1_context`): the child's
# transcript is read under the projects root of the record's PLAN (`SC_TRANSPORT`). A plan with a
# non-default `configDir` keeps its child transcripts in `<configDir>/projects`; every other plan
# reads `~/.claude/projects`. Registry, config dir and home are planted; nothing real is read.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/.claude/scripts/lib/sidecar_common.sh"

NATIVE="$(python3 - "$ROOT/.claude/tools" <<'PY'
import json, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
import model_registry
tmp = tempfile.mkdtemp(prefix="turn1_plan_")
data = model_registry.load()
second = os.path.join(tmp, "second-login")
data["transports"]["anthropic-secondary"]["configDir"] = second
with open(os.path.join(tmp, "external_models.json"), "w", encoding="utf-8", newline="\n") as fh:
    json.dump(data, fh)
row = {"type": "assistant", "message": {"usage": {"input_tokens": 1000, "cache_read_input_tokens": 500,
                                                  "cache_creation_input_tokens": 0}}}
for base, sid in ((os.path.join(second, "projects", "p"), "turn1-secondary-sid"),
                  (os.path.join(tmp, "home", ".claude", "projects", "p"), "turn1-primary-sid")):
    os.makedirs(base)
    with open(os.path.join(base, sid + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
print(tmp.replace("\\", "/"))
PY
)"
trap 'rm -rf "$NATIVE"' EXIT

export HARNESS_MODEL_REGISTRY="$NATIVE/external_models.json"
export HOME="$NATIVE/home" USERPROFILE="$NATIVE/home"
SC_LEDGER=""
SC_LEDGER_DEFAULT="$NATIVE/default.jsonl"
SC_WORKDIR="$ROOT"
SC_EFFORT="low"
SC_MODEL="sonnet"
SC_SCHEMA_FILE=""
SC_LABEL="turn1-proof"
SC_DISCLOSURE="full"
SC_COST_MODEL="plan-quota"
SC_REGISTRY_CLI="$ROOT/.claude/tools/model_registry.py"
SC_RECORD_PARSER="claude"

SC_TRANSPORT="anthropic-secondary"
SC_RECORD="$NATIVE/secondary.record.json"
sc_write_record '{"type":"result","subtype":"success","result":"ok","session_id":"turn1-secondary-sid","usage":{"input_tokens":1,"output_tokens":1}}' 0 2>/dev/null

SC_TRANSPORT="anthropic"
SC_RECORD="$NATIVE/primary.record.json"
sc_write_record '{"type":"result","subtype":"success","result":"ok","session_id":"turn1-primary-sid","usage":{"input_tokens":1,"output_tokens":1}}' 0 2>/dev/null

python3 - "$NATIVE/secondary.record.json" "$NATIVE/primary.record.json" <<'PY'
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
checks = [
    ("a secondary-plan child's turn-1 context is read under its configDir",
     a.get("turn1ContextTokens") == 1500),
    ("a primary-plan child's turn-1 context is still read under ~/.claude/projects",
     b.get("turn1ContextTokens") == 1500),
]
bad = 0
for name, ok in checks:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%r]" % ((a.get("turn1ContextTokens"), b.get("turn1ContextTokens")),)))
    bad += not ok
print("\n%d/%d passed" % (len(checks) - bad, len(checks)))
sys.exit(1 if bad else 0)
PY
