#!/usr/bin/env bash
# Proof for the per-plan Anthropic launcher: the invoked launcher path names the paying plan, whatever
# the caller's CLAUDE_CONFIG_DIR or SC_PLAN say, and sc_gate_band reads the calling session's PLAN.
# The registry, both login dirs, the capacity cache and the claude CLI are planted; no model turn runs.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE="$ROOT/.claude/scripts/anthropic_sidecar.sh"
WRAP="$ROOT/.claude/scripts/anthropic_secondary_sidecar.sh"
CONSOLE="$ROOT/.claude/scripts/anthropic_console_sidecar.sh"
CONSOLE_SECOND="$ROOT/.claude/scripts/anthropic_console_secondary_sidecar.sh"
FAKE_KEY_PRIMARY="sk-ant-fake-console-primary-9012345678"
FAKE_KEY_SECONDARY="sk-ant-fake-console-secondary-1234567890"

NATIVE="$(python3 - "$ROOT/.claude" <<'PY'
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(sys.argv[1], "tools"))
import model_registry
tmp = tempfile.mkdtemp(prefix="anthropic_plan_launcher_")
data = model_registry.load()
# The primary login is `~/.claude` under a planted home, so the default-login branch runs.
for name, sub, declared in (("anthropic", os.path.join("home", ".claude"), "~/.claude"),
                            ("anthropic-secondary", "secondary", None)):
    d = os.path.join(tmp, sub)
    os.makedirs(d)
    with open(os.path.join(d, ".credentials.json"), "w", encoding="utf-8") as fh:
        json.dump({"claudeAiOauth": {"accessToken": "planted-" + name}}, fh)
    data["transports"][name]["configDir"] = declared or d
    projects = os.path.join(d, "projects", "stub")
    os.makedirs(projects)
    with open(os.path.join(projects, "stub-sid.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 1}}}) + "\n")
data["transports"]["anthropic-secondary"]["status"] = {"state": "available"}
# The Console plans keep a login-free configDir the launcher must CREATE, and read their key from a
# planted env file. The fixture sets their status live whatever the shipped registry says.
for name in ("anthropic-console", "anthropic-console-secondary"):
    data["transports"][name]["configDir"] = os.path.join(tmp, name)
    data["transports"][name]["credentialFile"] = os.path.join(tmp, "env.ai-worker.cmd")
    data["transports"][name]["status"] = {"state": "available"}
with open(os.path.join(tmp, "external_models.json"), "w", encoding="utf-8", newline="\n") as fh:
    json.dump(data, fh)
print(tmp.replace("\\", "/"))
PY
)"
trap 'rm -rf "$NATIVE"' EXIT

# A fresh reading per plan; `band` names each plan's routing band, `exhausted` marks a spent plan.
plant_cache() { # <primary band|exhausted> <secondary band|exhausted>
  python3 - "$NATIVE/cache.json" "$1" "$2" <<'PY'
import json, sys, time
now = time.time()
def reading(plan, band):
    status = "exhausted" if band == "exhausted" else "available"
    band = "Hot" if band == "exhausted" else band
    win = {"name": "seven_day", "usedPercent": 50.0, "resetsAt": now + 3 * 86400, "windowDurationMins": 10080,
           "pressure": 1.0, "band": band, "runoutRatio": None}
    return {"schemaVersion": 1, "transport": plan, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "status": status,
            "error": None, "liveFailure": None, "balance": None,
            "quota": {"windows": [win], "dispatchBand": "Exhausted" if status == "exhausted" else band,
                      "routingBand": band, "routingWindow": "seven_day", "bindingWindow": "seven_day",
                      "credits": None, "scopes": []}}
json.dump({"anthropic": reading("anthropic", sys.argv[2]),
           "anthropic-secondary": reading("anthropic-secondary", sys.argv[3]),
           "anthropic-console": reading("anthropic-console", sys.argv[2]),
           "anthropic-console-secondary": reading("anthropic-console-secondary", sys.argv[3])},
          open(sys.argv[1], "w"))
PY
}

export HARNESS_MODEL_REGISTRY="$NATIVE/external_models.json"
export HOME="$NATIVE/home" USERPROFILE="$NATIVE/home"
PRIMARY="$NATIVE/home/.claude"
export PROVIDER_CAPACITY_CACHE="$NATIVE/cache.json"
export CLAUDE_BIN="$NATIVE/claude-stub"
export SC_MEMORY_CHECKED=1  # the planted client allocates no model; machine pressure is not this oracle
export SIDECAR_WATCH_POLL_SEC=0.2  # the launcher polls its child this often (default 10 s); the stub exits at once
unset CLAUDE_CODE_SESSION_ID  # the proof is no session: an invoking session's dispatch-doctrine state must not deny its launches
printf '#!/usr/bin/env bash\necho "{}"\n' > "$CLAUDE_BIN"; chmod +x "$CLAUDE_BIN"
unset CLAUDE_CODE_TRANSPORT CLAUDE_CODE_SIDECAR ANTHROPIC_BASE_URL SC_PLAN CLAUDE_CONFIG_DIR SC_LAUNCHER_DIR
plant_cache Surplus Surplus

pass=0; fail=0
check() { # <name> <ok 0|1> <detail>
  if [ "$2" = 0 ]; then pass=$((pass + 1)); echo "ok   $1"; else fail=$((fail + 1)); echo "FAIL $1   [$3]"; fi
}
cred_dir() { # the directory of the cred= path in a --check line, normalized to forward slashes
  printf '%s' "$1" | sed -n 's/.*cred=\([^ ]*\)\/\.credentials\.json.*/\1/p; s/.*cred=\([^ ]*\)\\\.credentials\.json.*/\1/p' | tr '\\' '/' | head -1
}
same_dir() {
  python3 -c 'import os, sys
n = lambda p: os.path.normcase(os.path.realpath(p))
sys.exit(0 if sys.argv[1] and n(sys.argv[1]) == n(sys.argv[2]) else 1)' "$1" "$2"
}

out="$(bash "$WRAP" --check 2>&1)"
d="$(cred_dir "$out")"
same_dir "$d" "$NATIVE/secondary"; check "the wrapper's --check reads the secondary login's credential" $? "$out"

out="$(bash "$BASE" --check 2>&1)"
d="$(cred_dir "$out")"
same_dir "$d" "$PRIMARY"; check "the base launcher's --check reads the primary login's credential" $? "$out"

out="$(CLAUDE_CONFIG_DIR="$NATIVE/secondary" bash "$BASE" --check 2>&1)"
d="$(cred_dir "$out")"
same_dir "$d" "$PRIMARY"; check "an exported CLAUDE_CONFIG_DIR does not move the base launcher's plan" $? "$out"

out="$(SC_PLAN=anthropic-secondary bash "$BASE" --check 2>&1)"
d="$(cred_dir "$out")"
same_dir "$d" "$PRIMARY"; check "an inherited SC_PLAN does not move the base launcher's plan" $? "$out"

out="$(SC_PLAN=anthropic bash "$WRAP" --check 2>&1)"
d="$(cred_dir "$out")"
same_dir "$d" "$NATIVE/secondary"; check "an inherited SC_PLAN does not move the wrapper's plan" $? "$out"

out="$(SC_INVOKED_LAUNCHER="$WRAP" bash "$BASE" --check 2>&1)"
d="$(cred_dir "$out")"
same_dir "$d" "$PRIMARY"; check "an inherited SC_INVOKED_LAUNCHER is ignored outside the launcher's own snapshot" $? "$out"

# sc_gate_band reads the calling session's plan: a secondary seat is gated on the secondary's band.
gate() { # <floor> -> exit code of sc_gate_band from a planted secondary seat; stderr to $NATIVE/gate.err
  ( CLAUDE_CONFIG_DIR="$NATIVE/secondary"
    export CLAUDE_CONFIG_DIR
    SC_TRANSPORT="anthropic"
    . "$ROOT/.claude/scripts/lib/sidecar_common.sh"
    SC_MIN_BAND="$1"; SC_ALIAS="planted"; SC_MODEL="planted-model"; SC_AUTHORIZED=0
    sc_gate_band ) 2>"$NATIVE/gate.err" >/dev/null
}
plant_cache exhausted Surplus
gate Surplus; check "a secondary seat passes with the primary exhausted" $? "$(cat "$NATIVE/gate.err")"
plant_cache Surplus Hot
gate Hot; check "a secondary seat at Hot passes a Hot floor while the primary is cool" $? "$(cat "$NATIVE/gate.err")"
plant_cache Hot Surplus
gate Hot; rc=$?
[ "$rc" = 5 ] && grep -q "anthropic-secondary" "$NATIVE/gate.err"
check "a cool secondary seat refuses a Hot floor, naming anthropic-secondary, while the primary is Hot" $? "rc=$rc $(cat "$NATIVE/gate.err")"
( export SC_SEAT_PLAN=anthropic; gate Hot ); rc=$?
[ "$rc" = 5 ] && grep -q "anthropic-secondary" "$NATIVE/gate.err"
check "an inherited SC_SEAT_PLAN does not move the seat sc_gate_band reads" $? "rc=$rc $(cat "$NATIVE/gate.err")"

# A launch through the wrapper: the child runs on the secondary login and names its plan, the
# record and ledger name the plan, and the sync step runs first.
plant_cache Surplus Surplus
export STUB_ENV_OUT="$NATIVE/child.env" SIDECAR_LEDGER_PATH="$NATIVE/ledger.jsonl"
cat > "$CLAUDE_BIN" <<'STUB'
#!/usr/bin/env bash
{ echo "CFG=${CLAUDE_CONFIG_DIR:-<unset>}"; echo "TR=${CLAUDE_CODE_TRANSPORT:-}"; } > "$STUB_ENV_OUT"
cat >/dev/null
echo '{"type":"result","subtype":"success","is_error":false,"result":"OK","session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}'
STUB
# Any available row on the route serves: the proof is about the plan, not the model.
MODEL_ID="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import model_registry as m
print(next(r["id"] for r in m.available_models() if r["transport"] == "anthropic"))' "$ROOT/.claude/tools")"
sed -i "s/__MODEL__/$MODEL_ID/" "$CLAUDE_BIN"
printf 'Reply OK.\n' > "$NATIVE/prompt.md"
printf 'planted primary instructions\n' > "$PRIMARY/ai_worker_model_guide.md"
# bench-review is the bare, review-guard kind; its Read-only profile is the kind minus Glob, Grep and both servers.
READ_ONLY="-Glob,-Grep,-mcp:semantic-search,-mcp:ai-worker"
launch() { # <launcher> <record>; LAUNCH_SHAPE picks the -G kind (bench-review: bare, review guard, no compaction window)
  ( cd "$ROOT" && bash "$1" -m "$MODEL_ID" -e medium -G "${LAUNCH_SHAPE:-bench-review}" -t "$READ_ONLY${LAUNCH_SHAPE:+$([ "$LAUNCH_SHAPE" = author ] && echo ,-Edit,-Write,-Bash)}" -f "$NATIVE/prompt.md" -R "$2" -d "$ROOT" ) \
    >"$NATIVE/launch${LAUNCH_TAG:-}.out" 2>"$NATIVE/launch${LAUNCH_TAG:-}.err"
}
# The wrapper, MCP, detached and base launches are independent (own stub output, stderr and ledger): they run at once.
printf '{"mcpServers": {"planted-srv": {"command": "planted"}}}' > "$NATIVE/home/.claude.json"
( launch "$WRAP" "$NATIVE/secondary.record.json"; echo $? > "$NATIVE/main.rc" ) &
( export STUB_ENV_OUT="$NATIVE/mcp.env" LAUNCH_TAG=.mcp SIDECAR_LEDGER_PATH="$NATIVE/mcp.ledger.jsonl"
  cd "$ROOT" && bash "$WRAP" -m "$MODEL_ID" -e medium -G bench-review -t "$READ_ONLY,+mcp:planted-srv" -f "$NATIVE/prompt.md" -R "$NATIVE/mcp.record.json" -d "$ROOT" >"$NATIVE/launch.mcp.out" 2>"$NATIVE/launch.mcp.err"
  echo $? > "$NATIVE/mcp.rc" ) &
( export STUB_ENV_OUT="$NATIVE/det.env" LAUNCH_TAG=.det SIDECAR_LEDGER_PATH="$NATIVE/det.ledger.jsonl"
  cd "$ROOT" && bash "$WRAP" -m "$MODEL_ID" -e medium -G bench-review -t "$READ_ONLY" -X -f "$NATIVE/prompt.md" -R "$NATIVE/detached.record.json" -d "$ROOT" >"$NATIVE/launch.det.out" 2>"$NATIVE/launch.det.err"
  for _ in $(seq 1 120); do [ -e "$NATIVE/detached.record.json.exit" ] && break; sleep 0.5; done ) &
( export STUB_ENV_OUT="$NATIVE/base.env" LAUNCH_TAG=.base SIDECAR_LEDGER_PATH="$NATIVE/base.ledger.jsonl"
  launch "$BASE" "$NATIVE/primary.record.json"; echo $? > "$NATIVE/base.rc" ) &
wait
rc=$(cat "$NATIVE/main.rc")
STUB_ENV_OUT="$NATIVE/child.env"
child="$(cat "$STUB_ENV_OUT" 2>/dev/null)"
cfg="${child#*CFG=}"; cfg="${cfg%%$'\n'*}"
[ "$rc" = 0 ] && same_dir "$cfg" "$NATIVE/secondary" && printf '%s' "$child" | grep -q '^TR=anthropic-secondary$'
check "a wrapper launch runs its child on the secondary login, named anthropic-secondary" $? "rc=$rc child=[$child] $(tail -3 "$NATIVE/launch.err")"
python3 - "$NATIVE/secondary.record.json" "$NATIVE/ledger.jsonl" <<'PY'
import json, sys
rec = json.load(open(sys.argv[1], encoding="utf-8"))
rows = [json.loads(l) for l in open(sys.argv[2], encoding="utf-8") if l.strip()]
sys.exit(0 if rec.get("transport") == "anthropic-secondary" and rows and rows[-1].get("transport") == "anthropic-secondary" else 1)
PY
check "the wrapper launch's record and ledger row name anthropic-secondary" $? "$(head -c 300 "$NATIVE/secondary.record.json" 2>/dev/null)"
[ "$(cat "$NATIVE/secondary/ai_worker_model_guide.md" 2>/dev/null)" = "planted primary instructions" ]
check "a wrapper launch syncs the secondary login's config first" $? "$(ls -a "$NATIVE/secondary")"

# MCP servers resolve from the default login for every plan: the child gets them through an explicit
# --strict-mcp-config, so a second login without its own registrations still gets the same servers.
wait
check "a wrapper launch resolves MCP servers from the default login" "$(cat "$NATIVE/mcp.rc")" "$(tail -3 "$NATIVE/launch.mcp.err")"

# A detached (-X) wrapper launch runs from a snapshot of the base script and keeps its plan.
grep -q '^TR=anthropic-secondary$' "$NATIVE/det.env" 2>/dev/null && [ "$(cat "$NATIVE/detached.record.json.exit" 2>/dev/null)" = 0 ]
check "a detached wrapper launch keeps the secondary plan through its snapshot" $? "exit=$(cat "$NATIVE/detached.record.json.exit" 2>/dev/null) child=[$(cat "$NATIVE/det.env" 2>/dev/null)] $(tail -2 "$NATIVE/detached.record.json.err" 2>/dev/null)"

rc=$(cat "$NATIVE/base.rc")
child="$(cat "$NATIVE/base.env" 2>/dev/null)"
[ "$rc" = 0 ] && printf '%s' "$child" | grep -q '^CFG=<unset>$' && printf '%s' "$child" | grep -q '^TR=anthropic$'
check "a base launch runs its child with CLAUDE_CONFIG_DIR unset, named anthropic" $? "rc=$rc child=[$child] $(tail -3 "$NATIVE/launch.base.err")"

# Capture the actual client argv/env, not the launcher's registry query. No paid turn.
cat > "$CLAUDE_BIN" <<'STUB'
#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys
json.dump({"argv": sys.argv[1:], "auto": os.getenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW"),
           "max": os.getenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS"),
           "ttl": os.getenv("CLAUDE_CODE_PROMPT_CACHE_TTL")}, open(os.environ["STUB_ENV_OUT"], "w"))
PY
cat >/dev/null
echo '{"type":"result","subtype":"success","is_error":false,"result":"OK","session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}'
STUB
# Plant four rows on the real Anthropic route; preserve the paying-plan and settings machinery.
python3 - "$HARNESS_MODEL_REGISTRY" <<'PY'
import copy, json, sys
p = sys.argv[1]; data = json.load(open(p))
base = next(r for r in data["models"] if r["id"] == "claude-opus-5-5")
for mid, alias, limits in [("claude-window-test", "window-test", {"contextTokens": 1000000, "maxOutputTokens": 128000, "compactAtTokens": 200000}),
                           ("claude-native-test", "native-test", {"contextTokens": 1000000, "maxOutputTokens": 128000}),
                           ("claude-uncertain-test", "uncertain-test", {"contextTokens": 500000}),
                           ("vendor-window-test", "vendor-test", {"contextTokens": 272000, "maxOutputTokens": 64000})]:
    row = copy.deepcopy(base); row.update(id=mid, alias=alias, family=alias, version=mid,
                                         order=max(r["order"] for r in data["models"]) + 1,
                                         limits=limits, longContextTier=None)
    data["models"].append(row)
json.dump(data, open(p, "w"))
PY
export SC_SETTINGS_JSON='{"env":{"KEEP":"yes"},"hooks":{"SessionStart":[{"matcher":"startup","hooks":[{"type":"command","command":"keep-existing","timeout":5}]}]}}'
# The launches are independent (own stub output, stderr and record), so they run at once and report in order.
window_case() { # <launcher> <model> <tag>: writes "<test rc>|<detail>" to $NATIVE/<tag>.res
  local launcher="$1" MODEL_ID="$2" tag="$3" record rc test_rc
  export STUB_ENV_OUT="$NATIVE/$tag.env" LAUNCH_TAG=".$tag" LAUNCH_SHAPE=author
  record="$NATIVE/$MODEL_ID-$(basename "$launcher").record.json"
  launch "$launcher" "$record"; rc=$?
  python3 - "$STUB_ENV_OUT" "$MODEL_ID" "$record" "$NATIVE/launch.$tag.err" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); mid = sys.argv[2]; args = c["argv"]
expected = ("233000", None, mid + "[1m]", 233000) if mid == "claude-window-test" else (
    ("500000", None, mid, None) if mid == "claude-uncertain-test" else (
    ("1000000", None, mid + "[1m]", 1000000) if mid.startswith("claude-") else (None, "272000", mid, 272000)))
assert (c["auto"], c["max"], args[args.index("--model") + 1]) == expected[:3], c
assert c["ttl"] == "5m", c
r = json.load(open(sys.argv[3])); assert r["contextWindow"] == expected[3], r
assert "contextOverride" not in r, r
assert r["contextWindowSource"] == ("unknown" if expected[3] is None else (
    "anthropic-auto-compact" if mid.startswith("claude-") else "launcher-declared")), r
assert (f'% of {expected[3]})' if expected[3] else '(window unknown)') in open(sys.argv[4]).read()
s = json.loads(args[args.index("--settings") + 1]); assert s["env"]["KEEP"] == "yes", s
hooks = s["hooks"]
assert any(x["matcher"] == "startup" for x in hooks["SessionStart"]), s
assert any(x["matcher"] == "compact" and "sidecar_recompact_reprompt.py" in x["hooks"][0]["command"]
           for x in hooks["SessionStart"]), s
assert any("sidecar_recompact_reprompt.py" in x["hooks"][0]["command"] for x in hooks["PreCompact"]), s
assert r["compactionRail"] == 4, r
PY
  test_rc=$?
  printf '%s|rc=%s %s\n' "$test_rc" "$rc" "$(tail -3 "$NATIVE/launch.$tag.err")" > "$NATIVE/$tag.res"
}
n=0; window_cases=()
for MODEL_ID in claude-window-test claude-native-test vendor-window-test claude-uncertain-test; do
  for launcher in "$BASE" "$WRAP"; do
    n=$((n + 1)); window_cases+=("$n|$launcher|$MODEL_ID")
    ( window_case "$launcher" "$MODEL_ID" "w$n" ) &
  done
