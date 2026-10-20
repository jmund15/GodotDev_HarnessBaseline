#!/usr/bin/env bash
# Proof for the Console-credit launcher's credential handling (S2). With fake keys planted in a fake
# env file, one full launch is grepped across every surface it can reach: the child's own
# environment, the run record, the ledger row, and the argv of every `env` the launcher ran. The
# key VALUE may appear in exactly one place -- ANTHROPIC_API_KEY in the child's own environment --
# and every ANTHROPIC_CONSOLE_ name may appear in none. The registry, both config dirs, the capacity
# cache, the env file and the CLI are planted: no model turn runs and no credit is spent.
#
# `env` is shimmed as well as `claude`: the launcher's other child variables travel as `env`'s own
# argv, so argv is the surface where a key would leak to `ps`, and only a shim sees it.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONSOLE="$ROOT/.claude/scripts/anthropic_console_sidecar.sh"
CONSOLE_SECOND="$ROOT/.claude/scripts/anthropic_console_secondary_sidecar.sh"
REAL_ENV="/usr/bin/env"

KEY_PRIMARY="sk-ant-fake-primary-abcdefghijklmnop01"
KEY_SECONDARY="sk-ant-fake-secondary-zyxwvutsrqponml02"
WORKSPACE_ID="wrkspc_01FAKEWORKSPACEIDENTIFIER"
DECOY_KEY="sk-ant-fake-decoy-0987654321"

NATIVE="$(python3 - "$ROOT/.claude" <<'PY'
import datetime, json, os, sys, tempfile
sys.path.insert(0, os.path.join(sys.argv[1], "tools"))
import model_registry
tmp = tempfile.mkdtemp(prefix="anthropic_console_creds_")
data = model_registry.load()
home = os.path.join(tmp, "home")
os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
data["transports"]["anthropic"]["configDir"] = "~/.claude"
# The credit cycle is planted relative to now rather than left as the registry's owner-entered
# dates, so this proof cannot expire: the marker's cycleEnd is asserted against these.
today = datetime.datetime.now(datetime.timezone.utc).date()
cycles = {name: {"cycleStart": str(today - datetime.timedelta(days=5)),
                 "cycleEnd": str(today + datetime.timedelta(days=15)), "grantUSD": 200.0}
          for name in ("anthropic-console", "anthropic-console-secondary")}
for name, cycle in cycles.items():
    data["transports"][name]["configDir"] = os.path.join(tmp, name)
    data["transports"][name]["credentialFile"] = os.path.join(tmp, "env.ai-worker.cmd")
    data["transports"][name]["status"] = {"state": "available"}
    data["transports"][name]["credit"] = cycle
with open(os.path.join(tmp, "external_models.json"), "w", encoding="utf-8", newline="\n") as fh:
    json.dump(data, fh)
with open(os.path.join(tmp, "cycles.json"), "w", encoding="utf-8", newline="\n") as fh:
    json.dump({name: cycle["cycleEnd"] for name, cycle in cycles.items()}, fh)
print(tmp.replace("\\", "/"))
PY
)"
trap 'rm -rf "$NATIVE"' EXIT

ENV_FILE="$NATIVE/env.ai-worker.cmd"
HOME_DIR="$NATIVE/home"
RECORD="$NATIVE/run.record.json"
LEDGER="$HOME_DIR/.claude/sidecar_ledger.jsonl"
MARKER="$HOME_DIR/.claude/console_credit_exhausted.json"
STUB_DIR="$NATIVE/stub"
mkdir -p "$STUB_DIR"
SHIM_LOG="$STUB_DIR/env-argv.log"
printf 'set ANTHROPIC_CONSOLE_KEY_PRIMARY=%s\r\nset ANTHROPIC_CONSOLE_KEY_SECONDARY=%s\r\nset ANTHROPIC_CONSOLE_WORKSPACE_ID_PRIMARY=%s\r\nset DEEPSEEK_API_KEY=%s\r\n' \
  "$KEY_PRIMARY" "$KEY_SECONDARY" "$WORKSPACE_ID" "$DECOY_KEY" > "$ENV_FILE"

export HARNESS_MODEL_REGISTRY="$NATIVE/external_models.json"
export HOME="$HOME_DIR" USERPROFILE="$HOME_DIR"
export PROVIDER_CAPACITY_CACHE="$NATIVE/cache.json"
export CLAUDE_BIN="$STUB_DIR/claude"
export SC_MEMORY_CHECKED=1
export STUB_ENV_OUT="$NATIVE/child.json"
unset CLAUDE_CODE_TRANSPORT CLAUDE_CODE_SIDECAR ANTHROPIC_BASE_URL SC_PLAN CLAUDE_CONFIG_DIR SC_LAUNCHER_DIR

