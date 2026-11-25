"""Contract tests for the free OpenCode CLI seat, `scripts/opencode_run_sidecar.sh`.

The stub cases run the launcher through Git Bash (the shell every caller uses) against a stub CLI
and spend nothing. `OPENCODE_CLI_LIVE=1` adds the live cases, which call the genuine CLI against a
Zen free model named by the registry alias in `OPENCODE_CLI_LIVE_MODEL`.

Each stub case spawns the launcher through Git Bash (about 5 s a launch alone), so the cases are split across
this file (the stub, the fixture, the launcher checks and the run lifecycle) and `test_opencode_run_sidecar_arms.py`,
`_policy.py`, `_attest.py` and `_schema.py`, which the pool runs side by side, and `_timing.py`, which runs alone.
This file reads only shipped files; the cases that load benchmark-campaign files (the free-pool log fixtures, the
campaign's void-reason classifier, the arm policy) live in `test_opencode_run_sidecar_campaign.py`.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ".claude/scripts/opencode_run_sidecar.sh"
sys.path.insert(0, str(ROOT / ".claude/tools"))
sys.path.insert(0, str(ROOT / ".claude/hooks"))
import sidecar_launch  # noqa: E402
import _transport_fixture  # noqa: E402
import _mcp_plant  # noqa: E402

# The launcher validates its tool profile against the MCP servers the caller's config registers; a runner has none.
# The sibling proofs import this module, so each of them reads the planted four.
_mcp_plant.plant()

BASH = sidecar_launch.git_bash()
APPROVED = {"read", "glob", "grep"}
SEARCH_TOOL = "mcp__plugin_semantic-search_semantic-search__search"
GRID_DENY = ("Task,Agent,Workflow,mcp__ai-worker__read_files,mcp__obsidian,mcp__godot,WebSearch,WebFetch,"
             "SendMessage,ListAgents,RemoteTrigger,CronCreate")
WRITE_TOOLS = f"Read,Glob,Grep,Edit,Write,Bash,TodoWrite,{SEARCH_TOOL}"
DENY_ALL = {"*": "deny", "**": "deny"}

STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, re, sys, time
args = sys.argv[1:]
state_path = pathlib.Path(os.environ["TEST_CLI_STATE"])
log = pathlib.Path(os.environ["TEST_CLI_LOG"])

def rules_from(config):
    agents = config.get("agent") or {}
    permission = (next(iter(agents.values()), None) or {}).get("permission") or {}
    rules = [{"permission": "*", "action": "allow", "pattern": "*"}]
    for name, value in permission.items():
        if isinstance(value, str):
            rules.append({"permission": name, "action": value, "pattern": "*"})
        else:
            rules += [{"permission": name, "action": act, "pattern": pat} for pat, act in value.items()]
    return rules

def offered(rules):
    tools = {}
    for tool in ("bash", "read", "glob", "grep", "task", "webfetch", "websearch", "todowrite", "skill", "apply_patch"):
        perm = "edit" if tool == "apply_patch" else tool
        hit = [r for r in rules if r["permission"] in ("*", perm)]
        tools[tool] = not (hit and hit[-1]["pattern"] == "*" and hit[-1]["action"] == "deny")
    return tools

config = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
if args[:3] == ["models", "opencode", "--verbose"]:
    if os.environ.get("TEST_CLI_LOCK_CATALOG"):
        count_path = pathlib.Path(os.environ["TEST_CLI_STATE"] + ".catalog-count")
        count = int(count_path.read_text()) if count_path.exists() else 0
        count_path.write_text(str(count + 1))
        with open(str(count_path) + ".times", "a") as fh:
            fh.write(repr(time.time()) + chr(10))
        if not os.environ.get("TEST_CLI_LOCK_THEN_SUCCEED") or count == 0:
            print("Error: Unexpected error\\n\\ndatabase is locked", file=sys.stderr)
            sys.exit(1)
    cost = 0.5 if os.environ.get("TEST_CLI_PRICED") else 0
    print("opencode/test-free-cli")
    print(json.dumps({"id": "test-free-cli", "cost": {"input": cost, "output": cost, "cache": {"read": 0, "write": 0}},
                      "limit": {"context": 1000, "output": 100},
                      "variants": {"low": {}, "medium": {}, "high": {}}}, indent=2))
    sys.exit(0)
if args[:2] == ["debug", "agent"]:
    if not config:
        print("Agent %s not found" % args[2])
        sys.exit(1)
    rules = rules_from(config)
    plant = os.environ.get("TEST_CLI_PLANT")
    if plant == "external-allow":
        rules.append({"permission": "external_directory", "action": "allow", "pattern": "*"})
    if plant == "ask":
        rules.append({"permission": "bash", "action": "ask", "pattern": "git *"})
    if plant == "vault-allow":
        rules.append({"permission": "bash", "action": "allow", "pattern": "*ObsidianVault*"})
    tools = offered(rules)
    if os.environ.get("TEST_CLI_DROP_TOOL"):
        tools["bash"] = False
    print(json.dumps({"name": args[2], "mode": "primary", "permission": rules, "tools": tools}))
    sys.exit(0)
if args[:2] == ["run", "--help"]:
    print("  -f, --file   file(s) to attach to message")
    if not os.environ.get("TEST_CLI_NO_VARIANT"):
        print("      --variant   model variant (provider-specific reasoning effort)")
    sys.exit(0)
if args[:1] == ["export"]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    message = state["message"] + ("mangled" if os.environ.get("TEST_CLI_MANGLE_MESSAGE") else "")
    parts = [{"type": "text", "text": message}]
    for path in state["files"]:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
        if os.environ.get("TEST_CLI_TRUNCATE"):
            lines = lines[:1]
        shown = "\n".join("%d: %s" % (i + 1, line) for i, line in enumerate(lines))
        body = "<path>%s</path>\n<type>file</type>\n<content>\n%s\n\n(End of file - total %d lines)\n</content>" % (
            path.replace("/", "\\"), shown, len(lines))
        if os.environ.get("TEST_CLI_REMINDER"):
            body += "\n\n<system-reminder>\nnearest instruction file\n</system-reminder>"
        parts += [{"type": "text", "synthetic": True, "text": body}, {"type": "file", "url": "file:///" + path}]
    model = "other-model" if os.environ.get("TEST_CLI_WRONG_MODEL") else "test-free-cli"
    agent = "build" if os.environ.get("TEST_CLI_WRONG_AGENT") else state["agent"]
    turns = [{"info": {"role": "user", "agent": agent}, "parts": parts},
             {"info": {"role": "assistant", "providerID": "opencode", "modelID": model, "agent": agent, "cost": 0,
                       "tokens": {"input": 11, "output": 7, "cache": {"read": 3, "write": 0}}}, "parts": []}]
    if os.environ.get("TEST_CLI_COMPACTION"):
        # The CLI summarizes a long session itself, on the same model, under its own `compaction` agent.
        turns.insert(1, {"info": {"role": "assistant", "providerID": "opencode", "modelID": model,
                                  "agent": os.environ["TEST_CLI_COMPACTION"], "cost": 0,
                                  "tokens": {"input": 5, "output": 2, "cache": {"read": 0, "write": 0}}}, "parts": []})
    print(json.dumps({"info": {"id": "ses_test"}, "messages": turns}))
    sys.exit(0)
if args[:1] != ["run"]:
    sys.exit(2)
files = [args[i + 1] for i, v in enumerate(args) if v == "--file"]
message = sys.stdin.buffer.read().decode("utf-8")
agent_name = args[args.index("--agent") + 1]
# A `--session` resume continues the session: its export still opens with the first turn's message and files.
if "--session" not in args or not state_path.exists():
    state_path.write_text(json.dumps({"files": files, "message": message, "agent": agent_name}), encoding="utf-8")
def priority():
    if os.name != "nt":
        return None
    import ctypes
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetPriorityClass.argtypes = [ctypes.c_void_p]
    return k.GetPriorityClass(k.GetCurrentProcess())
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": args, "stdin": message, "cwd": os.getcwd(), "priority": priority(),
                         "claudeCode": os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE"),
                         "files": [pathlib.Path(p).read_text(encoding="utf-8") for p in files],
                         "config": config}) + "\n")
print(json.dumps({"type": "step_start", "sessionID": "ses_test", "part": {"type": "step-start"}}), flush=True)
if os.getenv("TEST_CLI_LOG_FIXTURE") and "--print-logs" in args:
    # The real CLI writes these lines to stderr only under --print-logs; the fixtures are cut from live runs.
    sys.stderr.write(pathlib.Path(os.environ["TEST_CLI_LOG_FIXTURE"]).read_text(encoding="utf-8"))
    sys.stderr.flush()
if os.getenv("TEST_CLI_SLEEP"):
    time.sleep(float(os.environ["TEST_CLI_SLEEP"]))
if os.getenv("TEST_CLI_HOLD"):
    hold_deadline = time.time() + 120
    while not os.path.exists(os.environ["TEST_CLI_HOLD"]) and time.time() < hold_deadline:
        time.sleep(0.05)
if os.getenv("TEST_CLI_FAIL"):
    print("CLI error", file=sys.stderr)
    sys.exit(17)
if os.getenv("TEST_CLI_API_ERROR"):
    # Cut from a live opencode 429 (free-sweep-cli-ling-31 T3, 2026-10-14), trimmed.
    print(json.dumps({"type": "text", "sessionID": "ses_test", "part": {"type": "text", "messageID": "msg_1",
                                                                          "text": "Now I'll ground the design."}}))
    print(json.dumps({"type": "error", "sessionID": "ses_test", "error": {"name": "APIError", "data": {
        "message": "Upstream request failed: Endpoint is unavailable.", "statusCode": 429, "isRetryable": True}}}))
    sys.exit(1)
answer = os.environ.get("TEST_CLI_ANSWER") or "Done: .claude/CLAUDE.md:1"
event = lambda part: print(json.dumps({"type": part["type"], "sessionID": "ses_test", "part": part}))
event({"type": "tool", "tool": "read", "state": {"input": {"filePath": "C:\\repo\\.claude\\CLAUDE.md"}}})
if os.environ.get("TEST_CLI_EXTRA_READ"):
    event({"type": "tool", "tool": "read", "state": {"input": {"filePath": "C:\\repo\\.claude\\CLAUDE.core.md"}}})
if os.environ.get("TEST_CLI_OUTSIDE"):
    outside = os.environ["TEST_CLI_OUTSIDE"]
    event({"type": "tool", "tool": "write", "state": {"status": "error", "input": {"filePath": outside + "/stolen.txt"},
                                                       "error": "denied by rule"}})
    event({"type": "tool", "tool": "bash", "state": {"status": "error", "input": {"command": "cat ../t4-x-r10/raw.json"},
                                                      "error": "denied by rule"}})
    event({"type": "tool", "tool": "write", "state": {"status": "completed", "input": {"filePath": "inside.txt"}}})
event({"type": "text", "messageID": "msg_1", "text": answer})
'''


def native(path):
    return Path(path).as_posix()


def kernel32():
    import ctypes
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetPriorityClass.argtypes = [ctypes.c_void_p]
    k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    return k


def caller_priority():
    k = kernel32()
    return k.GetPriorityClass(k.GetCurrentProcess())


class StubFixture(unittest.TestCase):
    """The stub CLI, registry and scratch seat the stub cases share; the cases live in `StubSeat`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        catalog = json.loads((ROOT / ".claude/reference/external_models.json").read_text(encoding="utf-8"))
        catalog["transports"].setdefault("opencode-cli", {
            "launcher": LAUNCHER, "costModel": "marginal-usd",
            "capacityUnsupported": {"reason": "fixture"}, "effortValues": ["low", "medium", "high", "xhigh", "max"]})
        catalog["transports"]["opencode-cli"]["status"] = {"state": "available"}
        catalog["models"].append({
            "id": "opencode-cli/test-free-cli", "upstreamId": "test-free-cli", "alias": "test-cli", "input": ["text"],
            "transport": "opencode-cli", "label": "Test CLI", "family": "test-cli-family", "version": "test-free-cli",
            "lifecycle": "current", "order": 9998, "costRank": 0, "prior": "small", "status": {"state": "available"},
            "roles": ["fanout", "executor"], "limits": {"contextTokens": 1000, "maxOutputTokens": 100, "concurrency": None},
            "_limitsSource": "fixture",
            "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
                      "asOf": "2026-09-27", "source": "fixture"},
            "effort": {"evidence": "measured", "measuredVersion": "test-free-cli"}, "effortValues": ["low", "medium", "high"],
            "authTier": "open", "gate": {"minBand": "Surplus"}})
        self.registry = self.work / "models.json"
        self.registry.write_text(json.dumps(catalog), encoding="utf-8")
        self.log = self.work / "calls.jsonl"
        self.bin = self.work / "opencode"
        self.bin.write_bytes(STUB.encode("utf-8"))
        self.env = dict(os.environ, OPENCODE_BIN=native(self.bin), HARNESS_MODEL_REGISTRY=str(self.registry),
                        TEST_CLI_LOG=str(self.log), TEST_CLI_STATE=str(self.work / "state.json"),
                        SIDECAR_LEDGER_PATH=str(self.work / "ledger.jsonl"),
                        SIDECAR_EXHAUSTED_DIR=str(self.work / "exhausted"), PYTHONIOENCODING="utf-8",
                        SIDECAR_WATCH_POLL_SEC="0.2", SIDECAR_CATALOG_LOCK_WAITS="0.05 0.05",
                        LAUNCH_MIN_COMMIT_GIB="0", LAUNCH_MIN_PHYSICAL_GIB="0")
        for key in ("CLAUDE_CODE_SESSION_ID", "SC_LAUNCHER_DIR", "SC_SNAPSHOT_OF", "SIDECAR_DETACHED"):
            self.env.pop(key, None)
        self.env["PROVIDER_CAPACITY_CACHE"] = _transport_fixture._available_capacity_cache()
        self.brief = self.work / "brief.md"
        self.brief.write_text("Read first: .claude/CLAUDE.md\nReport one citation.\n", encoding="utf-8")
        self.cell = self.work / "BenchmarkArms" / "t4-x-r1"
        self.cell.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.cell)], check=True, capture_output=True, cwd=self.work)

    def run_sidecar(self, *args, env=None, timeout=300, stdin_text=""):
        # These cases inspect the CLI launcher's runtime refusals, not the shared preflight
        # refusal. The real preflight is exercised by test_dispatch_hook_preflight_accepts_and_refuses.
        runtime_env = dict(env or self.env, HARNESS_SIDECAR_PREFLIGHT="0")
        return subprocess.run([BASH, LAUNCHER, "-m", "test-cli", "-d", native(ROOT), *args], cwd=ROOT,
                              env=runtime_env, text=True, encoding="utf-8", errors="replace", input=stdin_text,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)

    def hold(self):
        """A release file the stub CLI blocks on: the run stays live until the case writes it, whatever the load."""
        release = self.work / ("release-%d" % len(list(self.work.glob("release-*"))))
        self.addCleanup(release.write_text, "go")
        return release

    def permission(self, index=-1):
        agents = self.calls()[index]["config"]["agent"]
        (name, agent), = agents.items()
        return name, agent["permission"]

    def await_exit(self, path):
        """The detached job's `.exit`, once written. The launcher writes the journal `ended` row, which the
        watcher reports as DONE, a few commands before `.exit` (R1), so a case ends only after the file lands:
        a case that ends first deletes the directory under the writer."""
        exit_file = Path(path + ".exit")
        deadline = time.monotonic() + 120
        while not (exit_file.exists() and exit_file.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.05)
        return exit_file.read_text().strip()

    def record(self, name):
        return native(self.work / name)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def assert_durable(self, path, rc):
        self.assertTrue(Path(path + ".pid").exists())
        self.assertTrue(Path(path + ".out").exists())
        self.assertEqual(int(Path(path + ".exit").read_text()), rc)
        return json.loads(Path(path + ".out").read_text(encoding="utf-8"))