done
wait
for entry in "${window_cases[@]}"; do
  IFS="|" read -r n launcher MODEL_ID <<< "$entry"
  IFS="|" read -r test_rc detail < "$NATIVE/w$n.res"
  check "$(basename "$launcher") $MODEL_ID enforces its window, records it, and preserves settings hooks" "${test_rc:-1}" "$detail"
done
# `window: "full"` (SIDECAR_FULL_WINDOW=1) opts a job out of the login's autoCompactWindow cap; the default job
# compacts at the smaller of the model's window and that cap. The login is the user settings under HOME/USERPROFILE.
mkdir -p "$HOME/.claude"; printf '{"autoCompactWindow":150000}\n' > "$HOME/.claude/settings.json"
full_window_case() { # <tag> <expected auto-compact window> [env assignment...]: "<rc>|<detail>" to $NATIVE/<tag>.res
  local tag="$1" expected="$2"; shift 2
  ( export STUB_ENV_OUT="$NATIVE/$tag.env" LAUNCH_TAG=".$tag" LAUNCH_SHAPE=author "$@"
    MODEL_ID=claude-window-test launch "$BASE" "$NATIVE/$tag.record.json"; rc=$?
    python3 - "$STUB_ENV_OUT" "$NATIVE/$tag.record.json" "$expected" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); r = json.load(open(sys.argv[2])); expected = sys.argv[3]
assert c["auto"] == expected and c["max"] is None, c
assert r["contextWindow"] == int(expected) and r["contextWindowSource"] == "anthropic-auto-compact", r
PY
    test_rc=$?; [ "$rc" = 0 ] && [ "$test_rc" = 0 ]
    printf '%s|rc=%s %s\n' "$?" "$rc" "$(tail -2 "$NATIVE/launch.$tag.err")" > "$NATIVE/$tag.res" )
}
full_window_case wcap 150000 SIDECAR_FULL_WINDOW=0
IFS="|" read -r ok detail < "$NATIVE/wcap.res"
check "a default job compacts at the login's autoCompactWindow cap" "${ok:-1}" "$detail"
full_window_case wfull 233000 SIDECAR_FULL_WINDOW=1
IFS="|" read -r ok detail < "$NATIVE/wfull.res"
check "a job with window full compacts at the model's own window, not the login cap" "${ok:-1}" "$detail"
rm -f "$HOME/.claude/settings.json"
unset SC_SETTINGS_JSON
MODEL_ID=claude-window-test
override_case() { # <via> <flags>: writes "<ok 0|1>|<detail>" to $NATIVE/o-<via>.res
  local via="$1" flags="$2" record="$NATIVE/override-$1.record.json" rc test_rc
  export STUB_ENV_OUT="$NATIVE/o-$via.env" LAUNCH_TAG=".o-$via"
  if [ "$via" != env ]; then
    bash "$WRAP" -m "$MODEL_ID" -e medium -G bench-review -t "$READ_ONLY" $flags       -f "$NATIVE/prompt.md" -R "$record" -d "$ROOT" >"$NATIVE/launch$LAUNCH_TAG.out" 2>"$NATIVE/launch$LAUNCH_TAG.err"
  else
    SIDECAR_COMPACT_AT_TOKENS=967000 launch "$WRAP" "$record"
  fi
  rc=$?
  python3 - "$STUB_ENV_OUT" "$record" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); r = json.load(open(sys.argv[2]))