python3 - "$PROVIDER_CAPACITY_CACHE" <<'PY'
import json, sys, time
now = time.time()
def reading(plan):
    win = {"name": "seven_day", "usedPercent": 20.0, "resetsAt": now + 3 * 86400, "windowDurationMins": 10080,
           "pressure": 1.0, "band": "Surplus", "runoutRatio": None}
    return {"schemaVersion": 1, "transport": plan, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "status": "available",
            "error": None, "liveFailure": None, "balance": None,
            "quota": {"windows": [win], "dispatchBand": "Surplus", "routingBand": "Surplus",
                      "routingWindow": "seven_day", "bindingWindow": "seven_day", "credits": None,
                      "scopes": []}}
json.dump({"anthropic": reading("anthropic"), "anthropic-console": reading("anthropic-console"),
           "anthropic-console-secondary": reading("anthropic-console-secondary")}, open(sys.argv[1], "w"))
PY

# The CLI shim records its own argv and its WHOLE environment (names and values).
cat > "$CLAUDE_BIN" <<'STUB'
#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys
json.dump({"argv": sys.argv[1:], "env": dict(os.environ)}, open(os.environ["STUB_ENV_OUT"], "w"))
PY
cat >/dev/null
echo '{"type":"result","subtype":"success","is_error":false,"result":"OK","session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}'
STUB
chmod +x "$CLAUDE_BIN"
MODEL_ID="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import model_registry as m
print(next(r["id"] for r in m.available_models() if r["transport"] == "anthropic"))' "$ROOT/.claude/tools")"
sed -i "s/__MODEL__/$MODEL_ID/" "$CLAUDE_BIN"

# The `env` shim: one argv element per line, then the real binary. Shebangs elsewhere resolve
# /usr/bin/env by absolute path, so this cannot recurse. It logs beside itself, so the proof reads a
# path no launcher environment can influence.
#
# The PATH entry must be the POSIX spelling of the stub dir: Git Bash does not resolve a bare `env`
# through a `C:/...` entry, and a shim that is never invoked makes every argv claim vacuous.
STUB_ON_PATH="$(cygpath -u "$STUB_DIR" 2>/dev/null || printf '%s' "$STUB_DIR")"
cat > "$STUB_DIR/env" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" >> "$STUB_DIR/env-argv.log"
exec $REAL_ENV "\$@"
EOF
chmod +x "$STUB_DIR/env"
export PATH="$STUB_ON_PATH:$PATH"
# The instrument proves itself before it is trusted: an empty log cannot distinguish "no key on the
# command line" from "the shim never ran".
rm -f "$SHIM_LOG"
env SHIM_SELF_TEST=1 /usr/bin/true
[ -f "$SHIM_LOG" ] && grep -qx 'SHIM_SELF_TEST=1' "$SHIM_LOG" || {
  echo "FAIL the planted env shim is not the 'env' the launcher would run"; exit 1; }
rm -f "$SHIM_LOG"

printf 'Reply OK.\n' > "$NATIVE/prompt.md"

pass=0; fail=0
check() { # <name> <ok 0|1> <detail>
  if [ "$2" = 0 ]; then pass=$((pass + 1)); echo "ok   $1"; else fail=$((fail + 1)); echo "FAIL $1   [$3]"; fi
}

PARENT_AUTH_LEAK=parent-auth-leak
# The dispatch-doctrine gate is not under test: a launch from a live session is denied until that session
# loaded the doctrine, so the proof launches as its own session with the ladder and skill marked loaded.
HOOK_STATE="$(mktemp -d)"
printf '{"model_ladder_ready": true, "fired_since_compaction": {"skill_in_context:orchestration": true}}' \
  > "$HOOK_STATE/consolep.json"
launch() { # <launcher> <record>; the parent carries a leak of every kind the scrub must remove
  ( cd "$ROOT" && \
    HARNESS_HOOK_STATE_DIR="$HOOK_STATE" CLAUDE_CODE_SESSION_ID="consolep-0000-0000-0000-000000000000" \
    ANTHROPIC_BASE_URL="https://parent.invalid/leak" ANTHROPIC_AUTH_TOKEN="$PARENT_AUTH_LEAK" \
    ANTHROPIC_CONSOLE_KEY_LEAK="parent-console-leak-sentinel" \
    ANTHROPIC_CONSOLE_WORKSPACE_ID_PRIMARY="$WORKSPACE_ID" \
    CLAUDE_CONFIG_DIR="$HOME_DIR/.claude" CLAUDE_CODE_TRANSPORT="codex" \
    bash "$1" -m "$MODEL_ID" -e low -G review -D bare -t Read -f "$NATIVE/prompt.md" \
      -R "$2" -l console-creds -d "$ROOT" ) >"$NATIVE/launch.out" 2>"$NATIVE/launch.err"
}

