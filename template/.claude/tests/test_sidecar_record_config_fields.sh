#!/usr/bin/env bash
# Proof for sc_write_record (lib/sidecar_common.sh): every run record states the configuration the child
# ran under (F148, F144): permissionMode, maxOutputTokens (null = the client default, never a guess),
# contextWindow, the stall watchdog limit and, for a resumed run, the session it resumed. A board row
# compares configurations, so a record that omits them pools cells run under different ones.
# contextWindow comes from the dispatched model, with contextWindowSource beside it: a `[1m]` suffix means
# 1000000, then the window the launcher declared, then the registry row's limits.contextTokens; with none of
# them it is null with source "unknown", never an invented number (W22).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
source "$ROOT/.claude/scripts/lib/sidecar_common.sh"
SC_LEDGER=""
SC_LEDGER_DEFAULT="$TMP/default.jsonl"
SC_WORKDIR="$ROOT"
SC_EFFORT="high"
SC_MODEL="sonnet"
SC_SCHEMA_FILE=""
SC_LABEL="config-proof"
SC_DISCLOSURE="full"
SC_TRANSPORT="anthropic"
SC_COST_MODEL="plan-quota"
SC_REGISTRY_CLI="$ROOT/.claude/tools/model_registry.py"
SC_RECORD_PARSER="claude"
RAW='{"type":"result","subtype":"success","result":"ok","usage":{"input_tokens":1,"output_tokens":1}}'

# 1. a proxied-style run: every knob set
SC_RECORD="$TMP/set.record.json"
SC_TOOL_FRAME="read-only"; SC_PERM_MODE="bypassPermissions"; SC_MAX_OUTPUT="16000"; SC_CONTEXT_TOKENS="256000"; SC_STALL_SEC="2700"; SC_WORK_PRIORITY="tools"
SC_RESUMED_FROM="0f0e0d0c-0000-4000-8000-000000000001"
sc_write_record "$RAW" 0
# 2. an anthropic-style run: no output cap passed to the child, no resume, a pin no registry row lists
SC_RECORD="$TMP/unset.record.json"; SC_MODEL="claude-unlisted-fixture"
SC_TOOL_FRAME=""; SC_PERM_MODE="auto"; SC_MAX_OUTPUT=""; SC_CONTEXT_TOKENS=""; SC_STALL_SEC="900"; SC_RESUMED_FROM=""; SC_WORK_PRIORITY=""
sc_write_record "$RAW" 0
# 3. an anthropic 1M pin: the window is the model's own
SC_RECORD="$TMP/onem.record.json"; SC_MODEL="opus[1m]"
sc_write_record "$RAW" 0
# 4. no declared window, but the registry row states one
SC_RECORD="$TMP/reg.record.json"; SC_MODEL="luna"
sc_write_record "$RAW" 0
# 5. the context-ceiling warning reads the same window: a [1m] arm whose peak request passed 80% of 1M
SC_RECORD="$TMP/ceil.record.json"; SC_MODEL="opus[1m]"
sc_write_record '{"type":"assistant","message":{"usage":{"input_tokens":850000}}}
{"type":"system","subtype":"compact_boundary","compact_metadata":{"trigger":"auto","pre_tokens":202008,"post_tokens":14822,"duration_ms":66330}}
'"$RAW" 0
# 6. toolVersions comes from the login the transport ran on: a planted registry points `anthropic` at a
#    planted login holding semantic-search 2.69.0 (user scope), and `anthropic-secondary` at one without it
mkdir -p "$TMP/login/plugins" "$TMP/bare/plugins"
python3 - "$ROOT/.claude/tools" "$TMP" <<'PY'
import json, os, sys
sys.path.insert(0, sys.argv[1])
import model_registry
tmp = sys.argv[2]
data = model_registry.load()
data["transports"]["anthropic"]["configDir"] = os.path.join(tmp, "login")
data["transports"]["anthropic-secondary"]["configDir"] = os.path.join(tmp, "bare")
json.dump(data, open(os.path.join(tmp, "registry.json"), "w", encoding="utf-8"))
json.dump({"version": 2, "plugins": {"semantic-search@dreb": [
    {"scope": "project", "projectPath": "elsewhere", "version": "2.14.0", "installPath": "p"},
    {"scope": "user", "version": "2.69.0", "gitCommitSha": "9f8e7d6c", "installPath": "u"}]}},
    open(os.path.join(tmp, "login", "plugins", "installed_plugins.json"), "w", encoding="utf-8"))