assert c["auto"] == "1000000" and c["max"] is None, c
assert r["contextWindow"] == 1000000 and r["contextOverride"] == 1000000, r
PY
  test_rc=$?
  [ "$rc" = 0 ] && [ "$test_rc" = 0 ]
  printf '%s|launch rc=%s\n' "$?" "$rc" > "$NATIVE/o-$via.res"
}
for via in flag env; do
  flags="-K 967000"
  ( override_case "$via" "$flags" ) &
done
wait
for via in flag env; do
  IFS="|" read -r ok detail < "$NATIVE/o-$via.res"
  check "override via $via reaches the child and stamps the record" "${ok:-1}" "$detail"
done

# ---- Console-credit plans: the credential is a KEY, not a login (S1) -------------------------
# The plan's configDir is login-free (nothing is ever logged in there), its credential is a variable
# in a planted env file, and its --check/launch must reach the same OK and the same gates a login
# plan does without any `.credentials.json`.
CONSOLE_DIR="$NATIVE/anthropic-console"
CONSOLE_SECOND_DIR="$NATIVE/anthropic-console-secondary"
CONSOLE_ENV="$NATIVE/env.ai-worker.cmd"
CONSOLE_LEDGER="$NATIVE/home/.claude/sidecar_ledger.jsonl"
console_env() { # <primary value> <secondary value>; CMD format, CRLF, like the real file
  printf 'set ANTHROPIC_CONSOLE_KEY_PRIMARY=%s\r\nset ANTHROPIC_CONSOLE_KEY_SECONDARY=%s\r\n' "$1" "$2" > "$CONSOLE_ENV"
}
plant_cache Surplus Surplus
rm -rf "$CONSOLE_DIR" "$CONSOLE_SECOND_DIR"

