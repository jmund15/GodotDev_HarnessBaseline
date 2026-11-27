"""The per-plan Anthropic launcher: the invoked launcher path names the paying plan, whatever the
caller's CLAUDE_CONFIG_DIR or SC_PLAN say, and `sc_gate_band` reads the calling session's plan.

The plan placement is `model_registry.plan_for_launcher`'s table and the client window is
`model_registry.anthropic_window`'s, both driven in process here. The live cases spawn the launcher
with the registry, both login dirs, the capacity cache and the claude CLI planted: one per real
boundary, the plan a launcher path selects, the band a seat reads, the child a plan's launch runs
(and its disclosure/settings/ledger), the detach snapshot and a Console plan's key. No model turn runs
and no plan is charged.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from _sidecar_launch import bash_exe, launch_env, plan_of, write_cache, write_stub  # noqa: E402
import model_registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BASE = ".claude/scripts/anthropic_sidecar.sh"
WRAP = ".claude/scripts/anthropic_secondary_sidecar.sh"
CONSOLE = ".claude/scripts/anthropic_console_sidecar.sh"
CONSOLE_SECOND = ".claude/scripts/anthropic_console_secondary_sidecar.sh"
FAKE_KEY_PRIMARY = "sk-ant-fake-console-primary-9012345678"
FAKE_KEY_SECONDARY = "sk-ant-fake-console-secondary-1234567890"
READ_ONLY = "-Glob,-Grep,-mcp:semantic-search,-mcp:ai-worker"


def plant_registry(root):
    """The registry with both login dirs and the Console plans' key file under `root`; returns its path."""
    data = model_registry.load()
    for name, sub, declared in (("anthropic", os.path.join("home", ".claude"), "~/.claude"),
                                ("anthropic-secondary", "secondary", None)):
        directory = os.path.join(root, sub)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, ".credentials.json"), "w", encoding="utf-8") as fh:
            json.dump({"claudeAiOauth": {"accessToken": "planted-" + name}}, fh)
        data["transports"][name]["configDir"] = declared or directory
        projects = os.path.join(directory, "projects", "stub")
        os.makedirs(projects, exist_ok=True)
        with open(os.path.join(projects, "stub-sid.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 1}}}) + "\n")
    data["transports"]["anthropic-secondary"]["status"] = {"state": "available"}
    for name in ("anthropic-console", "anthropic-console-secondary"):
        data["transports"][name]["configDir"] = os.path.join(root, name)
        data["transports"][name]["credentialFile"] = os.path.join(root, "env.ai-worker.cmd")
        data["transports"][name]["status"] = {"state": "available"}
    path = os.path.join(root, "external_models.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    return path


def cache_reading(plan, band):
    """A capacity reading for `plan` at `band` ("Surplus", "Hot", or "exhausted")."""
    now = time.time()
    status = "exhausted" if band == "exhausted" else "available"
    named = "Hot" if status == "exhausted" else band
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    window = {"name": "seven_day", "usedPercent": 50.0, "resetsAt": now + 3 * 86400, "windowDurationMins": 10080,
              "pressure": 1.0, "band": named, "runoutRatio": None}
    return {"schemaVersion": 1, "transport": plan, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": stamp, "status": status, "error": None, "liveFailure": None, "balance": None,
            "quota": {"windows": [window], "dispatchBand": "Exhausted" if status == "exhausted" else named,
                      "routingBand": named, "routingWindow": "seven_day", "bindingWindow": "seven_day",
                      "credits": None, "scopes": []}}


class PlanFixture(unittest.TestCase):
    """The planted registry, logins, capacity cache and claude CLI every live case shares."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="anthropic_plan_")
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.registry = plant_registry(str(self.work))
        guide = self.work / "home" / ".claude" / "ai_worker_model_guide.md"
        guide.parent.mkdir(parents=True, exist_ok=True)
        guide.write_text("planted primary instructions" + chr(10), encoding="utf-8")
        self.cache = self.work / "cache.json"
        self.plant_cache("Surplus", "Surplus")
        self.stub = write_stub(self.work / "bin" / "claude", 'echo "$@" >> "$STUB_LOG"\n'
                               'echo \'{"type":"result","subtype":"success","is_error":false,"result":"OK",'
                               '"session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},'
                               '"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}\'\n')
        self.env = launch_env(self.work, [self.work / "bin"], HARNESS_MODEL_REGISTRY=self.registry,
                              PROVIDER_CAPACITY_CACHE=str(self.cache), CLAUDE_BIN=str(self.stub),
                              STUB_LOG=str(self.work / "argv.log"), SC_MEMORY_CHECKED="1",
                              SIDECAR_WATCH_POLL_SEC="0.2")
        self.prompt = self.work / "prompt.md"
        self.prompt.write_text("Reply OK.\n", encoding="utf-8", newline="\n")
        # A planted roster row on the paying route: no real model name enters the proof.
        data = model_registry.load(self.registry)
        self.model = next(r["id"] for r in data["models"]
                          if r["transport"] == "anthropic" and r.get("lifecycle") == "current")

    def plant_cache(self, primary, secondary):
        data = {"anthropic": cache_reading("anthropic", primary),
                "anthropic-secondary": cache_reading("anthropic-secondary", secondary),
                "anthropic-console": cache_reading("anthropic-console", primary),
                "anthropic-console-secondary": cache_reading("anthropic-console-secondary", secondary)}
        self.cache.write_text(json.dumps(data), encoding="utf-8")

    def run_launcher(self, launcher, args, env=None):
        return subprocess.run([bash_exe(), str(ROOT / launcher), *args], cwd=str(ROOT), env=dict(env or self.env),
                              text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180,
                              stdin=subprocess.DEVNULL)

    def launch(self, launcher, record, env=None, extra=(), kind="bench-review"):
        return self.run_launcher(launcher, ["-m", self.model, "-e", "medium", "-G", kind, "-t", READ_ONLY, *extra,
                                            "-f", str(self.prompt), "-R", str(record), "-d", str(ROOT)], env=env)

    def credential_dir(self, out):
        """The directory of the `cred=` path a `--check` line prints."""
        for token in out.split():
            if token.startswith("cred=") and token.endswith(".credentials.json"):
                return Path(token[len("cred="):-len("/.credentials.json")].replace("\\", "/"))
        return None


class PlanPlacement(PlanFixture):
    def test_each_launcher_path_names_its_plan(self):
        """The registry's table: a launcher path -> plan, config dir, default-login flag, key source."""
        expected = {
            BASE: ("anthropic", "~/.claude", "", ""),
            WRAP: ("anthropic-secondary", str(self.work / "secondary"), "", ""),
            CONSOLE: ("anthropic-console", str(self.work / "anthropic-console"), "ANTHROPIC_CONSOLE_KEY_PRIMARY",
                      str(self.work / "env.ai-worker.cmd")),
            CONSOLE_SECOND: ("anthropic-console-secondary", str(self.work / "anthropic-console-secondary"),
                             "ANTHROPIC_CONSOLE_KEY_SECONDARY", str(self.work / "env.ai-worker.cmd")),
        }
        for launcher, (plan, config_dir, cred_var, cred_file) in expected.items():
            with self.subTest(launcher=launcher):
                got = plan_of(ROOT, launcher, self.env)
                self.assertEqual((got[0], got[3], got[4], got[2]),
                                 (plan, cred_var, cred_file, "1" if plan == "anthropic" else "0"), got)
        with self.assertRaises(AssertionError):
            plan_of(ROOT, ".claude/scripts/no_such_sidecar.sh", self.env)

    def test_a_hostile_environment_cannot_move_the_plan(self):
        """CLAUDE_CONFIG_DIR, SC_PLAN and SC_INVOKED_LAUNCHER are inputs a launcher ignores: its own path pays."""
        hostile = dict(self.env, CLAUDE_CONFIG_DIR=str(self.work / "secondary"),
                       SC_PLAN="anthropic-secondary", SC_INVOKED_LAUNCHER=str(ROOT / WRAP))
        base = self.run_launcher(BASE, ["--check"], env=hostile)
        self.assertEqual(base.returncode, 0, base.stdout + base.stderr)
        self.assertEqual(Path(os.path.normcase(self.credential_dir(base.stdout))),
                         Path(os.path.normcase(str(self.work / "home" / ".claude"))))
        wrap = self.run_launcher(WRAP, ["--check"], env=dict(hostile, SC_PLAN="anthropic"))
        self.assertEqual(wrap.returncode, 0, wrap.stdout + wrap.stderr)
        self.assertEqual(self.credential_dir(wrap.stdout), self.work / "secondary")

    def test_the_band_gate_reads_the_calling_session_plan(self):
        """`sc_gate_band` gates a secondary seat on the secondary login's band; a sourced SC_SEAT_PLAN never
        moves the seat, and a cool seat refuses a Hot floor naming anthropic-secondary."""
        def gate(floor, extra_env=None):
            script = ('SC_TRANSPORT=anthropic; . "%s/.claude/scripts/lib/sidecar_common.sh"; '
                      'SC_MIN_BAND="%s"; SC_ALIAS=planted; SC_MODEL=planted-model; SC_AUTHORIZED=0; sc_gate_band'
                      % (ROOT.as_posix(), floor))
            env = dict(self.env, CLAUDE_CONFIG_DIR=str(self.work / "secondary"), **(extra_env or {}))
            return subprocess.run([bash_exe(), "-c", script], cwd=str(ROOT), env=env, text=True, encoding="utf-8",
                                  errors="replace", capture_output=True, timeout=120, stdin=subprocess.DEVNULL)

        self.plant_cache("exhausted", "Surplus")
        self.assertEqual(gate("Surplus").returncode, 0, "a secondary seat passes with the primary exhausted")
        self.plant_cache("Surplus", "Hot")
        self.assertEqual(gate("Hot").returncode, 0, "a Hot seat passes a Hot floor")
        self.plant_cache("Hot", "Surplus")
        refused = gate("Hot")
        self.assertEqual(refused.returncode, 5)
        self.assertIn("anthropic-secondary", refused.stderr)
        inherited = gate("Hot", {"SC_SEAT_PLAN": "anthropic"})
        self.assertEqual(inherited.returncode, 5, "an inherited SC_SEAT_PLAN is reset at source time")
        self.assertIn("anthropic-secondary", inherited.stderr)


class PlanWindow(PlanFixture):
    def test_the_client_window_contract(self):
        """The registry's window table: a Claude row compacts below its catalog window, an uncertain row reports
        unknown, a vendor row keeps its declared window, and the login's autoCompactWindow caps a default job."""
        rows = {}
        data = model_registry.load(self.registry)
        base = next(r for r in data["models"]
                    if r["id"].lower().startswith("claude-") and (r.get("limits") or {}).get("contextTokens"))
        for mid, limits in (("claude-window-test", {"contextTokens": 1000000, "maxOutputTokens": 128000,
                                                    "compactAtTokens": 200000}),
                            ("claude-native-test", {"contextTokens": 1000000, "maxOutputTokens": 128000}),
                            ("claude-uncertain-test", {"contextTokens": 500000}),
                            ("vendor-window-test", {"contextTokens": 272000, "maxOutputTokens": 64000})):
            rows[mid] = dict(base, id=mid, limits=limits, longContextTier=None)
        cases = {
            "claude-window-test": ((233000, None, "claude-window-test[1m]", 233000, "anthropic-auto-compact"), {}),
            "claude-native-test": ((1000000, None, "claude-native-test[1m]", 1000000, "anthropic-auto-compact"), {}),
            "claude-uncertain-test": ((500000, None, "claude-uncertain-test", None, "unknown"), {}),
            "vendor-window-test": ((None, 272000, "vendor-window-test", 272000, "launcher-declared"), {}),
        }
        # The login cap is a file the caller's home owns: plant an empty one so no host setting leaks in.
        empty_login = self.work / "login-empty"
        empty_login.mkdir()
        previous = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(empty_login)
        for mid, (want, _) in cases.items():
            with self.subTest(model=mid):
                got = model_registry.anthropic_window(rows[mid])
                self.assertEqual((got["autoCompactWindow"], got["maxContextTokens"], got["model"],
                                  got["contextWindow"], got["contextWindowSource"]), want)
        # The login's cap: a default job compacts at the smaller of the model window and the login setting.
        login = self.work / "home" / ".claude"
        (login / "settings.json").write_text('{"autoCompactWindow":150000}', encoding="utf-8")
        os.environ["CLAUDE_CONFIG_DIR"] = str(login)
        try:
            capped = model_registry.anthropic_window(rows["claude-window-test"])
            self.assertEqual((capped["autoCompactWindow"], capped["contextWindow"]), (150000, 150000))
            override = model_registry.anthropic_window(rows["claude-window-test"], compact_override=967000)
            self.assertEqual((override["autoCompactWindow"], override["contextWindow"]), (1000000, 1000000))
        finally:
            if previous is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = previous
            (login / "settings.json").unlink()


class PlanLaunch(PlanFixture):
    def test_a_wrapper_launch_runs_its_child_on_the_secondary_login(self):
        """One wrapper launch: the child's login and plan, the record and ledger row, the config sync, and the
        MCP servers resolved from the default login."""
        (self.work / "home" / ".claude.json").write_text(
            '{"mcpServers": {"planted-srv": {"command": "planted"}}}', encoding="utf-8")
        (self.work / "home" / ".claude" / "ai_worker_model_guide.md").parent.mkdir(parents=True, exist_ok=True)
        (self.work / "home" / ".claude" / "ai_worker_model_guide.md").write_text("planted primary instructions\n",
                                                                                encoding="utf-8")
        child_env = self.work / "child.env"
        stub = write_stub(self.work / "bin" / "claude",
                          'echo "CFG=${CLAUDE_CONFIG_DIR:-<unset>}" > "$STUB_ENV_OUT"\n'
                          'echo "TR=${CLAUDE_CODE_TRANSPORT:-}" >> "$STUB_ENV_OUT"\n'
                          'echo "MCP=$*" >> "$STUB_ENV_OUT"\n'
                          'cat >/dev/null\n'
                          'echo \'{"type":"result","subtype":"success","is_error":false,"result":"OK",'
                          '"session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},'
                          '"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}\'\n')
        text = stub.read_text(encoding="utf-8").replace("__MODEL__", self.model)
        stub.write_text(text, encoding="utf-8", newline="\n")
        record = self.work / "secondary.record.json"
        ledger = self.work / "ledger.jsonl"
        env = dict(self.env, STUB_ENV_OUT=str(child_env), SIDECAR_LEDGER_PATH=str(ledger))
        result = self.launch(WRAP, record, env=env, extra=("-t", READ_ONLY + ",+mcp:planted-srv"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        child = child_env.read_text(encoding="utf-8")
        cfg = next(line[len("CFG="):] for line in child.splitlines() if line.startswith("CFG="))
        self.assertEqual(os.path.normcase(cfg), os.path.normcase(str(self.work / "secondary")))
        self.assertIn("TR=anthropic-secondary", child)
        self.assertIn("planted-srv", child, "the child gets the default login's MCP server")
        row = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(row["transport"], "anthropic-secondary")
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(rows[-1]["transport"], "anthropic-secondary")
        self.assertEqual((self.work / "secondary" / "ai_worker_model_guide.md").read_text(encoding="utf-8").strip(),
                         "planted primary instructions", "a wrapper launch syncs the secondary login's config first")

    def test_a_base_launch_runs_with_no_config_dir_and_writes_its_window(self):
        """A base launch: CLAUDE_CONFIG_DIR unset, TR=anthropic, and the client window the child gets on the
        argv/env and the record stamps, with the caller's settings hooks preserved."""
        child_env = self.work / "base.env"
        stub = write_stub(self.work / "bin" / "claude",
                          'python3 - "$@" <<PY\n'
                          'import json, os, sys\n'
                          'json.dump({"argv": sys.argv[1:], "cfg": os.getenv("CLAUDE_CONFIG_DIR", "<unset>"),'
                          ' "tr": os.getenv("CLAUDE_CODE_TRANSPORT", ""),'
                          ' "auto": os.getenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW"),'
                          ' "max": os.getenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS"),'
                          ' "ttl": os.getenv("CLAUDE_CODE_PROMPT_CACHE_TTL")}, open(os.environ["STUB_ENV_OUT"], "w"))\n'
                          'PY\n'
                          'cat >/dev/null\n'
                          'echo \'{"type":"result","subtype":"success","is_error":false,"result":"OK",'
                          '"session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},'
                          '"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}\'\n')
        stub.write_text(stub.read_text(encoding="utf-8").replace("__MODEL__", self.model), encoding="utf-8",
                        newline="\n")
        (self.work / "home" / ".claude").mkdir(parents=True, exist_ok=True)
        (self.work / "home" / ".claude" / "settings.json").write_text('{"autoCompactWindow":150000}', encoding="utf-8")
        settings = '{"env":{"KEEP":"yes"},"hooks":{"SessionStart":[{"matcher":"startup","hooks":[{"type":"command",' \
                   '"command":"keep-existing","timeout":5}]}]}}'
        record = self.work / "base.record.json"
        env = dict(self.env, STUB_ENV_OUT=str(child_env), SC_SETTINGS_JSON=settings, SIDECAR_FULL_WINDOW="1")
        result = self.launch(BASE, record, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        child = json.loads(child_env.read_text(encoding="utf-8"))
        self.assertEqual((child["cfg"], child["tr"]), ("<unset>", "anthropic"))
        self.assertEqual((child["auto"], child["max"]), ("150000", None))
        self.assertEqual(child["ttl"], "5m")
        argv = child["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], self.model)
        merged = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual(merged["env"]["KEEP"], "yes")
        self.assertTrue(any(h["matcher"] == "startup" for h in merged["hooks"]["SessionStart"]))
        self.assertTrue(any("sidecar_recompact_reprompt.py" in h["hooks"][0]["command"]
                            for h in merged["hooks"].get("PreCompact", [])))
        row = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(row["contextWindow"], 150000)
        self.assertEqual(row["compactionRail"], 4)

    def test_a_compact_override_reaches_the_child_and_the_record(self):
        """`-K` (and the env input the registry's window CLI reads) replaces the compact point: the child's
        window and the record's contextOverride say so."""
        child_env = self.work / "override.env"
        stub = write_stub(self.work / "bin" / "claude", """python3 - "$@" <<PY
import json, os, sys
json.dump({"argv": sys.argv[1:], "auto": os.getenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW")},
          open(os.environ["STUB_ENV_OUT"], "w"))
PY
cat >/dev/null
echo '{"type":"result","subtype":"success","is_error":false,"result":"OK","session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}'
""")
        stub.write_text(stub.read_text(encoding="utf-8").replace("__MODEL__", self.model), encoding="utf-8")
        record = self.work / "override.record.json"
        result = self.launch(WRAP, record, env=dict(self.env, STUB_ENV_OUT=str(child_env)), extra=("-K", "967000"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        child = json.loads(child_env.read_text(encoding="utf-8"))
        row = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual((child["auto"], row["contextWindow"], row["contextOverride"]),
                         ("1000000", 1000000, 1000000), "the override's window is the compact point plus the reserve")
        override = model_registry.anthropic_window(
            dict(model_registry.resolve(self.model, data=model_registry.load(self.registry)), longContextTier=None),
            compact_override=967000)
        self.assertEqual((override["autoCompactWindow"], override["contextWindow"]), (1000000, 1000000))

    def test_a_detached_wrapper_launch_keeps_its_plan_through_the_snapshot(self):
        child_env = self.work / "det.env"
        stub = write_stub(self.work / "bin" / "claude",
                          'echo "TR=${CLAUDE_CODE_TRANSPORT:-}" > "$STUB_ENV_OUT"\ncat >/dev/null\n'
                          'echo \'{"type":"result","subtype":"success","is_error":false,"result":"OK",'
                          '"session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},'
                          '"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}\'\n')
        stub.write_text(stub.read_text(encoding="utf-8").replace("__MODEL__", self.model), encoding="utf-8",
                        newline="\n")
        record = str(self.work / "detached.record.json")
        result = self.launch(WRAP, record, env=dict(self.env, STUB_ENV_OUT=str(child_env)), extra=("-X",))
        self.assertEqual(result.returncode, 0, result.stderr)
        deadline = time.monotonic() + 120
        exit_file = Path(record + ".exit")
        while not (exit_file.exists() and exit_file.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(exit_file.read_text().strip(), "0")
        self.assertEqual(child_env.read_text(encoding="utf-8").strip(), "TR=anthropic-secondary")


class PlanConsole(PlanFixture):
    def write_keys(self, primary, secondary):
        (self.work / "env.ai-worker.cmd").write_text(
            "set ANTHROPIC_CONSOLE_KEY_PRIMARY=%s\r\nset ANTHROPIC_CONSOLE_KEY_SECONDARY=%s\r\n"
            % (primary, secondary), encoding="utf-8", newline="")

    def test_a_key_plan_names_its_variable_and_never_its_value(self):
        self.write_keys("<redacted>", FAKE_KEY_SECONDARY)
        placeholder = self.run_launcher(CONSOLE, ["--check"])
        self.assertEqual(placeholder.returncode, 3, placeholder.stdout + placeholder.stderr)
        self.assertIn("ANTHROPIC_CONSOLE_KEY_PRIMARY", placeholder.stdout)
        self.assertNotIn("<redacted>", placeholder.stdout)
        (self.work / "env.ai-worker.cmd").unlink()
        missing = self.run_launcher(CONSOLE, ["--check"])
        self.assertEqual(missing.returncode, 3, missing.stdout + missing.stderr)
        self.assertIn("ANTHROPIC_CONSOLE_KEY_PRIMARY", missing.stdout)
        self.write_keys(FAKE_KEY_PRIMARY, FAKE_KEY_SECONDARY)
        ok = self.run_launcher(CONSOLE, ["--check"])
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("ANTHROPIC_CONSOLE_KEY_PRIMARY", ok.stdout)
        self.assertNotIn(FAKE_KEY_PRIMARY, ok.stdout)
        self.assertFalse((self.work / "anthropic-console" / ".credentials.json").exists())
        second = self.run_launcher(CONSOLE_SECOND, ["--check"])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("ANTHROPIC_CONSOLE_KEY_SECONDARY", second.stdout)
        self.assertNotIn(FAKE_KEY_PRIMARY, second.stdout)

    def test_a_console_launch_hands_the_key_to_the_child_and_no_further(self):
        self.write_keys(FAKE_KEY_PRIMARY, FAKE_KEY_SECONDARY)
        dump = self.work / "console.env"
        stub = write_stub(self.work / "bin" / "claude",
                          'python3 - "$@" <<PY\n'
                          'import json, os, sys\n'
                          'json.dump({"argv": sys.argv[1:], "env": dict(os.environ)}, open(os.environ["STUB_ENV_OUT"], "w"))\n'
                          'PY\ncat >/dev/null\n'
                          'echo \'{"type":"result","subtype":"success","is_error":false,"result":"OK",'
                          '"session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1},'
                          '"modelUsage":{"__MODEL__":{"inputTokens":1,"outputTokens":1}}}\'\n')
        stub.write_text(stub.read_text(encoding="utf-8").replace("__MODEL__", self.model), encoding="utf-8",
                        newline="\n")
        ledger = self.work / "home" / ".claude" / "sidecar_ledger.jsonl"
        record = self.work / "console.record.json"
        env = dict(self.env, STUB_ENV_OUT=str(dump), ANTHROPIC_BASE_URL="https://parent.invalid/sentinel",
                   ANTHROPIC_AUTH_TOKEN="<parent-auth-sentinel>",
                   ANTHROPIC_CONSOLE_KEY_LEAK="parent-console-leak-sentinel",
                   SIDECAR_LEDGER_PATH=str(self.work / "env-ignored.jsonl"),
                   CLAUDE_CONFIG_DIR=str(self.work / "home" / ".claude"))
        result = self.launch(CONSOLE, record, env=env, extra=("-L", str(self.work / "explicit-ignored.jsonl")))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        child = json.loads(dump.read_text(encoding="utf-8"))
        self.assertEqual(child["env"].get("ANTHROPIC_API_KEY"), FAKE_KEY_PRIMARY)
        self.assertEqual(os.path.normcase(child["env"].get("CLAUDE_CONFIG_DIR", "")),
                         os.path.normcase(str(self.work / "anthropic-console")))
        self.assertEqual(child["env"].get("CLAUDE_CODE_TRANSPORT"), "anthropic-console")
        self.assertFalse([n for n in child["env"] if n.startswith("ANTHROPIC")
                          and n not in ("ANTHROPIC_API_KEY", "ANTHROPIC_SMALL_FAST_MODEL")])
        self.assertFalse([n for n in child["env"] if n.startswith("CLAUDE")
                          and not n.startswith("CLAUDE_CODE_") and n != "CLAUDE_CONFIG_DIR"])
        self.assertFalse([v for v in ("https://parent.invalid/sentinel", "<parent-auth-sentinel>",
                                      "parent-console-leak-sentinel") if v in child["env"].values()])
        self.assertFalse([a for a in child["argv"] if FAKE_KEY_PRIMARY in a], "the key is on no command line")
        record_text = record.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(rows[-1]["transport"], "anthropic-console")
        for ignored in ("explicit-ignored.jsonl", "env-ignored.jsonl"):
            self.assertFalse((self.work / ignored).exists(), "the one default ledger owns a Console row")
        for surface in (record_text, ledger.read_text(encoding="utf-8")):
            self.assertNotIn(FAKE_KEY_PRIMARY, surface)
            self.assertNotIn(FAKE_KEY_SECONDARY, surface)
            self.assertNotIn("ANTHROPIC_CONSOLE_", surface)
        self.assertFalse((self.work / "anthropic-console" / ".credentials.json").exists())
        self.assertEqual((self.work / "anthropic-console" / "ai_worker_model_guide.md").read_text(encoding="utf-8").strip(),
                         "planted primary instructions")

    def test_the_deep_probe_records_one_priced_ledger_row(self):
        self.write_keys(FAKE_KEY_PRIMARY, FAKE_KEY_SECONDARY)
        ledger = self.work / "home" / ".claude" / "sidecar_ledger.jsonl"
        before = len([line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]) \
            if ledger.exists() else 0
        result = self.run_launcher(CONSOLE, ["--check", "--deep"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        new = rows[before:]
        self.assertEqual(len(new), 1, rows)
        self.assertEqual(new[0]["transport"], "anthropic-console")
        self.assertGreater(new[0]["costUSD"], 0)
        self.assertNotIn(FAKE_KEY_PRIMARY, ledger.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _case_pool import run_cases, unittest_cases
    sys.exit(run_cases(unittest_cases(PlanPlacement) + unittest_cases(PlanWindow) + unittest_cases(PlanLaunch) +
                       unittest_cases(PlanConsole)))
