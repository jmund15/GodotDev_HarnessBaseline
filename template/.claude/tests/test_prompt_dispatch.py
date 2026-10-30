#!/usr/bin/env python3
"""Re-runnable proof for hooks/prompt_dispatch.py, the UserPromptSubmit dispatcher.

One process runs eight hooks concurrently, each in its own thread with its own stdin/stdout/stderr
capture and its own budget, and prints their normalized text. Plan: .claude/plans/archive/
prompt-dispatch-consolidation.md, Verification cases 1-9; case 10 there (the live run through Git Bash)
was run by the driving session, and this file's own case 10 covers the close-out review's findings.

Real members run against a planted machine: HARNESS_HOOK_STATE_DIR, CLAUDE_PROJECT_DIR, TMP/TEMP/TMPDIR and
HARNESS_PROMPT_DISPATCH_TIMING all point at temp locations, so nothing under ~/.claude or the repo
cache is touched. Stub members live in a temp directory put on sys.path.

A case that prints SKIP is not a pass; it names the landing that will make it run.

    python3 .claude/tests/test_prompt_dispatch.py
"""
import ast
import contextlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import time

# The hook's plan-usage sampler writes here, never to the real ~/.claude/usage_samples.jsonl.
os.environ["HARNESS_USAGE_SAMPLES"] = os.path.join(tempfile.mkdtemp(prefix="pd_samples_"), "samples.jsonl")

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
HOOKS = os.path.join(CLAUDE, "hooks")
DISPATCH = os.path.join(HOOKS, "prompt_dispatch.py")

sys.path.insert(0, HERE)
import _settings_probe  # noqa: E402

EXPECTED_CHAIN = (
    ("prompt_memory_loader", 30),
    ("critical_analysis_reminder", 30),
    ("prompt_git_state_delta", 15),
    ("tool_routing_cumulative_reset", 15),
    ("tool_routing_prompt_synthesis", 15),
    ("shell_census", 25),
    ("sidecar_watch_rearm", 5),
    ("budget_posture", 15),
    ("activity_registry", 15),
    ("overnight_ask_guard", 5),
)

# Members with no reachable positive control: why each one is proven by `ok` status alone.
KNOWN_SILENT = {
    "shell_census": "its text comes from a live machine process table that main() lists itself; "
                    "no env seam plants one, and its emission logic is proven with injected process "
                    "lists in test_shell_census.py",
}

SYNTHESIS = """Compare the existing hook files and summarize how routing decisions flow across
multiple modules. I need the exact source evidence preserved, the current contract checked, and a
recommendation about which behavior should remain. Look at these files as one bounded investigation,
but distinguish copyable facts from architectural judgment and line-level edits. Do not assume that
file count or directory names decide the route."""

STUBS = {
    "pd_text": "def main():\n    print('plain text line')\n",
    "pd_json": ("import json\ndef main():\n    print(json.dumps({'hookSpecificOutput': "
                "{'hookEventName': 'UserPromptSubmit', 'additionalContext': 'reaper ctx'}}))\n"),
    "pd_empty": "def main():\n    print('{}')\n",
    "pd_noctx": "import json\ndef main():\n    print(json.dumps({'hookSpecificOutput': {}}))\n",
    "pd_silent": "def main():\n    pass\n",
    "pd_import_fault": "raise RuntimeError('boom-import')\n",
    "pd_main_fault": "def main():\n    print('leaked-partial')\n    raise ValueError('boom-main')\n",
    "pd_swallow": "def main():\n    print('kept-partial')\n    raise ValueError('boom-swallow')\n",
    "pd_exit2": "import sys\ndef main():\n    sys.exit(2)\n",
    "pd_exit0": "import sys\ndef main():\n    print('exit-zero-text')\n    sys.exit(0)\n",
    "pd_exit_none": "import sys\ndef main():\n    print('exit-none-text')\n    sys.exit(None)\n",
    "pd_good": "def main():\n    print('good-output')\n",
    "pd_slow": ("import os, time\ndef main():\n    print('slow-partial')\n"
                "    time.sleep(float(os.environ.get('PD_SLOW_SLEEP', '1.6')))\n"
                "    open(os.environ['PD_MARKER'], 'w').write('late')\n"),
    "pd_fast": "def main():\n    print('fast-output')\n",
    "pd_a": ("import json, sys, time\ndef main():\n    data = json.load(sys.stdin)\n"
             "    for i in range(300):\n        print('A-%d-%s' % (i, data['session_id']))\n"
             "        time.sleep(0.0002)\n"),
    "pd_b": ("import json, sys, time\ndef main():\n    data = json.load(sys.stdin)\n"
             "    for i in range(300):\n        print('B-%d-%s' % (i, data['session_id']))\n"
             "        time.sleep(0.0002)\n"),
    "pd_reconf": ("import sys\nsys.stdout.reconfigure(encoding='cp1252', errors='ignore')\n"
                  "def main():\n    print('na\\u00efve \\u2014 \\u201cquoted\\u201d \\u540d\\u524d')\n"),
    "pd_stderr": "import sys\ndef main():\n    print('warn-xyz', file=sys.stderr)\n",
    "pd_baseexc": "def main():\n    print('ki-partial')\n    raise KeyboardInterrupt()\n",
    "pd_late_swallow": ("import time\ndef main():\n    print('late-partial')\n    time.sleep(2.0)\n"
                        "    raise ValueError('late-boom')\n"),
    "pd_sleepy": "import time\ndef main():\n    time.sleep(3.0)\n    print('sleepy-out')\n",
    "pd_over": "import time\ndef main():\n    time.sleep(2.0)\n    print('over-out')\n",
    "pd_json_nocontext": ("import json\ndef main():\n    print(json.dumps({'hookSpecificOutput': "
                          "{'hookEventName': 'UserPromptSubmit'}, 'other': 1}))\n"),
    "pd_argv": "import os, sys\ndef main():\n    print(len(sys.argv), os.path.basename(sys.argv[0]))\n",
}