console_env '<redacted>' "$FAKE_KEY_SECONDARY"
out="$(bash "$CONSOLE" --check 2>&1)"; rc=$?
[ "$rc" = 3 ] && printf '%s' "$out" | grep -q 'ANTHROPIC_CONSOLE_KEY_PRIMARY' && ! printf '%s' "$out" | grep -q 'redacted'
check "a key plan's --check refuses a placeholder by naming the variable, never its value" $? "rc=$rc [$out]"

rm -f "$CONSOLE_ENV"
out="$(bash "$CONSOLE" --check 2>&1)"; rc=$?
[ "$rc" = 3 ] && printf '%s' "$out" | grep -q 'ANTHROPIC_CONSOLE_KEY_PRIMARY'
check "a key plan's --check exits 3 naming the variable when the env file is missing" $? "rc=$rc [$out]"

console_env "$FAKE_KEY_PRIMARY" "$FAKE_KEY_SECONDARY"
out="$(bash "$CONSOLE" --check 2>&1)"; rc=$?
[ "$rc" = 0 ] && printf '%s' "$out" | grep -q '^OK ' \
  && printf '%s' "$out" | grep -q 'ANTHROPIC_CONSOLE_KEY_PRIMARY' \
  && ! printf '%s' "$out" | grep -q "$FAKE_KEY_PRIMARY" \
  && [ ! -e "$CONSOLE_DIR/.credentials.json" ]