json.dump({"version": 2, "plugins": {}}, open(os.path.join(tmp, "bare", "plugins", "installed_plugins.json"), "w", encoding="utf-8"))
PY
export HARNESS_MODEL_REGISTRY="$TMP/registry.json"
SC_RECORD="$TMP/tools.record.json"; SC_MODEL="sonnet"; SC_TRANSPORT="anthropic"
sc_write_record "$RAW" 0
SC_RECORD="$TMP/bare.record.json"; SC_TRANSPORT="anthropic-secondary"
sc_write_record "$RAW" 0
unset HARNESS_MODEL_REGISTRY
SC_TRANSPORT="anthropic"
# 7. the rail stamp's import fails (a hook mid-edit): the record still publishes, rail null.
#    2026-10-08: an ImportError here discarded a finished run's record and left it unratable.
mkdir -p "$TMP/plant/tools" "$TMP/plant/hooks"
cp "$ROOT"/.claude/tools/*.py "$TMP/plant/tools/"
cp "$ROOT"/.claude/hooks/*.py "$TMP/plant/hooks/"
printf 'raise ImportError("planted: hook mid-edit")\n' > "$TMP/plant/hooks/sidecar_recompact_reprompt.py"
printf 'brief\n' > "$TMP/brief.md"
export HARNESS_MODEL_REGISTRY="$ROOT/.claude/reference/external_models.json"
SC_RECORD="$TMP/rail.record.json"; SC_MODEL="sonnet"; SC_PROMPT_FILE="$TMP/brief.md"
SC_REGISTRY_CLI="$TMP/plant/tools/model_registry.py"
sc_write_record "$RAW" 0 2>"$TMP/rail.err" || true
[ -s "$TMP/rail.record.json" ] || { echo "rail record missing; writer stderr:"; tail -3 "$TMP/rail.err"; }
SC_REGISTRY_CLI="$ROOT/.claude/tools/model_registry.py"; SC_PROMPT_FILE=""
unset HARNESS_MODEL_REGISTRY

python3 - "$TMP/set.record.json" "$TMP/unset.record.json" "$TMP/onem.record.json" "$TMP/reg.record.json" "$ROOT/.claude/tools" "$TMP/ceil.record.json" "$TMP/tools.record.json" "$TMP/bare.record.json" "$TMP/rail.record.json" <<'PY'
import json, os, sys
r = json.load(open(sys.argv[9], encoding="utf-8")) if os.path.exists(sys.argv[9]) and os.path.getsize(sys.argv[9]) else {}
f = json.load(open(sys.argv[7], encoding="utf-8"))
g = json.load(open(sys.argv[8], encoding="utf-8"))
a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
c = json.load(open(sys.argv[3], encoding="utf-8"))
d = json.load(open(sys.argv[4], encoding="utf-8"))
sys.path.insert(0, sys.argv[5])
import model_registry
luna = model_registry.resolve("luna")["limits"]["contextTokens"]
e = json.load(open(sys.argv[6], encoding="utf-8"))
checks = [
    ("toolFrame recorded, null when undeclared", a.get("toolFrame") == "read-only" and b.get("toolFrame") is None),
    ("permissionMode recorded", a.get("permissionMode") == "bypassPermissions" and b.get("permissionMode") == "auto"),
    ("maxOutputTokens recorded as an integer", a.get("maxOutputTokens") == 16000),
    ("maxOutputTokens present and null when the child keeps the client default", "maxOutputTokens" in b and b["maxOutputTokens"] is None),
    ("contextWindow recorded", a.get("contextWindow") == 256000 and "contextWindow" in b),
    ("a declared window is recorded with its source", a.get("contextWindowSource") == "launcher-declared"),
    ("a [1m] pin records 1000000 from the model suffix",
     c.get("contextWindow") == 1000000 and c.get("contextWindowSource") == "model-suffix"),
    ("with nothing declared, the registry row's contextTokens is recorded",
     d.get("contextWindow") == luna and d.get("contextWindowSource") == "registry"),
    ("a pin with no suffix and no registry row records null, source unknown",
     b.get("contextWindow") is None and b.get("contextWindowSource") == "unknown"),
    ("the context-ceiling warning uses the recorded window (a [1m] arm at 850k of 1M warns)",
     e.get("peakRequestTokens") == 850000 and e.get("contextCeilingWarning") is True),
    ("a stream's compactions are counted; a record with no stream claims none",
     e.get("compactions") == 1 and "compactions" not in d),
    ("a stream's compaction wall time is summed from its boundaries", e.get("compactionMs") == 66330),
    ("stall watchdog limit recorded", a.get("stallSec") == 2700 and b.get("stallSec") == 900),
    ("workPriority names what ran below normal, null when nothing did",
     a.get("workPriority") == "tools" and "workPriority" in b and b["workPriority"] is None),
    ("resumedFrom names the resumed session", a.get("resumedFrom") == "0f0e0d0c-0000-4000-8000-000000000001"),
    ("resumedFrom present and null on a fresh run", "resumedFrom" in b and b["resumedFrom"] is None),
    ("toolVersions.semanticSearch is the user-scope build identity (revision) installed on the transport's login",
     f.get("toolVersions") == {"semanticSearch": "9f8e7d6c"}),
    ("toolVersions.semanticSearch is null on a login without the plugin, never another login's version",
     "toolVersions" in g and g["toolVersions"] == {"semanticSearch": None}),
    ("a failed rail-stamp import still publishes the record, with compactionRail null",
     r.get("label") == "config-proof" and "compactionRail" in r and r["compactionRail"] is None),
]
bad = 0
for name, ok in checks:
    print(("ok   " if ok else "FAIL ") + name)
    bad += not ok
if bad:
    print("records:", {k: (r.get("contextWindow"), r.get("contextWindowSource")) for k, r in zip("abcd", (a, b, c, d))})
    raise SystemExit(1)
print("PASS test_sidecar_record_config_fields")
PY