scan() { # <record> <expected key> <other key> -> "name<TAB>ok<TAB>detail" lines
  python3 - "$1" "$2" "$3" "$STUB_ENV_OUT" "$SHIM_LOG" "$LEDGER" <<'PY'
import json, os, re, sys
record_path, key, other_key, dump_path, argv_path, ledger = sys.argv[1:7]
dump = json.load(open(dump_path, encoding="utf-8")) if os.path.exists(dump_path) else {"argv": [], "env": {}}
env = dump.get("env") or {}
text = lambda p: open(p, encoding="utf-8").read() if os.path.exists(p) else ""
record, argv_log, ledger_text = text(record_path), text(argv_path), text(ledger)
bare_env = json.dumps({k: v for k, v in env.items() if k != "ANTHROPIC_API_KEY"})
argv_lines = [line for line in argv_log.splitlines() if line]
# sc_scrub_env names every ANTHROPIC* variable the CALLER set on `env -u <name>`, by design (the same
# operand appears today for ANTHROPIC_API_KEY). That is a variable NAME, never a value, so it is the
# one permitted appearance; anything else carrying the prefix on argv is a finding.
scrub_operand = lambda i: (i > 0 and argv_lines[i - 1] == "-u"
                           and re.fullmatch(r"ANTHROPIC_CONSOLE_[A-Z0-9_]+", argv_lines[i]) is not None)
stray_console = [line for i, line in enumerate(argv_lines) if "ANTHROPIC_CONSOLE_" in line
                 and not scrub_operand(i)]
rows = [
    ("the child authenticates with the plan's key and nothing else in its environment does",
     [("ANTHROPIC_API_KEY", env.get("ANTHROPIC_API_KEY") != key),
      ("any other place in the child env", key in bare_env),
      ("the child env was not captured", not env)]),
    ("the key value reaches no other surface: not the record, not the ledger, not any argv",
     [("record missing", not record), ("ledger row missing", not ledger_text),
      ("record", key in record), ("ledger row", key in ledger_text),
      ("`env` argv", key in argv_log), ("child argv", any(key in a for a in dump.get("argv") or []))]),
    ("the `env` shim is a live instrument: it logged the launcher's own child argv",
     [("`env` argv log is empty", not argv_log),
      ("no CLAUDE_CODE_ENTRYPOINT=cli in the logged argv", "CLAUDE_CODE_ENTRYPOINT=cli" not in argv_log)]),
    ("no ANTHROPIC_CONSOLE_ name leaves the child's environment, the record, the ledger or any argv "
     "but the scrub's own `env -u <name>` operands",
     [("child env names", sorted(n for n in env if n.startswith("ANTHROPIC_CONSOLE_"))),
      ("record", "ANTHROPIC_CONSOLE_" in record),
      ("ledger", "ANTHROPIC_CONSOLE_" in ledger_text),
      ("stray `env` argv lines", stray_console),
      ("the credential variable's own name on argv",
       [line for line in argv_lines if "ANTHROPIC_CONSOLE_KEY_PRIMARY" in line
        or "ANTHROPIC_CONSOLE_KEY_SECONDARY" in line])]),
    ("the second plan's key and the decoy key are absent everywhere",
     [("other key", other_key in record or other_key in ledger_text or other_key in json.dumps(dump))]),
    ("the parent's scrubbed ANTHROPIC*/CLAUDE* values never reach the child",
     [("leaked values", sorted(v for v in ("https://parent.invalid/leak", "parent-auth-leak",
                                           "parent-console-leak-sentinel") if v in env.values())),
      ("parent CLAUDE_CODE_TRANSPORT=codex", env.get("CLAUDE_CODE_TRANSPORT") == "codex")]),
]
for name, pairs in rows:
    bad = [(k, v) for k, v in pairs if v]
    print("%s\t%s\t%s" % (name, 0 if not bad else 1, str(bad)[:300].replace("\t", " ")))
PY
}

run_scan() { # <label> <record> <expected key> <other key>
  local rows; rows="$(scan "$2" "$3" "$4")"
  while IFS=$'\t' read -r n o d; do
    [ -n "$n" ] || continue
    check "$1: $n" "$o" "$d $(tail -2 "$NATIVE/launch.err")"
  done <<< "$rows"
  [ "$(printf '%s\n' "$rows" | grep -c .)" = 6 ]
  check "$1: all six credential proofs reported (none silently skipped)" $? "$(printf '%s' "$rows" | head -c 200)"
}