check "a key plan's --check is OK with no .credentials.json, naming its key variable and not the key" $? "rc=$rc [$out]"

out="$(bash "$CONSOLE_SECOND" --check 2>&1)"; rc=$?
[ "$rc" = 0 ] && printf '%s' "$out" | grep -q 'ANTHROPIC_CONSOLE_KEY_SECONDARY' \
  && ! printf '%s' "$out" | grep -q "$FAKE_KEY_PRIMARY"
check "the second Console plan's --check names ITS variable from the same env file" $? "rc=$rc [$out]"

# The child records its own environment and argv. A launch must hand it the key and its own
# login-free config dir, and nothing else the parent carried.
cat > "$CLAUDE_BIN" <<'STUB'
#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys
json.dump({"argv": sys.argv[1:], "env": dict(os.environ)}, open(os.environ["STUB_ENV_OUT"], "w"))
PY
cat >/dev/null
echo '{"type":"result","subtype":"success","is_error":false,"result":"OK","session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}'
STUB
sed -i "s/__MODEL__/$MODEL_ID/" "$CLAUDE_BIN"
rm -f "$STUB_ENV_OUT" "$CONSOLE_LEDGER" "$NATIVE/explicit-ignored.jsonl" "$NATIVE/ledger.jsonl"
PARENT_AUTH_SENTINEL=parent-auth-sentinel
( cd "$ROOT" && ANTHROPIC_BASE_URL="https://parent.invalid/sentinel" ANTHROPIC_AUTH_TOKEN="$PARENT_AUTH_SENTINEL" \
    ANTHROPIC_CONSOLE_KEY_LEAK="parent-console-leak-sentinel" CLAUDE_CONFIG_DIR="$PRIMARY" \
    bash "$CONSOLE" -m "$MODEL_ID" -e medium -G bench-review -t "$READ_ONLY" -f "$NATIVE/prompt.md" \
      -R "$NATIVE/console.record.json" -L "$NATIVE/explicit-ignored.jsonl" -l console-proof -d "$ROOT" ) \
  >"$NATIVE/console.out" 2>"$NATIVE/console.err"
