"""Contract tests for the Antigravity CLI seat, `scripts/antigravity_sidecar.sh`.

Each case runs the launcher through Bash against a stub `agy` (AGY_BIN) that replays streams cut from
live agy 1.5.14 runs (`fixtures/agy/`), so nothing is billed and no Google login is touched. The spawned
cases check process-tree shutdown and exit codes using a native wrapper around the same Python stub.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ".claude/scripts/antigravity_sidecar.sh"
FIX = ROOT / ".claude" / "tests" / "fixtures" / "agy"
sys.path.insert(0, str(ROOT / ".claude/tools"))
sys.path.insert(0, str(ROOT / ".claude/hooks"))
import sidecar_launch  # noqa: E402
import _transport_fixture  # noqa: E402
import _mcp_plant  # noqa: E402

_mcp_plant.plant()
BASH = sidecar_launch.git_bash()

STUB = r'''
import json, os, pathlib, sys, time
args = sys.argv[1:]
log = pathlib.Path(os.environ["TEST_AGY_LOG"])
if args[:1] == ["models"]:
    mode = os.environ.get("TEST_AGY_MODELS", "ok")
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": args, "home": os.environ.get("USERPROFILE") or os.environ.get("HOME")}) + "\n")
    if mode == "signedout":
        print("Please sign in"); sys.exit(1)
    if mode == "empty":
        sys.exit(0)
    print("other-model-1" if mode == "noid" else "agy-test-model\nother-model-1")
    sys.exit(0)
stdin = sys.stdin.buffer.read().decode("utf-8", "replace")
with log.open("a", encoding="utf-8") as fh:
    settings = json.loads(pathlib.Path(args[args.index("--settings") + 1]).read_text()) if "--settings" in args else None
    fh.write(json.dumps({"argv": args, "stdin": stdin, "cwd": os.getcwd(), "settings": settings,
                         "kind": os.environ.get("CLAUDE_CODE_SIDECAR_KIND")}) + "\n")
model = args[args.index("--model") + 1]
for line in pathlib.Path(os.environ["TEST_AGY_STREAM"]).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    event = json.loads(line)
    if event.get("event") == "init":
        event["init"]["model"] = model
    print(json.dumps(event), flush=True)
time.sleep(float(os.environ.get("TEST_AGY_LINGER", "0")))
sys.exit(int(os.environ.get("TEST_AGY_EXIT", "0")))
'''


def native(path):
    return str(path).replace("\\", "/")


class AntigravitySeat(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        stub = self.work / "agy_stub.py"
        stub.write_text(STUB, encoding="utf-8")
        if os.name == "nt":
            self.bin = self.work / "agy.cmd"
            self.bin.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, stub), encoding="utf-8")
        else:
            self.bin = self.work / "agy"
            self.bin.write_text('#!/bin/sh\nexec %s "$@"\n' % shlex.join([sys.executable, str(stub)]), encoding="utf-8")
            self.bin.chmod(0o755)
        self.home = self.work / "home"
        (self.home / ".gemini").mkdir(parents=True)
        self.sentinel = self.home / ".gemini" / "oauth_creds.json"
        self.sentinel.write_text('{"sentinel": true}', encoding="utf-8")
        # Every Python the launcher starts records any open of a path under .gemini.
        hook = self.work / "audit"
        hook.mkdir()
        self.opens = self.work / "gemini_opens.log"
        (hook / "sitecustomize.py").write_text(
            "import sys\n"
            "def _audit(event, args):\n"
            "    if event == 'open' and args and '.gemini' in str(args[0]):\n"
            "        open(%r, 'a').write(str(args[0]) + '\\n')\n"
            "sys.addaudithook(_audit)\n" % str(self.opens), encoding="utf-8")
        catalog = json.loads((ROOT / ".claude/reference/external_models.json").read_text(encoding="utf-8"))
        catalog["transports"]["antigravity"] = {
            "imageRoute": {"state": "unverified"},
            "launcher": LAUNCHER, "costModel": "plan-quota", "capacityUnsupported": {"reason": "fixture"},
            "effortValues": ["low", "medium", "high", "xhigh"], "status": {"state": "available"}}
        catalog["models"] = [m for m in catalog["models"] if m["transport"] != "antigravity"] + [{
            "id": "antigravity/agy-test-model", "upstreamId": "agy-test-model", "alias": "agy-test", "input": ["text"],
            "transport": "antigravity", "label": "Test agy", "family": "agy-test-family", "version": "agy-test-model",
            "lifecycle": "current", "order": 9998, "costRank": 1, "prior": "mid", "status": {"state": "available"},
            "roles": ["fanout", "executor", "scout"],
            "limits": {"contextTokens": 1000, "maxOutputTokens": 100, "concurrency": None}, "_limitsSource": "fixture",
            "price": {"cacheHitPer1M": 0.0, "cacheMissPer1M": 0.0, "outputPer1M": 0.0, "currency": "USD",
                      "asOf": "2026-11-21", "source": "fixture"},
            "effort": {"evidence": "measured", "measuredVersion": "agy-test-model"}, "effortValues": ["low", "medium", "high", "xhigh"],
            "authTier": "open", "gate": {"minBand": "Surplus"}}]
        registry = self.work / "models.json"
        registry.write_text(json.dumps(catalog), encoding="utf-8")
        self.log = self.work / "calls.jsonl"
        self.cell = self.work / "cell"
        self.cell.mkdir()
        self.brief = self.work / "brief.md"
        self.brief.write_text("Report the first line of .claude/CLAUDE.md.\n", encoding="utf-8")
        self.env = dict(os.environ, AGY_BIN=native(self.bin), HARNESS_MODEL_REGISTRY=str(registry), TEST_AGY_LOG=str(self.log),
                        TEST_AGY_STREAM=str(FIX / "base.jsonl"), HOME=str(self.home), USERPROFILE=str(self.home),
                        PYTHONPATH=str(hook) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                        SIDECAR_LEDGER_PATH=str(self.work / "ledger.jsonl"),
                        SIDECAR_EXHAUSTED_DIR=str(self.work / "exhausted"), PYTHONIOENCODING="utf-8",
                        LAUNCH_MIN_COMMIT_GIB="0", LAUNCH_MIN_PHYSICAL_GIB="0", HARNESS_SIDECAR_PREFLIGHT="0")
        for key in ("CLAUDE_CODE_SESSION_ID", "SC_LAUNCHER_DIR", "SC_SNAPSHOT_OF", "SIDECAR_DETACHED"):
            self.env.pop(key, None)
        self.env["PROVIDER_CAPACITY_CACHE"] = _transport_fixture._available_capacity_cache()

    def launch(self, *args, env=None, timeout=300):
        return subprocess.run([BASH, LAUNCHER, *args], cwd=ROOT, env=env or self.env, text=True, encoding="utf-8",
                              errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)

    def run_job(self, kind="scout", env=None, extra=(), workdir=None):
        record = self.work / "job.record.json"
        got = self.launch("-m", "agy-test", "-e", "low", "-G", kind, "-d", native(workdir or self.cell), "-f", native(self.brief),
                          "-R", native(record), *extra, env=env)
        return got, record

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_check_is_ok_when_agy_lists_the_row(self):
        got = self.launch("--check", "-m", "agy-test")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
        self.assertIn("OK", got.stdout)

    def test_check_is_unavailable_signed_out_empty_or_without_the_id(self):
        for mode in ("signedout", "empty", "noid"):
            got = self.launch("--check", "-m", "agy-test", env=dict(self.env, TEST_AGY_MODELS=mode))
            self.assertNotEqual(got.returncode, 0, mode)
            self.assertIn("UNAVAILABLE", got.stdout, mode)
        signed_out = self.launch("--check", "-m", "agy-test", env=dict(self.env, TEST_AGY_MODELS="signedout"))
        self.assertIn("run agy once", signed_out.stdout)

    def test_a_scout_run_records_the_served_model_and_tokens_and_sends_the_rail(self):
        got, record = self.run_job()
        self.assertEqual(got.returncode, 0, got.stdout[-800:] + got.stderr[-1500:])
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["servedModel"], "agy-test-model")
        self.assertGreater((data.get("inputTokens") or 0) + (data.get("outputTokens") or 0), 0)
        run = [c for c in self.calls() if c["argv"][:1] != ["models"]][-1]
        self.assertIn("You run UNATTENDED", run["stdin"])
        self.assertIn("Report the first line", run["stdin"])
        argv = run["argv"]
        self.assertEqual(argv[argv.index("--effort") + 1], "low")
        self.assertIn("--disable-slash-commands", argv)
        self.assertIn("--settings", argv)

    def test_a_kind_gets_only_the_mcp_servers_its_profile_lists(self):
        # guards/tool_profiles.json: author lists semantic-search and ai-worker, not git.
        got, _record = self.run_job(kind="author", workdir=ROOT)
        self.assertEqual(got.returncode, 0, got.stderr[-800:])
        allow = [c for c in self.calls() if c["argv"][:1] != ["models"]][-1]["settings"]["permissions"]["allow"]
        self.assertIn("mcp(semantic-search/*)", allow)
        self.assertNotIn("mcp(git/*)", allow)

    def test_a_project_root_job_takes_its_guards_from_the_bridged_session_start(self):
        # The `.agents` workspace bridges SessionStart, whose rails hook reads the exported kind, so stdin repeats none.
        got, _record = self.run_job(kind="judge", workdir=ROOT)
        self.assertEqual(got.returncode, 0, got.stderr[-800:])
        run = [c for c in self.calls() if c["argv"][:1] != ["models"]][-1]
        self.assertNotIn("[delegate rails", run["stdin"])
        self.assertEqual(run["kind"], "judge")

    def test_a_full_disclosure_kind_carries_its_guards_on_stdin(self):
        # A root that is not this project gets no `.agents` workspace, so no project hook fires inside agy.
        got, _record = self.run_job(kind="judge")
        self.assertEqual(got.returncode, 0, got.stderr[-800:])
        run = [c for c in self.calls() if c["argv"][:1] != ["models"]][-1]
        self.assertIn("[delegate rails", run["stdin"])

    def test_an_ungoverned_tool_stops_the_run_and_exits_nonzero(self):
        start = time.time()
        got, _record = self.run_job(env=dict(self.env, TEST_AGY_STREAM=str(FIX / "deny_probe.jsonl"),
                                             TEST_AGY_LINGER="120"))
        self.assertNotEqual(got.returncode, 0)
        self.assertLess(time.time() - start, 100)
        self.assertIn("schedule", got.stderr)

    def test_a_quota_refusal_exits_10_and_marks_the_plan_until_its_reset(self):
        # The observed refusal: agy exits 3 with "Third-party quota reached ... Resets in
        # 95h34m36s". Contract code 3 means credential missing; this is a usage limit, and it must
        # stop launches on the plan until that reset.
        before = int(time.time())
        stream = self.work / "quota.stream"
        got, record = self.run_job(extra=("-P", native(stream)),
                                   env=dict(self.env, TEST_AGY_STREAM=str(FIX / "quota_refusal.jsonl"),
                                            TEST_AGY_EXIT="3"))
        self.assertEqual(got.returncode, 10, got.stdout[-800:] + got.stderr[-1500:])
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["exitCode"], 10)
        marker_path = self.work / "exhausted" / "antigravity.json"
        self.assertTrue(marker_path.exists(), got.stderr[-1200:])
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        reset = before + 95 * 3600 + 34 * 60 + 36
        self.assertLessEqual(abs(marker["resetsAt"] - reset), 120, marker)
        self.assertEqual(marker["expiresAt"], marker["resetsAt"])
        # One terminal result on the progress stream: the synthetic usage-limit line is written once.
        lines = [line for line in stream.read_text(encoding="utf-8").splitlines() if line.strip()]
        limit_results = [line for line in lines if '"provider_usage_limit"' in line]
        self.assertEqual(len(limit_results), 1, lines[-4:])

    def test_a_child_status_is_not_a_launcher_code(self):
        # Any child status other than 0/1 is reported as this launcher's own child-failure code 1;
        # passing agy's 3 through made the fan-out label a plain child failure "credential missing".
        got, record = self.run_job(env=dict(self.env, TEST_AGY_EXIT="3"))
        self.assertEqual(got.returncode, 1, got.stdout[-800:] + got.stderr[-1500:])
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["exitCode"], 1)

    def test_a_scout_in_the_own_checkout_runs_there_with_the_shell(self):
        got, _record = self.run_job(workdir=ROOT)
        self.assertEqual(got.returncode, 0, got.stderr[-800:])
        run = [c for c in self.calls() if c["argv"][:1] != ["models"]][-1]
        self.assertEqual(Path(run["cwd"]).resolve(), ROOT.resolve())

    def test_a_write_kind_in_the_own_checkout_runs_there(self):
        # The bridge runs the project's guards on every agy write; with no Claude transcript the ownership checks
        # read an empty session id and deny more, never less, so the own checkout needs no refusal.
        got, _record = self.run_job(kind="author", workdir=ROOT)
        self.assertEqual(got.returncode, 0, got.stderr[-800:])
        run = [c for c in self.calls() if c["argv"][:1] != ["models"]][-1]
        self.assertEqual(Path(run["cwd"]).resolve(), ROOT.resolve())

    def test_a_schema_outside_the_subset_is_refused_before_spawning(self):
        schema = self.work / "schema.json"
        schema.write_text(json.dumps({"type": "object", "anyOf": [{"required": ["a"]}]}), encoding="utf-8")
        got, _record = self.run_job(extra=("-S", native(schema)))
        self.assertEqual(got.returncode, 2, got.stderr[-600:])
        self.assertIn("anyOf", got.stderr)
        self.assertEqual([c for c in self.calls() if c["argv"][:1] != ["models"]], [])

    def test_each_extra_folder_reaches_agy_as_a_workspace_folder(self):
        extra = [self.work / "extra_a", self.work / "extra_b"]
        for folder in extra:
            folder.mkdir()
        got, _record = self.run_job(extra=("-a", native(extra[0]), "-a", native(extra[1])))
        self.assertEqual(got.returncode, 0, got.stderr[-800:])
        argv = [c for c in self.calls() if c["argv"][:1] != ["models"]][-1]["argv"]
        given = [Path(argv[i + 1]).resolve() for i, a in enumerate(argv) if a == "--add-dir"]
        self.assertEqual(given, [f.resolve() for f in extra])

    def test_resume_is_refused(self):
        got = self.launch("-m", "agy-test", "-r", "abc", "-G", "scout")
        self.assertEqual(got.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_no_python_the_launcher_starts_opens_the_gemini_login(self):
        got, _record = self.run_job()
        self.assertEqual(got.returncode, 0, got.stderr[-800:])
        self.assertFalse(self.opens.exists() and self.opens.read_text().strip(),
                         self.opens.read_text() if self.opens.exists() else "")
        self.assertTrue(self.sentinel.exists())
        models = [c for c in self.calls() if c["argv"][:1] == ["models"]]
        self.assertTrue(models and models[0]["home"] == str(self.home), models)


if __name__ == "__main__":
    unittest.main()