rm -f "$RECORD" "$LEDGER" "$SHIM_LOG" "$STUB_ENV_OUT"
launch "$CONSOLE" "$RECORD"
rc=$?
[ "$rc" = 0 ]
check "a Console launch completes through the planted shim" $? "rc=$rc $(tail -3 "$NATIVE/launch.err")"
run_scan "primary" "$RECORD" "$KEY_PRIMARY" "$KEY_SECONDARY"

# R1's negative control: a clean child result must mark nothing. Without this case, a launcher that
# marked the plan on every launch would satisfy the marker case below.
[ ! -e "$MARKER" ]
check "a normal Console result writes no credit-exhausted marker" $? "$(ls "$HOME_DIR/.claude" 2>/dev/null)"

# A parent ANTHROPIC_CONSOLE_* value must be scrubbed, not merely unset by name: the launch above
# exported three of them, and the run_scan case reads the child env that resulted.
out="$(python3 - "$STUB_ENV_OUT" <<'PY'
import json, os, sys
env = (json.load(open(sys.argv[1], encoding="utf-8")).get("env") or {}) if os.path.exists(sys.argv[1]) else {}
print(json.dumps(sorted(n for n in env if n.startswith("ANTHROPIC_CONSOLE_"))))
PY
)"
[ "$out" = "[]" ]
check "sc_scrub_env leaves no ANTHROPIC_CONSOLE_* the parent set in the child" $? "$out"

rm -f "$RECORD" "$LEDGER" "$SHIM_LOG" "$STUB_ENV_OUT"
launch "$CONSOLE_SECOND" "$RECORD"
rc=$?
[ "$rc" = 0 ]
check "the second Console plan's launch completes through the planted shim" $? "rc=$rc $(tail -3 "$NATIVE/launch.err")"
run_scan "secondary" "$RECORD" "$KEY_SECONDARY" "$KEY_PRIMARY"

# ---- R1: the child result is wired to the credit-exhausted marker ---------------------------
# The endpoint can refuse a spent credit while the ledger still shows dollars left, and C7's error
# reaches the launcher only on the child's own result. The marker is the plan's exhausted fact for
# the rest of the cycle; before this wiring the probe's --child-result mode had no caller at all.
STUB_EXHAUSTED="$STUB_DIR/claude-exhausted"
cat > "$STUB_EXHAUSTED" <<'STUB'
#!/usr/bin/env bash
cat >/dev/null
echo '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"Credit balance is too low","session_id":"stub-sid"}'
exit 1
STUB
chmod +x "$STUB_EXHAUSTED"

marker_entries() { # "<plan>=<cycleEnd>" per marked plan, sorted; empty when no marker file
  python3 - "$MARKER" <<'PY'
import json, os, sys
path = sys.argv[1]
blob = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
print(" ".join("%s=%s" % (name, (entry or {}).get("cycleEnd"))
               for name, entry in sorted(blob.items())))
PY
}
cycle_end_of() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' "$NATIVE/cycles.json" "$1"; }

rm -f "$MARKER" "$RECORD"
( export CLAUDE_BIN="$STUB_EXHAUSTED"
  launch "$CONSOLE" "$RECORD" ) >/dev/null 2>"$NATIVE/exhausted.err"
rc=$?
[ -s "$RECORD" ]
check "the credit-exhausted launch still writes its run record (the marker hook is after it in the launcher)" $? "rc=$rc $(tail -2 "$NATIVE/exhausted.err")"
entries="$(marker_entries)"
[ "$entries" = "anthropic-console=$(cycle_end_of anthropic-console)" ]
check "a Console child result carrying the credit error marks that plan for the cycle the registry names" $? "$entries"

# The SECOND plan's own launch, so a launcher that marked a hardcoded default plan instead of the
# plan it is running fails here. The marker is removed first, so its single entry is the proof.
rm -f "$MARKER" "$RECORD"
( export CLAUDE_BIN="$STUB_EXHAUSTED"
  launch "$CONSOLE_SECOND" "$RECORD" ) >/dev/null 2>"$NATIVE/exhausted2.err"
entries="$(marker_entries)"
[ "$entries" = "anthropic-console-secondary=$(cycle_end_of anthropic-console-secondary)" ]
check "...and the marker names the plan that ran, not a default one" $? "$entries"

python3 - "$NATIVE/launch.out" "$NATIVE/launch.err" "$NATIVE/prompt.md" "$KEY_PRIMARY" "$KEY_SECONDARY" <<'PY'
import sys
key1, key2 = sys.argv[4], sys.argv[5]
bad = [p for p in sys.argv[1:4] if key1 in open(p, encoding="utf-8", errors="replace").read()
       or key2 in open(p, encoding="utf-8", errors="replace").read()]
sys.exit(1 if bad else 0)
PY
check "no key value appears in either launch's stdout, stderr or prompt file" $? ""

echo
echo "$pass/$((pass + fail)) passed"
[ "$fail" = 0 ]