console_rc=$?

console_problems() {
  python3 - "$@" <<'PY'
import json, os, sys
try:
    dump = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    dump = {"argv": [], "missing": "%s: %s" % (type(exc).__name__, exc)}
env, argv = dump.get("env") or {}, dump.get("argv") or []
key, key2, cfgdir = sys.argv[6], sys.argv[7], sys.argv[8]
norm = lambda p: os.path.normcase(os.path.realpath(p))
record = open(sys.argv[2], encoding="utf-8").read() if os.path.exists(sys.argv[2]) else ""
ledger_text = open(sys.argv[3], encoding="utf-8").read() if os.path.exists(sys.argv[3]) else ""
rows = [
    ("the child ran and read ANTHROPIC_API_KEY, this plan's login-free config dir and its plan name",
     [("child env dump", dump.get("missing") or not env),
      ("ANTHROPIC_API_KEY", env.get("ANTHROPIC_API_KEY") != key),
      ("CLAUDE_CONFIG_DIR", norm(env.get("CLAUDE_CONFIG_DIR") or "") != norm(cfgdir)),
      ("CLAUDE_CODE_TRANSPORT", env.get("CLAUDE_CODE_TRANSPORT") != "anthropic-console")]),
    ("no ANTHROPIC_CONSOLE_ or foreign CLAUDE_/ANTHROPIC_ variable from the parent reaches the child",
     [("ANTHROPIC_CONSOLE_ names", sorted(n for n in env if n.startswith("ANTHROPIC_CONSOLE_"))),
      ("other ANTHROPIC* names", sorted(n for n in env if n.startswith("ANTHROPIC")
                                        and n not in ("ANTHROPIC_API_KEY", "ANTHROPIC_SMALL_FAST_MODEL"))),
      ("foreign CLAUDE* names", sorted(n for n in env if n.startswith("CLAUDE")
                                       and not n.startswith("CLAUDE_CODE_") and n != "CLAUDE_CONFIG_DIR")),
      ("parent sentinel values", sorted(v for v in ("https://parent.invalid/sentinel", "parent-auth-sentinel",
                                                    "parent-console-leak-sentinel") if v in env.values()))]),
    ("neither the run record nor the ledger row carries the key or an ANTHROPIC_CONSOLE_ name",
     [("record missing", not record), ("ledger row missing", not ledger_text),
      ("key in record", key in record), ("key in ledger", key in ledger_text),
      ("ANTHROPIC_CONSOLE_ in record", "ANTHROPIC_CONSOLE_" in record),
      ("ANTHROPIC_CONSOLE_ in ledger", "ANTHROPIC_CONSOLE_" in ledger_text),
      ("the second plan's key", key2 in record or key2 in ledger_text)]),
    ("the key is on no command line: no argv element the child received carries it",
     [("child argv hits", [a for a in argv if key in a])]),
]
for name, pairs in rows:
    bad = [(k, v) for k, v in pairs if v]
    print("%s\t%s\t%s" % (name, 0 if not bad else 1, str(bad)[:300].replace("\t", " ")))
PY
}
console_rows="$(console_problems "$STUB_ENV_OUT" "$NATIVE/console.record.json" "$CONSOLE_LEDGER" \
                  "$NATIVE/explicit-ignored.jsonl" "$NATIVE/ledger.jsonl" "$FAKE_KEY_PRIMARY" \
                  "$FAKE_KEY_SECONDARY" "$CONSOLE_DIR")"