class StubSeat(StubFixture):
    def test_stub_env_plants_a_fresh_capacity_cache(self):
        cache = self.env.get("PROVIDER_CAPACITY_CACHE", "")
        self.assertTrue(cache and Path(cache).is_file(), cache)

    def test_a_memory_floor_refuses_the_free_launch_before_the_cli_runs(self):
        path = self.record("memory-refused")
        result = self.run_sidecar("-G", "lens", "-f", native(self.brief), "-R", path,
                                  env=dict(self.env, LAUNCH_MIN_COMMIT_GIB="1000000", LAUNCH_MIN_PHYSICAL_GIB="1000000"))
        self.assertEqual(result.returncode, 12, result.stdout + result.stderr)
        self.assertEqual(self.calls(), [])

    def test_a_memory_floor_holds_a_detached_launch_until_its_bound(self):
        path = self.record("memory-waits.record.json")
        launch = self.run_sidecar("-G", "lens", "-f", native(self.brief), "-R", path, "-X",
                                  env=dict(self.env, LAUNCH_MIN_COMMIT_GIB="1000000", LAUNCH_MIN_PHYSICAL_GIB="1000000",
                                           SIDECAR_TIMEOUT="3"))
        self.assertEqual(launch.returncode, 0, launch.stderr)
        self.assertEqual(self.await_exit(path), "12")
        self.assertEqual(self.calls(), [])

    def test_check_fails_closed_on_invalid_permissions(self):
        result = subprocess.run([BASH, LAUNCHER, "--check", "-m", "test-cli"], cwd=ROOT,
                                env=dict(self.env, TEST_CLI_DROP_TOOL="1"), text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("UNAVAILABLE", result.stdout)

    def test_dispatch_hook_preflight_accepts_and_refuses(self):
        import sidecar_dispatch_context
        command = "bash .claude/scripts/opencode_run_sidecar.sh -m test-cli -G lens -f brief.md"
        with patch.dict(os.environ, {k: v for k, v in self.env.items() if k.startswith(("OPENCODE", "HARNESS", "TEST_CLI", "SIDECAR"))}):
            self.assertEqual(sidecar_dispatch_context.run_preflight(command, str(ROOT)), ("passed", None))
            os.environ["TEST_CLI_DROP_TOOL"] = "1"
            state, refusal = sidecar_dispatch_context.run_preflight(command, str(ROOT))
        self.assertEqual(state, "refused")
        self.assertIn("UNAVAILABLE", refusal)

    def test_cli_failure_writes_durable_record(self):
        path = self.record("failure")
        result = self.run_sidecar("-G", "lens", "-f", native(self.brief), "-R", path, env=dict(self.env, TEST_CLI_FAIL="1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.assert_durable(path, result.returncode)["is_error"])

    def test_an_upstream_api_error_names_itself_in_the_envelope(self):
        """A 429 the CLI reports as an error event leads the result and carries `error_status`, the key the
        grid's rate-limit classifier reads; the model's last narration stays after it."""
        path = self.record("api-error")
        result = self.run_sidecar("-G", "lens", "-f", native(self.brief), "-R", path,
                                  env=dict(self.env, TEST_CLI_API_ERROR="1"))
        self.assertNotEqual(result.returncode, 0)
        envelope = self.assert_durable(path, result.returncode)
        self.assertTrue(envelope["is_error"])
        self.assertEqual(envelope["error_status"], 429)
        self.assertTrue(envelope["result"].startswith("APIError 429: Upstream request failed: Endpoint is unavailable."),
                        envelope["result"])
        self.assertIn("Now I'll ground the design.", envelope["result"])

    def test_killed_run_writes_durable_record(self):
        path = self.record("killed")
        result = self.run_sidecar("-G", "lens", "-f", native(self.brief), "-R", path,
                                  env=dict(self.env, TEST_CLI_SLEEP="30", SIDECAR_TIMEOUT="2"))
        self.assertEqual(result.returncode, 124)
        self.assertTrue(self.assert_durable(path, 124)["is_error"])

    def test_detached_run_watch_reports_done_only_after_exit(self):
        path = self.record("detached.record.json")
        release = self.hold()
        launch = self.run_sidecar("-G", "lens", "-f", native(self.brief), "-R", path, "-X",
                                  env=dict(self.env, TEST_CLI_HOLD=native(release)))
        self.assertEqual(launch.returncode, 0, launch.stderr)
        self.assertIn("DETACHED", launch.stdout)
        deadline = time.monotonic() + 60
        while Path(path + ".pid").read_text().count("winpid=") < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(Path(path + ".pid").read_text().count("winpid="), 2, "the detached job never stamped its pid")
        watcher = subprocess.Popen([sys.executable, str(ROOT / ".claude/tools/watch_records.py"), path],
                                   stdout=subprocess.PIPE, text=True, env=dict(self.env, WATCH_RECORDS_POLL_SEC="0.2"))
        self.addCleanup(watcher.wait, 5)
        time.sleep(0.8)
        self.assertIsNone(watcher.poll(), "watch finished before the run wrote .exit")
        self.assertFalse(Path(path + ".exit").exists())
        release.write_text("go")
        first = watcher.stdout.readline()
        self.assertTrue(first.startswith("DONE detached exit=0"), first)
        self.assertEqual(self.await_exit(path), "0")
        self.assertIn("ALL REPORTED", watcher.stdout.read())
        self.assertFalse(json.loads(Path(path + ".out").read_text(encoding="utf-8"))["is_error"])

    def test_watch_started_at_detach_does_not_report_a_live_job_died(self):
        path = self.record("race.record.json")
        release = self.hold()
        launch = self.run_sidecar("-G", "lens", "-f", native(self.brief), "-R", path, "-X",
                                  env=dict(self.env, TEST_CLI_HOLD=native(release)))
        self.assertEqual(launch.returncode, 0, launch.stderr)
        watcher = subprocess.Popen([sys.executable, str(ROOT / ".claude/tools/watch_records.py"), path], text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=dict(self.env, WATCH_RECORDS_POLL_SEC="0.2"))
        self.addCleanup(watcher.kill)
        time.sleep(0.5)
        release.write_text("go")
        out, _ = watcher.communicate(timeout=120)
        watch = subprocess.CompletedProcess(watcher.args, watcher.returncode, out)
        self.assertIn("DONE race exit=0", watch.stdout, "a watcher started at DETACHED saw only the exited launcher's pid")
        self.assertEqual(self.await_exit(path), "0")

    def test_progress_stream_carries_events_while_the_run_is_live(self):
        stream = self.work / "live.stream"
        release = self.hold()
        proc = subprocess.Popen([BASH, LAUNCHER, "-m", "test-cli", "-d", native(ROOT), "-G", "lens", "-f",
                                 native(self.brief), "-P", native(stream)], cwd=ROOT,
                                env=dict(self.env, TEST_CLI_HOLD=native(release)), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.addCleanup(proc.wait, 60)
        deadline = time.monotonic() + 120
        while not self.calls() and time.monotonic() < deadline:
            time.sleep(0.2)
        seen_live = False
        while proc.poll() is None and time.monotonic() < deadline:
            if stream.exists() and "step_start" in stream.read_text(encoding="utf-8"):
                seen_live = proc.poll() is None
                break
            time.sleep(0.2)
        self.assertTrue(seen_live, "no step_start event reached the stream while the run was live")
        release.write_text("go")
        self.assertEqual(proc.wait(60), 0)
        self.assertIn('"type": "result"', stream.read_text(encoding="utf-8"))

    def test_a_silent_run_writes_the_stall_row_and_keeps_running(self):
        """The shared watchdog's contract: silence past -Z writes a `stalled` journal row for the session
        watcher and never ends the run; the next traffic writes `resumed`."""
        path = self.record("silent.record.json")
        stream = self.work / "silent.stream"
        release = self.hold()
        proc = subprocess.Popen([BASH, LAUNCHER, "-m", "test-cli", "-d", native(ROOT), "-G", "lens", "-f",
                                 native(self.brief), "-R", path, "-P", native(stream), "-Z", "1"], cwd=ROOT,
                                env=dict(self.env, TEST_CLI_HOLD=native(release), HARNESS_SIDECAR_PREFLIGHT="0"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.addCleanup(proc.wait, 60)
        journal = Path(path + ".journal.jsonl")

        def events():
            try:
                return [json.loads(line).get("event") for line in journal.read_text(encoding="utf-8").splitlines()
                        if line.strip()]
            except (OSError, ValueError):
                return []

        # The stub stays silent until released, so the stall row is owed whatever the launch took.
        deadline = time.monotonic() + 100
        while "stalled" not in events() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertIn("stalled", events(), "no stalled row while the silent run was live")
        self.assertIsNone(proc.poll(), "the stall row ended the run")
        release.write_text("go")
        self.assertEqual(proc.wait(60), 0)
        self.assertFalse(Path(path + ".stall").exists(), "a .stall marker outlived the run")

    def test_full_disclosure_attaches_claude_md_and_each_of_its_imports(self):
        # A `full` kind (reviewer) attaches what Claude Code loads: CLAUDE.md and every file its `@` lines import, in order. A fixed
        # pair (CLAUDE.md, CLAUDE.core.md) left out the layer files a project imports.
        claude = ROOT / ".claude"
        text = (claude / "CLAUDE.md").read_text(encoding="utf-8")
        imports = [line[1:].strip() for line in text.splitlines() if line.startswith("@")]
        expected = [text] + [(claude / rel).read_text(encoding="utf-8") for rel in imports if (claude / rel).is_file()]
        path = native(self.work / "full.record.json")
        result = self.run_sidecar("-G", "reviewer", "-S", native(ROOT / ".claude/schemas/review_findings.json"),
                                  "-f", native(self.brief), "-R", path)
        self.assertTrue(self.calls(), result.stdout + result.stderr)
        attached = self.calls()[0]["files"]  # the first turn; a schema retry resumes the session with none
        self.assertEqual([t for t in attached if t in expected], expected)

    def test_pointer_disclosure_attaches_no_claude_file(self):
        # A `pointer` kind (lens) attaches none.
        claude_texts = {(ROOT / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")}
        path = native(self.work / "pointer.record.json")
        result = self.run_sidecar("-G", "lens", "-f", native(self.brief), "-R", path)
        self.assertTrue(self.calls(), result.stdout + result.stderr)
        self.assertFalse(claude_texts & set(self.calls()[0]["files"]))

    def test_fanout_context_and_tool_removal(self):
        context = self.work / "context.md"
        context.write_text("Context marker: LANTERN-241\n", encoding="utf-8")
        jobs = self.work / "jobs.json"
        jobs.write_text(json.dumps([{"label": "cli-stub", "alias": "test-cli", "kind": "lens",
                                     "promptFile": str(self.brief), "workdir": str(self.cell),
                                     "contextFiles": [str(context)], "tools": {"remove": ["Grep"]}}]), encoding="utf-8")
        outdir = self.work / "fanout"
        # No clock here: the fan-out waits on its child's end with no timeout (sidecar_fanout._wait_detached), and a
        # launcher start costs seconds under load. The case waits for the fan-out's own end, then for the record's .exit.
        result = subprocess.run([sys.executable, ".claude/tools/sidecar_fanout.py", str(jobs), "--out-dir", str(outdir),
                                 "--max-parallel", "1"], cwd=ROOT, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = list(outdir.glob("*.record.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(self.await_exit(str(records[0])), "0")
        call = self.calls()[0]
        self.assertTrue(any("LANTERN-241" in text for text in call["files"]))
        self.assertEqual(set(call["config"]["agent"]["sidecar-readonly"]["permission"]["grep"].values()), {"deny"})


@unittest.skipUnless(os.environ.get("OPENCODE_CLI_LIVE") == "1", "live free-model calls need OPENCODE_CLI_LIVE=1")
class LiveBase(unittest.TestCase):
    """The live staging and launcher call the live cases share; the cases live in `LiveSeat` and, for the campaign's
    arm policy, in `test_opencode_run_sidecar_campaign.py`."""
    ALIAS = os.environ.get("OPENCODE_CLI_LIVE_MODEL", "")

    @classmethod
    def setUpClass(cls):
        import model_registry
        if not cls.ALIAS:
            raise unittest.SkipTest("OPENCODE_CLI_LIVE_MODEL names no registry alias")
        cls.upstream = model_registry.resolve(cls.ALIAS)["upstreamId"]
        cls.tmp = tempfile.TemporaryDirectory(prefix="cli-live-")
        cls.stage = Path(cls.tmp.name)
        cls.rel = native(cls.stage)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_sidecar(self, *args, timeout=400):
        return subprocess.run([BASH, LAUNCHER, "-m", self.ALIAS, *args], cwd=ROOT, text=True, encoding="utf-8",
                              errors="replace", capture_output=True, timeout=timeout,
                              env=dict(os.environ, SIDECAR_LEDGER_PATH=str(self.stage / "ledger.jsonl")))

    def brief(self, name, text):
        path = self.stage / name
        path.write_text(text, encoding="utf-8")
        return native(path)


class LiveSeat(LiveBase):
    def test_bare_deny_config_reproduces_free_tier_403(self):
        allow = {k: "allow" for k in APPROVED}
        config = json.dumps({"agent": {"sidecar-readonly": {"mode": "primary", "permission": {"*": "deny", **allow}}}})
        result = subprocess.run([BASH, "-lc", f"opencode run 'Reply with OK.' --agent sidecar-readonly "
                                 f"--model opencode/{self.upstream} --format json < /dev/null"], cwd=self.stage,
                                env=dict(os.environ, OPENCODE_CONFIG_CONTENT=config), text=True, encoding="utf-8",
                                errors="replace", capture_output=True, timeout=150)
        if "FreeTierError" not in result.stdout + result.stderr:
            self.skipTest(f"{self.upstream} accepts a bare deny; the 403 repro needs a model whose route refuses it")

    def test_survey_cites_staged_source_with_engagement_and_attestation(self):
        source = self.stage / "source.md"
        source.write_text("alpha\nbeta\nThe lantern code is QUILL-5531.\ngamma\n", encoding="utf-8")
        record = native(self.stage / "survey.record.json")
        brief = self.brief("survey.md", f"Read first: {self.rel}/source.md\n\nUse the Read tool on that file, then "
                           f"state the lantern code and cite it as path:line.\n")
        result = self.run_sidecar("-G", "lens", "-d", native(ROOT), "-f", brief, "-R", record)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        envelope = json.loads(Path(record + ".out").read_text(encoding="utf-8"))
        self.assertIn("QUILL-5531", envelope["result"])
        self.assertIn(f"source.md:3", envelope["result"])
        data = json.loads(Path(record).read_text(encoding="utf-8"))
        self.assertEqual((data["engagement"], data["attestedModel"], data["servedModel"]),
                         ("proven", self.upstream, self.upstream))

    def test_review_returns_schema_valid_json(self):
        source = self.stage / "review_source.py"
        source.write_text("def mean(values):\n    return sum(values) / len(values)\n", encoding="utf-8")
        record = native(self.stage / "review.record.json")
        brief = self.brief("review.md", f"Read first: {self.rel}/review_source.py\n\nReview that function for "
                           f"defects. Fill every required field of the attached schema.\n")
        result = self.run_sidecar("-G", "reviewer", "-S", native(ROOT / ".claude/schemas/review_findings.json"),
                                  "-f", brief, "-R", record)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsInstance(json.loads(Path(record + ".out").read_text(encoding="utf-8"))["structured_output"], dict)
        self.assertTrue(json.loads(Path(record).read_text(encoding="utf-8"))["schemaValid"])

    def test_planted_write_and_bash_requests_are_denied(self):
        record = native(self.stage / "plant.record.json")
        brief = self.brief("plant.md", "Use your bash tool to run exactly: echo planted > planted_bash.txt . Then use "
                           "your write tool to create planted_write.txt containing hi. Report whether each succeeded.\n")
        stream = self.stage / "plant.stream"
        result = self.run_sidecar("-G", "lens", "-d", native(self.stage), "-f", brief, "-R", record,
                                  "-P", native(stream))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(list(self.stage.glob("planted_*")))
        self.assertFalse(list(ROOT.glob("planted_*")))
        attempts = []
        for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                part = json.loads(line).get("part") or {}
            except ValueError:
                continue
            if part.get("type") == "tool" and part.get("tool") in ("bash", "write", "edit", "apply_patch"):
                attempts.append(((part.get("state") or {}).get("status"), str((part.get("state") or {}).get("error"))))
        if not attempts:
            self.skipTest("the model declined to call bash or write, so the denial was not exercised")
        self.assertTrue(all(status == "error" and "rule" in error for status, error in attempts), attempts)

    def test_large_brief_reaches_the_child_intact(self):
        body = "".join(f"Filler line {i}: {'lorem ' * 14}\n" for i in range(110))
        brief = self.brief("large.md", body + "Final instruction: reply with the word TAILMARK-8812 and nothing else.\n")
        self.assertGreater(Path(brief).stat().st_size, 8192)
        record = native(self.stage / "large.record.json")
        result = self.run_sidecar("-G", "lens", "-f", brief, "-R", record)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TAILMARK-8812", json.loads(Path(record + ".out").read_text(encoding="utf-8"))["result"])


if __name__ == "__main__":
    if os.environ.get("OPENCODE_CLI_LIVE") == "1":
        unittest.main()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _case_pool import run_cases, unittest_cases
    sys.exit(run_cases(unittest_cases(StubSeat)))