CTX = {}

STUBS_JSON_NOCONTEXT = '{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit"}, "other": 1}\n'

RUNNER = """import importlib.util, json, os, sys
sys.path.insert(0, os.environ["PD_TEST_STUBS"])
spec = importlib.util.spec_from_file_location("prompt_dispatch", os.environ["PD_TEST_DISPATCH"])
pd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pd)
_real_present = pd._optional_hooks.present
pd._optional_hooks.present = lambda name: _real_present(name) or os.path.isfile(os.path.join(os.environ["PD_TEST_STUBS"], name + ".py"))
pd.CHAIN = tuple((n, b) for n, b in json.loads(os.environ["PD_TEST_CHAIN"]))
if os.environ.get("PD_TEST_SWALLOW"):
    pd._SWALLOWS = frozenset(json.loads(os.environ["PD_TEST_SWALLOW"]))
pd.main()
"""


def load_dispatcher():
    if not os.path.isfile(DISPATCH):
        raise ImportError("hooks/prompt_dispatch.py does not exist")
    if HOOKS not in sys.path:
        sys.path.insert(0, HOOKS)
    spec = importlib.util.spec_from_file_location("prompt_dispatch", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def planted_machine(state, project, tmp, cwd, timing=None):
    """Env + tempdir + cwd for an in-process dispatch over the real members."""
    changes = {"HARNESS_HOOK_STATE_DIR": state, "CLAUDE_PROJECT_DIR": project, "TMP": tmp, "TEMP": tmp, "TMPDIR": tmp,
               "HARNESS_PROMPT_DISPATCH_TIMING": timing,
               "PROVIDER_CAPACITY_CACHE": os.path.join(tmp, "provider_capacity.json"), "HARNESS_CAPACITY_WARM": "0",
               "CLAUDE_CODE_ENTRYPOINT": None, "CLAUDE_CODE_TRANSPORT": None, "ANTHROPIC_BASE_URL": None}
    saved_env = {key: os.environ.get(key) for key in changes}
    saved_tempdir, saved_cwd = tempfile.tempdir, os.getcwd()
    for key, value in changes.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    tempfile.tempdir = tmp
    os.chdir(cwd)
    try:
        yield
    finally:
        os.chdir(saved_cwd)
        tempfile.tempdir = saved_tempdir
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def env_set(**changes):
    saved = {key: os.environ.get(key) for key in changes}
    for key, value in changes.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def write_file(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read_records(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError):
        return []


def payload_for(session, prompt="stub prompt", **extra):
    return json.dumps(dict({"session_id": session, "hook_event_name": "UserPromptSubmit",
                            "prompt": prompt}, **extra))


def statuses(record):
    return {name: entry.get("status") for name, entry in (record or {}).get("hooks", {}).items()}


def machine_env(state, project, tmp, timing):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, CLAUDE_PROJECT_DIR=project, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
               HARNESS_PROMPT_DISPATCH_TIMING=timing, PYTHONUTF8="1",
               # The posture hook reads provider bands from this cache; the live repo's goes Exhausted on its own clock.
               PROVIDER_CAPACITY_CACHE=os.path.join(tmp, "provider_capacity.json"), HARNESS_CAPACITY_WARM="0")
    for key in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TRANSPORT", "ANTHROPIC_BASE_URL"):
        env.pop(key, None)
    return env


def run_dispatcher(env, payload, cwd):
    result = subprocess.run([sys.executable, DISPATCH], input=payload.encode("utf-8"), capture_output=True,
                            env=env, cwd=cwd, timeout=120)
    return (result.returncode, result.stdout.decode("utf-8", "replace"),
            result.stderr.decode("utf-8", "replace"))


def run_runner(env, chain, payload, extra_env=None, cwd=None):
    """main() over a stub chain in a real process: the runner patches CHAIN, then calls main()."""
    full = dict(env, PD_TEST_DISPATCH=DISPATCH, PD_TEST_STUBS=CTX["stubs"], PD_TEST_CHAIN=json.dumps(chain),
                **(extra_env or {}))
    for key in ("PYTHONIOENCODING", "PYTHONUTF8"):
        full.pop(key, None)
    started = time.monotonic()
    result = subprocess.run([sys.executable, CTX["runner"]], input=payload.encode("utf-8"),
                            capture_output=True, env=full, cwd=cwd, timeout=60)
    elapsed = time.monotonic() - started
    return (result.returncode, result.stdout, result.stderr.decode("utf-8", "replace"), elapsed)


def write_capture(tmp, session, used_pct):
    """The rate_limits snapshot statusline.py writes every turn (half the window elapsed)."""
    now = time.time()
    write_file(os.path.join(tmp, "cc-cachestat-%s.json" % session), json.dumps({"rate_limits": {
        "captured_at": now,
        "seven_day": {"used_percentage": used_pct, "resets_at": now + 7 * 24 * 3600 / 2}}}))


def write_registry_record(tmp, pid, label):
    """A registry record of the shape regression_gate.ps1 writes."""
    write_file(os.path.join(tmp, "harness-activity", "gate-%d.json" % pid), json.dumps({
        "pid": pid, "kind": "gate", "checkout": "C:/other/checkout", "sessionId": None, "label": label,
        "startedAt": time.time(), "heartbeatAt": time.time(), "expectedSec": 300}))


def init_git_repo(path):
    subprocess.run(["git", "init", "-q", path], check=True, capture_output=True)
    write_file(os.path.join(path, "seed.txt"), "seed")
    write_file(os.path.join(path, ".git", "info", "exclude"), ".claude/\n")
    subprocess.run(["git", "-C", path, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
                    "-m", "seed"], check=True, capture_output=True)


def chain_source():
    with open(DISPATCH, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "CHAIN" for t in targets):
                return ast.literal_eval(node.value)
    return None


def main():
    cases = []

    def case(label, ok, skip=None):
        cases.append((label, ok, skip))

    try:
        pd = load_dispatcher()
    except Exception as exc:  # noqa: BLE001 - any import failure is the RED reason
        print("FAIL hooks/prompt_dispatch.py imports: %s: %s" % (type(exc).__name__, exc))
        print("\n0/1 cases pass")
        return 1

    root = tempfile.mkdtemp(prefix="pd_proof_")
    try:
        state, project, tmp, plain, stubs = (os.path.join(root, d) for d in
                                             ("state", "project", "tmp", "plain", "stubs"))
        for directory in (state, project, tmp, plain, stubs):
            os.makedirs(directory)
        for name, source in STUBS.items():
            write_file(os.path.join(stubs, name + ".py"), source)
        runner = os.path.join(root, "runner.py")
        write_file(runner, RUNNER)
        sys.path.insert(0, stubs)
        # The dispatcher runs only members that are installed hook files; the stub members count as installed.
        real_present = pd._optional_hooks.present
        pd._optional_hooks.present = lambda name: real_present(name) or os.path.isfile(os.path.join(stubs, name + ".py"))
        CTX.update(runner=runner, stubs=stubs)
        timing = os.path.join(root, "timing.jsonl")

        # ---- 1. every real member runs through the router ------------------------------------
        real_streams = (sys.stdin, sys.stdout, sys.stderr, list(sys.argv))
        stdout_errors = getattr(sys.stdout, "errors", None)
        with planted_machine(state, project, tmp, plain, timing):
            code, out, err = pd.dispatch(payload_for("exec-s1", "I think we should rename this", cwd=plain),
                                         timing_path=timing)
        records = read_records(timing)
        member_names = [name for name, _budget in pd.CHAIN]
        case("case 1: the real chain exits 0", code == 0)
        case("case 1: the timing record names every CHAIN member",
             bool(records) and list(records[-1].get("hooks", {})) == member_names)
        case("case 1: every real member's status is ok (%s)" % statuses(records[-1] if records else None),
             bool(records) and all(s == "ok" for s in statuses(records[-1]).values()))
        case("case 1: the dispatcher names no fault, timeout or exit on stderr", "[prompt-dispatch]" not in err)
        case("case 1: the router restored the real streams and argv",
             (sys.stdin, sys.stdout, sys.stderr, list(sys.argv)) == real_streams
             and getattr(sys.stdout, "errors", None) == stdout_errors)

        # ---- 2. positive controls: each member emits (or is named KNOWN_SILENT) --------------
        env = machine_env(state, project, tmp, timing)
        controls = {}

        def last_ok():
            record = (read_records(timing) or [None])[-1]
            return record is not None and all(s == "ok" for s in statuses(record).values())

        _, out, _ = run_dispatcher(env, payload_for("crit0001", "I think we should split this hook, right?"),
                                   plain)
        controls["critical_analysis_reminder"] = "Critically analyze" in out and last_ok()

        _, out, _ = run_dispatcher(env, payload_for("syn00001", SYNTHESIS), plain)
        lowered = out.lower()
        # Which route the bulk-copyable arm names is a per-machine fact (ai-worker registered or
        # not), so the control asserts the arm AND a route, not one machine's wording.
        controls["tool_routing_prompt_synthesis"] = (
            "bulk copyable" in lowered
            and ("read_files" in lowered or "bounded Read" in lowered) and last_ok())

        prompt = "Debug why the freeze effect is not working on critters"
        _, out, _ = run_dispatcher(env, payload_for("cum00001", prompt), plain)
        try:
            with open(os.path.join(state, "cum00001.json"), encoding="utf-8") as fh:
                planted_prompt = json.load(fh).get("last_prompt")
        except (OSError, ValueError):
            planted_prompt = None
        controls["tool_routing_cumulative_reset"] = planted_prompt == prompt and last_ok()
        controls["prompt_memory_loader"] = "HIGH-RISK TASK" in out and last_ok()

        write_capture(tmp, "bpc00001", 40.0)
        _, out, _ = run_dispatcher(env, payload_for("bpc00001", "next slice"), plain)
        case("case 2: spendable telemetry speaks at the first prompt, before any dispatch",
             "[budget-posture]" in out and last_ok())
        write_capture(tmp, "bpc00002", 100.0)
        _, out, _ = run_dispatcher(env, payload_for("bpc00002", "next slice"), plain)
        controls["budget_posture"] = "[budget-posture]" in out and "Exhausted" in out and last_ok()

        peer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            write_registry_record(tmp, peer.pid, "gate: final")
            _, out, _ = run_dispatcher(env, payload_for("arc00001", "next slice", cwd="C:/repo"), plain)
            controls["activity_registry"] = "[activity]" in out and "peer gate live" in out and last_ok()
        finally:
            peer.terminate()
            peer.wait(timeout=30)

        night = os.path.join(root, "night")
        marker = os.path.join(night, ".claude", "scratch", "overnight", "active-ovn00001.json")
        write_file(marker, json.dumps({"session_id": "ovn00001", "goal": "finish", "armed_at": time.time()}))
        _, out, _ = run_dispatcher(env, payload_for("ovn00001", "morning", cwd=night), plain)
        controls["overnight_ask_guard"] = ("[overnight]" in out and not os.path.exists(marker)
                                           and last_ok())

        repo = os.path.join(root, "repo")
        init_git_repo(repo)
        _, prime_out, _ = run_dispatcher(env, payload_for("gsd00001", "next slice"), repo)
        write_file(os.path.join(repo, "new.txt"), "planted change")
        _, delta_out, _ = run_dispatcher(env, payload_for("gsd00001", "next slice"), repo)
        _, quiet_out, _ = run_dispatcher(env, payload_for("gsd00001", "next slice"), repo)
        controls["prompt_git_state_delta"] = ("git-state-delta" not in prime_out
                                              and "git-state-delta" in delta_out
                                              and "untracked=1" in delta_out
                                              and "git-state-delta" not in quiet_out and last_ok())

        watch_record = os.path.join(root, "watch.record.json")
        write_file(watch_record + ".pid", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) +
                   (" winpid=%d" % os.getpid() if os.name == "nt" else "") + " pid=%d\n" % os.getpid())
        write_file(os.path.join(state, "wat00001.json"), json.dumps({"dispatch_table_pending": [
            {"kind": "sidecar", "id": watch_record, "record": watch_record,
             "jobs": [{"label": "watch"}], "launched_at": time.time()}]}))
        _, watch_out, _ = run_dispatcher(env, payload_for(
            "wat00001", "<task-notification><status>killed</status></task-notification>"), plain)
        controls["sidecar_watch_rearm"] = ("python3 .claude/tools/watch_records.py" in watch_out
                                             and watch_record in watch_out and last_ok())

        for name, _budget in pd.CHAIN:
            if name in KNOWN_SILENT:
                case("case 2: %s is KNOWN_SILENT (%s)" % (name, KNOWN_SILENT[name][:60]),
                     name not in controls and last_ok())
            else:
                case("case 2: %s emits under its positive control" % name, controls.get(name) is True)

        # ---- 3. normalizer -------------------------------------------------------------------
        with env_set(HARNESS_PROMPT_DISPATCH_TIMING=None):
            code, out, err = pd.dispatch(payload_for("norm0001"), chain=(
                ("pd_text", 5), ("pd_json", 5), ("pd_empty", 5), ("pd_noctx", 5), ("pd_silent", 5)),
                timing_path=os.path.join(root, "norm.jsonl"))
            case("case 3: plain text and a reaper-style JSON context join with newline, never JSON",
                 code == 0 and out == "plain text line\nreaper ctx")
            code, out, err = pd.dispatch(payload_for("norm0002"), chain=(
                ("pd_empty", 5), ("pd_noctx", 5), ("pd_silent", 5)),
                timing_path=os.path.join(root, "norm.jsonl"))
            case("case 3: a silent chain prints {}", code == 0 and out == "{}")

        # ---- 4. fault isolation --------------------------------------------------------------
        fault_timing = os.path.join(root, "fault.jsonl")
        fault_chain = (("pd_import_fault", 5), ("pd_main_fault", 5), ("pd_exit2", 5), ("pd_exit0", 5),
                       ("pd_exit_none", 5), ("pd_good", 5))
        code, out, err = pd.dispatch(payload_for("flt00001"), chain=fault_chain, timing_path=fault_timing)
        record = (read_records(fault_timing) or [None])[-1]
        case("case 4: exit code 0 and every healthy member's output is intact",
             code == 0 and out == "exit-zero-text\nexit-none-text\ngood-output")
        case("case 4: a faulting member's partial output is discarded", "leaked-partial" not in out)
        case("case 4: import fault, main fault and exit 2 are named on stderr",
             all(token in err for token in ("pd_import_fault", "boom-import", "pd_main_fault", "boom-main",
                                            "pd_exit2")))
        case("case 4: no traceback reaches stdout", "Traceback" not in out)
        case("case 4: the record classifies fault, fault, exit2, ok, ok, ok (%s)" % statuses(record),
             statuses(record) == {"pd_import_fault": "fault", "pd_main_fault": "fault", "pd_exit2": "exit2",
                                  "pd_exit0": "ok", "pd_exit_none": "ok", "pd_good": "ok"})
        saved_swallow = getattr(pd, "_SWALLOWS", None)
        pd._SWALLOWS = frozenset({"pd_swallow"})
        try:
            code, out, err = pd.dispatch(payload_for("flt00002"), chain=(("pd_swallow", 5), ("pd_good", 5)),
                                         timing_path=fault_timing)
        finally:
            if saved_swallow is not None:
                pd._SWALLOWS = saved_swallow
        record = (read_records(fault_timing) or [None])[-1]
        case("case 4: a swallowing member keeps its printed output and is still recorded as a fault",
             out == "kept-partial\ngood-output" and statuses(record).get("pd_swallow") == "fault")
        case("case 4: the real swallowing members are the two whose __main__ block swallows",
             saved_swallow == frozenset({"budget_posture", "activity_registry"}))
        case("case 4: streams are restored after a faulting chain",
             (sys.stdin, sys.stdout, sys.stderr, list(sys.argv)) == real_streams)

        # ---- 5. budget and hard exit ---------------------------------------------------------
        slow_timing = os.path.join(root, "slow.jsonl")
        marker = os.path.join(root, "late.marker")
        env5 = machine_env(state, project, tmp, slow_timing)
        code, out, err, elapsed = run_runner(env5, [("pd_slow", 1), ("pd_fast", 5)], payload_for("slw00001"),
                                             extra_env={"PD_MARKER": marker}, cwd=plain)
        time.sleep(1.5)
        record = (read_records(slow_timing) or [None])[-1]
        case("case 5: main() returns 0 within budget + 1 s (%.2f s)" % elapsed,
             code == 0 and elapsed < 2.0)
        case("case 5: the healthy member is delivered and the abandoned member's output is not",
             out.decode("utf-8", "replace") == "fast-output")
        case("case 5: the laggard is named on stderr and recorded as timeout",
             "pd_slow" in err and "timeout" in err and statuses(record).get("pd_slow") == "timeout"
             and statuses(record).get("pd_fast") == "ok")
        case("case 5: the abandoned member never runs to completion (no late marker)",
             not os.path.exists(marker))
        code, out, err, elapsed = run_runner(env5, [("pd_slow", 5), ("pd_fast", 5)], payload_for("slw00002"),
                                             extra_env={"PD_MARKER": marker, "PD_SLOW_SLEEP": "0.1"}, cwd=plain)
        case("case 5: control: a member finishing inside its budget delivers and writes its marker",
             out.decode("utf-8", "replace") == "slow-partial\nfast-output" and os.path.exists(marker))

        # ---- 6. no cross-talk ----------------------------------------------------------------
        errors_before = getattr(sys.stdout, "errors", None)
        code, out, err = pd.dispatch(payload_for("xt000001"), chain=(("pd_a", 10), ("pd_b", 10)),
                                     timing_path=os.path.join(root, "xt.jsonl"))
        lines = out.split("\n")
        case("case 6: two concurrent large outputs do not interleave and each read its own stdin",
             len(lines) == 600 and lines[:300] == ["A-%d-xt000001" % i for i in range(300)]
             and lines[300:] == ["B-%d-xt000001" % i for i in range(300)])
        code, out, err = pd.dispatch(payload_for("xt000002"), chain=(("pd_stderr", 5), ("pd_argv", 5)),
                                     timing_path=os.path.join(root, "xt.jsonl"))
        case("case 6: a member's stderr lands in the returned stderr, never stdout",
             "warn-xyz" in err and "warn-xyz" not in out)
        case("case 6: sys.argv is [dispatcher path] for members", out == "1 prompt_dispatch.py")
        code, out, err = pd.dispatch(payload_for("xt000003"), chain=(("pd_reconf", 5),),
                                     timing_path=os.path.join(root, "xt.jsonl"))
        case("case 6: an import-time reconfigure acts on the capture, not the real stdout",
             getattr(sys.stdout, "errors", None) == errors_before)
        code, out, err, _ = run_runner(machine_env(state, project, tmp, os.path.join(root, "xt.jsonl")),
                                       [("pd_reconf", 5)], payload_for("xt000004"), cwd=plain)
        case("case 6: non-ASCII output reaches a cp1252-default pipe as UTF-8",
             out.decode("utf-8") == "na\u00efve \u2014 \u201cquoted\u201d \u540d\u524d")

        # ---- 7. timing log and reader --------------------------------------------------------
        log = os.path.join(root, "log", "t.jsonl")
        chain7 = (("pd_text", 5), ("pd_good", 5))
        pd.dispatch(payload_for("tim00001"), chain=chain7, timing_path=log)
        pd.dispatch(payload_for("tim00002"), chain=chain7, timing_path=log)
        records = read_records(log)
        case("case 7: one record per dispatch, a second run appends", len(records) == 2)
        good = bool(records) and all(
            set(r) >= {"ts", "session", "wall_ms", "hooks"} and list(r["hooks"]) == ["pd_text", "pd_good"]
            and all({"ms", "status"} <= set(e) and isinstance(e["ms"], int) and e["status"] == "ok"
                    for e in r["hooks"].values()) for r in records)
        case("case 7: each record carries ts, session, wall_ms and {ms, status} for every member", good)
        case("case 7: the session field is the payload's session id prefix",
             bool(records) and records[0]["session"] == "tim00001"[:8])

        default_project = os.path.join(root, "default_proj")
        with env_set(HARNESS_PROMPT_DISPATCH_TIMING=None, CLAUDE_PROJECT_DIR=default_project):
            pd.dispatch(payload_for("tim00003"), chain=chain7)
        case("case 7: the default path is <CLAUDE_PROJECT_DIR>/logs/prompt_dispatch_timing.jsonl (the "
             "_hook_state.log_path home)",
             len(read_records(os.path.join(default_project, "logs", "prompt_dispatch_timing.jsonl"))) == 1)
        env_log = os.path.join(root, "env_log.jsonl")
        with env_set(HARNESS_PROMPT_DISPATCH_TIMING=env_log):
            pd.dispatch(payload_for("tim00004"), chain=chain7)
        case("case 7: HARNESS_PROMPT_DISPATCH_TIMING overrides the path", len(read_records(env_log)) == 1)

        big = os.path.join(root, "big.jsonl")
        write_file(big, "".join('{"pad":"%s"}\n' % ("x" * 1000) for _ in range(3000)))
        pd.dispatch(payload_for("tim00005"), chain=chain7, timing_path=big)
        rotated = read_records(big)
        case("case 7: an oversized log rotates to its newest lines and keeps the new record",
             len(rotated) <= 501 and "hooks" in rotated[-1] and rotated[-1]["session"] == "tim00005"[:8])

        blocked = os.path.join(root, "blocker.txt")
        write_file(blocked, "a file, not a directory")
        code, out, err = pd.dispatch(payload_for("tim00006"), chain=chain7,
                                     timing_path=os.path.join(blocked, "sub", "log.jsonl"))
        case("case 7: an unwritable timing log never changes the output (fail-open)",
             code == 0 and out == "plain text line\ngood-output")

        report_log = os.path.join(root, "report.jsonl")
        planted = []
        for i in range(12):
            planted.append({"ts": "2026-09-20T00:00:%02dZ" % i, "session": "rep00001", "wall_ms": 100 * (i + 1),
                            "hooks": {"alpha": {"ms": 10 * (i + 1),
                                                "status": "timeout" if i in (10, 11) else "ok"},
                                      "beta": {"ms": 5, "status": "ok"}}})
        write_file(report_log, "".join(json.dumps(r) + "\n" for r in planted) + "not json\n")
        with env_set(HARNESS_PROMPT_DISPATCH_TIMING=report_log):
            text = pd.report(10)
        member_lines = [ln for ln in text.splitlines() if re.match(r"^\w+\s+n=", ln)]
        alpha = next((ln for ln in member_lines if ln.startswith("alpha")), "")
        stats = re.search(r"median=(\d+)ms p95=(\d+)ms non_ok=(\d+)", alpha)
        case("case 7: report(N) prints one line per member seen in the last N records",
             [ln.split()[0] for ln in member_lines] == ["alpha", "beta"])
        # last 10 records: alpha ms 30..120 -> median 75, nearest-rank p95 120; two timeouts
        case("case 7: report(N) carries median, p95 and non-ok count over the last N (%s)" % (alpha.strip(),),
             bool(stats) and stats.groups() == ("75", "120", "2"))
        with env_set(HARNESS_PROMPT_DISPATCH_TIMING=os.path.join(root, "absent.jsonl")):
            empty_text = pd.report(5)
        case("case 7: report(N) over a missing log says so instead of raising", "no timing" in empty_text.lower())
        cli = subprocess.run([sys.executable, DISPATCH, "--report", "5"], capture_output=True, timeout=60,
                             env=dict(os.environ, HARNESS_PROMPT_DISPATCH_TIMING=report_log))
        cli_out = cli.stdout.decode("utf-8", "replace")
        case("case 7: --report N prints the report and exits 0 without dispatching",
             cli.returncode == 0 and "alpha" in cli_out and "beta" in cli_out)

        # ---- 8. the lock excludes threads of one process -------------------------------------
        from _hook_state import update_json_locked  # noqa: E402
        counter = os.path.join(root, "counter.json")

        def bump(state_doc):
            state_doc["n"] = state_doc.get("n", 0) + 1

        def worker():
            give_up = time.monotonic() + 60
            for _ in range(50):
                while not update_json_locked(counter, bump)[0] and time.monotonic() < give_up:
                    pass

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        try:
            with open(counter, encoding="utf-8") as fh:
                final = json.load(fh).get("n")
        except (OSError, ValueError):
            final = None
        case("case 8: two threads x 50 locked increments land exactly 100 (got %s)" % final, final == 100)

        # ---- H. hardening from the close-out reviews -----------------------------------------
        bx_timing = os.path.join(root, "bx.jsonl")
        code, out, err = pd.dispatch(payload_for("bx000001"), chain=(("pd_baseexc", 5), ("pd_good", 5)),
                                     timing_path=bx_timing)
        record = (read_records(bx_timing) or [None])[-1]
        case("hardening: a BaseException from a member is a fault, its partial output dropped, the rest delivered",
             code == 0 and out == "good-output" and statuses(record).get("pd_baseexc") == "fault"
             and "pd_baseexc" in err)

        late_timing = os.path.join(root, "late.jsonl")
        env10 = machine_env(state, project, tmp, late_timing)
        code, out, err, elapsed = run_runner(
            env10, [("pd_late_swallow", 1), ("pd_sleepy", 4)], payload_for("lt000001"),
            extra_env={"PD_TEST_SWALLOW": json.dumps(["pd_late_swallow"])}, cwd=plain)
        record = (read_records(late_timing) or [None])[-1]
        case("hardening: an abandoned swallowing member stays timeout and its output stays dropped when it "
             "finishes later (%r)" % out.decode("utf-8", "replace"),
             out.decode("utf-8", "replace") == "sleepy-out"
             and statuses(record).get("pd_late_swallow") == "timeout")

        over_timing = os.path.join(root, "over.jsonl")
        env10b = machine_env(state, project, tmp, over_timing)
        code, out, err, elapsed = run_runner(env10b, [("pd_sleepy", 4), ("pd_over", 1)], payload_for("ov000001"),
                                             cwd=plain)
        record = (read_records(over_timing) or [None])[-1]
        case("hardening: a member that finished past its own budget, behind a longer-budget member, is a "
             "timeout with its output dropped (%r)" % out.decode("utf-8", "replace"),
             out.decode("utf-8", "replace") == "sleepy-out" and statuses(record).get("pd_over") == "timeout"
             and statuses(record).get("pd_sleepy") == "ok")

        saved_writer, saved_budget = pd._write_timing, getattr(pd, "_TIMING_WRITE_BUDGET", None)
        pd._write_timing = lambda *args, **kwargs: time.sleep(5)
        pd._TIMING_WRITE_BUDGET = 0.3
        try:
            began = time.monotonic()
            code, out, err = pd.dispatch(payload_for("tw000001"), chain=(("pd_good", 5),),
                                         timing_path=os.path.join(root, "tw.jsonl"))
            waited = time.monotonic() - began
        finally:
            pd._write_timing = saved_writer
            if saved_budget is None:
                del pd._TIMING_WRITE_BUDGET
            else:
                pd._TIMING_WRITE_BUDGET = saved_budget
        case("hardening: a stalled timing write delays delivery by at most its own budget (%.2f s)" % waited,
             code == 0 and out == "good-output" and waited < 2.0)

        def swallows_in_main_block(path):
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in tree.body:
                if isinstance(node, ast.If) and "__main__" in ast.dump(node.test):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Try) and any(
                                isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in sub.handlers):
                            if any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "main"
                                   for part in sub.body for n in ast.walk(part)):
                                return True
            return False

        planted_swallow = os.path.join(root, "planted_swallow.py")
        planted_plain = os.path.join(root, "planted_plain.py")
        write_file(planted_swallow, "def main():\n    pass\n\nif __name__ == '__main__':\n    try:\n"
                                    "        main()\n    except Exception:\n        pass\n")
        write_file(planted_plain, "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n")
        case("hardening: the swallow detector fires on a planted swallowing block and not on a plain one",
             swallows_in_main_block(planted_swallow) and not swallows_in_main_block(planted_plain))
        detected = {name for name, _budget in pd.CHAIN
                    if swallows_in_main_block(os.path.join(HOOKS, name + ".py"))}
        case("hardening: _SWALLOWS is exactly the CHAIN members whose own __main__ block swallows (%s)"
             % sorted(detected), detected == set(pd._SWALLOWS))

        parity_prompt = "I think we should split this hook, right?"
        parity_members = (("prompt_memory_loader", 30), ("critical_analysis_reminder", 30))
        alone_state = os.path.join(root, "alone_state")
        os.makedirs(alone_state, exist_ok=True)
        alone_env = machine_env(alone_state, project, tmp, os.path.join(root, "alone.jsonl"))
        alone_parts = []
        for member, _budget in parity_members:
            result = subprocess.run([sys.executable, os.path.join(HOOKS, member + ".py")],
                                    input=payload_for("par00001", parity_prompt, cwd=plain).encode("utf-8"),
                                    capture_output=True, env=alone_env, cwd=plain, timeout=60)
            text = result.stdout.decode("utf-8", "replace").replace("\r\n", "\n").strip()
            if text and text != "{}":
                alone_parts.append(text)
        joined_state = os.path.join(root, "joined_state")
        os.makedirs(joined_state, exist_ok=True)
        with planted_machine(joined_state, project, tmp, plain, os.path.join(root, "joined.jsonl")):
            code, out, err = pd.dispatch(payload_for("par00001", parity_prompt, cwd=plain), chain=parity_members,
                                         timing_path=os.path.join(root, "joined.jsonl"))
        case("hardening: dispatcher text equals the standalone hooks' text modulo line endings",
             bool(alone_parts) and out == "\n".join(alone_parts))

        for label, raw in (("empty stdin", ""), ("malformed JSON", "not json {"), ("a JSON list", "[]"),
                           ("JSON null", "null"), ("a bare number", "123")):
            code, out, err = run_dispatcher(machine_env(state, project, tmp, os.path.join(root, "adv.jsonl")),
                                            raw, plain)
            noted = set(re.findall(r"\[prompt-dispatch\] (\w+) ", err))
            case("hardening: main() over the real chain survives %s (exit %s, no traceback in stdout, notes from %s)"
                 % (label, code, sorted(noted)), code == 0 and "Traceback" not in out and out.strip() != "")

        # ---- H2. session-audit dispositions --------------------------------------------------
        render_a, render_b = pd._Member("pd_text", 5, ""), pd._Member("pd_json_nocontext", 5, "")
        render_a.out.write("plain text line\n")
        render_b.out.write(STUBS_JSON_NOCONTEXT)
        parts, notes = pd._render([render_a, render_b])
        case("hardening: _render is pure: text kept, a JSON object with no additionalContext dropped and named",
             parts == ["plain text line"] and any("pd_json_nocontext" in n and "dropped" in n for n in notes))
        timed_out = pd._Member("pd_sleepy", 7, "")
        timed_out.status, timed_out.ms = "timeout", 7000
        case("hardening: _render drops a timed-out member's output and names its budget",
             pd._render([timed_out]) == ([], ["[prompt-dispatch] pd_sleepy timeout: exceeded 7 s\n"]))

        bad_log = os.path.join(root, "not_a_dir", "log.jsonl")
        write_file(os.path.join(root, "not_a_dir"), "a file, so the log path cannot be created")
        code, out, err = pd.dispatch(payload_for("tl000001"), chain=(("pd_good", 5),), timing_path=bad_log)
        case("hardening: a timing log that cannot be written is named on stderr and never changes delivery",
             code == 0 and out == "good-output" and "timing log write failed" in err)

        usage = subprocess.run([sys.executable, DISPATCH, "--reprot"], input=b"{}", capture_output=True, timeout=30)
        case("hardening: an unknown argument prints usage and exits 2 without running the hooks",
             usage.returncode == 2 and b"usage:" in usage.stderr and usage.stdout == b"")

        ab_timing = os.path.join(root, "ab.jsonl")
        code, out, err, elapsed = run_runner(machine_env(state, project, tmp, ab_timing),
                                             [("pd_late_swallow", 1), ("pd_sleepy", 4)], payload_for("ab000001"),
                                             cwd=plain)
        record = (read_records(ab_timing) or [None])[-1]
        case("hardening: an abandoned member is recorded at its budget, not at the moment the joiner gave up",
             (record or {}).get("hooks", {}).get("pd_late_swallow", {}).get("ms") == 1000)

        scan_patterns = (r"sys\.__stdout__", r"os\.write\(\s*1\b", r"redirect_std(?:out|err)",
                         r"sys\.std(?:out|err)\s*=[^=]", r"os\.chdir\(", r"\batexit\b", r"signal\.signal\(")

        def module_stream_rebind(text):
            """True when a module rebinds sys.stdout/sys.stderr at import time. A function-local rebind runs
            only when that function is called, and the dispatcher only imports its members: sidecar_fanout's
            `main` tees its own stdout into launch-refused.txt, which no in-process dispatcher call reaches."""
            try:
                tree = ast.parse(text)
            except SyntaxError:
                return True
            for node in tree.body:
                targets = node.targets if isinstance(node, ast.Assign) else (
                    [node.target] if isinstance(node, ast.AnnAssign) else [])
                for target in targets:
                    for sub in ast.walk(target):
                        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                                and sub.value.id == "sys" and sub.attr in ("stdout", "stderr")):
                            return True
            return False

        def local_module_files(name, seen):
            path = None
            for base in (HOOKS, os.path.join(CLAUDE, "tools")):
                if os.path.isfile(os.path.join(base, name + ".py")):
                    path = os.path.join(base, name + ".py")
                    break
            if path is None or path in seen:
                return
            seen.add(path)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        local_module_files(alias.name.split(".")[0], seen)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    local_module_files(node.module.split(".")[0], seen)

        reachable = set()
        for name, _budget in pd.CHAIN:
            local_module_files(name, reachable)
        scan_hits = []
        for path in sorted(reachable):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for pattern in scan_patterns:
                hit = (module_stream_rebind(text) if pattern == r"sys\.std(?:out|err)\s*=[^=]"
                       else re.search(pattern, text))
                if hit:
                    scan_hits.append("%s:%s" % (os.path.basename(path), pattern))
        case("hardening: no CHAIN member or local module it imports rebinds a stream, writes fd 1, chdirs or "
             "registers atexit/signal handlers (%d files scanned; hits %s)" % (len(reachable), scan_hits),
             not scan_hits and len(reachable) > len(pd.CHAIN))
        planted_bad = os.path.join(root, "planted_bad.py")
        write_file(planted_bad, "import contextlib, io\nwith contextlib.redirect_stdout(io.StringIO()):\n    pass\n")
        with open(planted_bad, encoding="utf-8") as fh:
            planted_text = fh.read()
        case("hardening: the stream-rebinding scan fires on a planted redirect_stdout",
             any(re.search(pattern, planted_text) for pattern in scan_patterns))

        # ---- 9. registration and CHAIN contract ----------------------------------------------
        literal = chain_source()
        case("case 9: CHAIN is a plain literal that ast.literal_eval reads", literal == pd.CHAIN)
        case("case 9: CHAIN names exactly the nine expected members with their budgets",
             literal == EXPECTED_CHAIN)
        case("case 9: every CHAIN module exists",
             all(os.path.isfile(os.path.join(HOOKS, name + ".py")) for name, _b in pd.CHAIN))
        offenders = []
        for name, _budget in pd.CHAIN:
            with open(os.path.join(HOOKS, name + ".py"), encoding="utf-8") as fh:
                text = fh.read()
            if "sys.__stdout__" in text or "os.write(1" in text:
                offenders.append(name)
        case("case 9: no CHAIN module writes past the router (sys.__stdout__ / os.write(1) %s" % offenders,
             not offenders)
        case("case 9: the reaper is not a CHAIN member", "runaway_scan_reaper" not in dict(pd.CHAIN))

        with open(_settings_probe.settings_path(CLAUDE), encoding="utf-8") as fh:
            settings = json.load(fh)
        commands = [(hook.get("command", ""), hook.get("timeout"))
                    for group in settings["hooks"]["UserPromptSubmit"] for hook in group.get("hooks", [])]
        dispatcher_rows = [row for row in commands if "prompt_dispatch.py" in row[0]]
        skip = None
        case("case 9: UserPromptSubmit contains prompt_dispatch.py exactly once",
             len(dispatcher_rows) == 1, skip)
        case("case 9: UserPromptSubmit contains runaway_scan_reaper.py exactly once",
             sum("runaway_scan_reaper.py" in cmd for cmd, _t in commands) == 1, skip)
        case("case 9: UserPromptSubmit registers no command outside the dispatcher and the reaper",
             all("prompt_dispatch.py" in cmd or "runaway_scan_reaper.py" in cmd for cmd, _t in commands), skip)
        case("case 9: the registered dispatcher timeout exceeds the largest CHAIN budget",
             bool(dispatcher_rows) and isinstance(dispatcher_rows[0][1], (int, float))
             and dispatcher_rows[0][1] > max(budget for _n, budget in pd.CHAIN), skip)
        git_bash = "C:/Program Files/Git/usr/bin/bash.exe"
        live_skip = None if os.path.isfile(git_bash) else "SKIP (Git Bash not found at %s)" % git_bash
        live_ok = False
        if live_skip is None and dispatcher_rows:
            live_env = machine_env(state, project, tmp, os.path.join(root, "live.jsonl"))
            # The registered command resolves the dispatcher from CLAUDE_PROJECT_DIR, and its members write caches
            # under it: run a copy of the hooks in a temp project so the checkout's .claude/.cache stays untouched.
            live_project = os.path.join(root, "live_project")
            shutil.copytree(os.path.join(CLAUDE, "hooks"), os.path.join(live_project, ".claude", "hooks"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            live_env["CLAUDE_PROJECT_DIR"] = live_project.replace("\\", "/")
            began = time.monotonic()
            live = subprocess.run([git_bash, "-c", dispatcher_rows[0][0]],
                                  input=payload_for("live0001", "I think we should look", cwd=plain).encode("utf-8"),
                                  capture_output=True, env=live_env, cwd=plain, timeout=60)
            live_out = live.stdout.decode("utf-8", "replace")
            live_ok = (live.returncode == 0 and "Traceback" not in live_out and live_out.strip() != ""
                       and time.monotonic() - began < 10)
        case("case 10: the registered command runs through Git Bash: exit 0, output present, under 10 s",
             live_ok, live_skip)
    finally:
        sys.path[:] = [p for p in sys.path if p != os.path.join(root, "stubs")]
        shutil.rmtree(root, ignore_errors=True)

    failures = [label for label, ok, skip in cases if skip is None and not ok]
    skipped = [label for label, ok, skip in cases if skip is not None and not ok]
    for label, ok, skip in cases:
        if skip is not None and not ok:
            print("%-4s %s [%s]" % ("SKIP", skip, label))
        else:
            print("%-4s %s" % ("ok" if ok else "FAIL", label))
    ran = len(cases) - len(skipped)
    print("\n%d/%d cases pass%s" % (ran - len(failures), ran,
                                  " (%d skipped)" % len(skipped) if skipped else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