while IFS=$'\t' read -r cname cok cdetail; do
  [ -n "$cname" ] || continue
  check "$cname" "$cok" "rc=$console_rc $cdetail $(tail -2 "$NATIVE/console.err")"
done <<< "$console_rows"
[ "$(printf '%s\n' "$console_rows" | grep -c .)" = 4 ]
check "the Console launch reported all four child-env proofs (none silently skipped)" $? \
  "rc=$console_rc [$(printf '%s' "$console_rows" | head -c 200)]"

# D5: a Console launch writes the ONE default ledger and ignores both -L and SIDECAR_LEDGER_PATH.
python3 - "$CONSOLE_LEDGER" "$NATIVE/explicit-ignored.jsonl" "$NATIVE/ledger.jsonl" <<'PY'
import json, os, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()] if os.path.exists(sys.argv[1]) else []
touched = [os.path.exists(p) and bool(open(p, encoding="utf-8").read().strip()) for p in sys.argv[2:4]]
sys.exit(0 if rows and rows[-1].get("transport") == "anthropic-console" and not any(touched) else 1)
PY
check "a Console launch lands in \$HOME/.claude/sidecar_ledger.jsonl, ignoring -L and SIDECAR_LEDGER_PATH" $? \
  "rows=$(wc -l < "$CONSOLE_LEDGER" 2>/dev/null || echo none)"

# Nothing was logged in there and no credential was copied into it, though its config files synced.
[ ! -e "$CONSOLE_DIR/.credentials.json" ] && [ "$(cat "$CONSOLE_DIR/ai_worker_model_guide.md" 2>/dev/null)" = "planted primary instructions" ] \
  && ! grep -rql 'planted-anthropic' "$CONSOLE_DIR" 2>/dev/null
check "a Console launch syncs the plan's config files and copies no credential into its config dir" $? \
  "$(ls -a "$CONSOLE_DIR" 2>&1) $(tail -2 "$NATIVE/console.err")"
printf 'set ANTHROPIC_CONSOLE_KEY_PRIMARY=%s\r\nset ANTHROPIC_CONSOLE_KEY_SECONDARY=%s\r\n' \
  "$FAKE_KEY_PRIMARY" "$FAKE_KEY_SECONDARY" > "$CONSOLE_ENV"

# The key is EXPORTED inside the child's subshell; it never becomes one of `env`'s own arguments.
python3 - "$BASE" <<'PY'
import sys
src = open(sys.argv[1], encoding="utf-8").read()
bad, exported = [], False
for line in src.splitlines():
    text = line.strip()
    if text.startswith("#") or "ANTHROPIC_API_KEY=" not in text:
        continue
    rhs = text.split("ANTHROPIC_API_KEY=", 1)[1]
    if rhs[:1] in ('"', "'") and rhs[1:2] == "$":
        if text.startswith("export "):
            exported = True
        else:
            bad.append(text)
sys.exit(0 if exported and not bad else 1)
PY
check "the launcher exports the key inside the child's subshell, never as an \`env\` argument" $? \
  "$(grep -n 'ANTHROPIC_API_KEY=' "$BASE")"

# D5 also covers the deep probe: its one-turn dispatch is a real spend and belongs in the ledger.
console_before="$(wc -l < "$CONSOLE_LEDGER" 2>/dev/null || echo 0)"
out="$(bash "$CONSOLE" --check --deep 2>&1)"; rc=$?
python3 - "$CONSOLE_LEDGER" "$console_before" "$FAKE_KEY_PRIMARY" <<'PY'
import json, os, sys
text = open(sys.argv[1], encoding="utf-8").read() if os.path.exists(sys.argv[1]) else ""
new = [json.loads(l) for l in text.splitlines() if l.strip()][int(sys.argv[2]):]
ok = (len(new) == 1 and new[0].get("transport") == "anthropic-console"
      and isinstance(new[0].get("costUSD"), (int, float)) and new[0]["costUSD"] > 0
      and "ANTHROPIC_CONSOLE_" not in text and sys.argv[3] not in text)
sys.exit(0 if ok else 1)
PY
check "--check --deep records its one-turn probe as ONE priced ledger row, with no key or variable name" $? \
  "rc=$rc [${out:0:200}]"

echo
echo "$pass/$((pass + fail)) passed"
[ "$fail" = 0 ]
