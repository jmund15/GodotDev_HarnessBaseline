"""Re-runnable proof for hooks/wide_test_guard.py, hooks/_wide_runs.py and the `gate_cadence` rows
they read (harness plan `verify-cadence-harness.md` S1). A project's own `gate_cadence` rows are proved
by its local `test_wide_test_guard_project.py`, where it has one.

Planted: a project root whose `adaptation.pure.defaults.json` holds the harness `gate_cadence` rows,
wrapper scripts and transcripts under a temp root. Direct cases call `guard.hook`; the wiring cases
send real PreToolUse payloads through `hooks/pre_bash_dispatch.py` and read the emitted channel.

The payload envelope is `fixtures/wide_test_guard/lane_author_payload.json`, cut from one live lane job's
Bash PreToolUse payload (tool_use `toolu_01DKTDs1wXERCNZFiLU9BGdQ`, client 2.1.327, `sdk-cli`, `permissionMode: auto`), trimmed and with
session id and paths renamed. `agent_id` and `agent_type` are the job record's live `toolProfile.agentType`
(`delegate-author`); each case overrides the fields it tests.

    python3 .claude/tests/test_wide_test_guard.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
HOOKS = os.path.join(CLAUDE, "hooks")
sys.path.insert(0, HOOKS)
sys.path.insert(0, HERE)
import _wide_runs as wide_runs  # noqa: E402
import wide_test_guard as guard  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wide_guard_")
CLOSE = "python3 .claude/scripts/harness_tests.py --close"
RUNNER_ROW = {"runner": r"(?:(?:python3?|py)\s+)?(?:\S*/)?harness_tests\.py\b",
              "exempt": ["^--help$"], "narrow": None}
ROW_TABLE = {"slice": ["for `.claude/**` units: the edited unit's proof file, run directly"],
             "merge": [], "close": ["python3 .claude/scripts/harness_tests.py --close"],
             "wide": [RUNNER_ROW]}
GODOT_TABLE = {
    "slice": [], "merge": [], "close": [],
    "wide": [
        {"runner": r"(?:\S*/)?run_test_suite\.ps1\b", "exempt": [],
         "narrow": {"declared": {"arg": "Filter", "glob": "Tests/**/*.cs",
                                 "decl": [r"class\s+(\w+)", r"(?:void|Task)\s+(\w+)\s*\("],
                                 "admit": [r"(?:^|\.)Tests\.Logic\.Lint\.?$"]}}},
        {"runner": r"(?:\S*/)?regression_gate\.ps1\b",
         "exempt": [r"(?i)^-?staticonly$"], "narrow": None},
        {"runner": r"unitrun\s+test\b", "exempt": [],
         "narrow": {"declared": {"arg": "filter", "glob": "Tests/**/*.cs",
                                 "decl": [r"class\s+(\w+)", r"(?:void|Task)\s+(\w+)\s*\("],
                                 "admit": []}}},
    ]}

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append("%s %s" % (name, detail))


def write(name, text):
    path = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path.replace("\\", "/")


def planted_root(name, table, where="adaptation.pure.defaults.json"):
    root = os.path.join(TMP, "roots", name)
    skill = os.path.join(root, ".claude", "skills", "project_subsystems")
    os.makedirs(skill, exist_ok=True)
    with open(os.path.join(skill, where), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"gate_cadence": table}, fh)
    return root


def clear_env():
    for key in ("CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_KIND", "CLAUDE_CODE_SIDECAR_PROMPT_FILE"):
        os.environ.pop(key, None)


with open(os.path.join(HERE, "fixtures", "wide_test_guard", "lane_author_payload.json"),
          encoding="utf-8") as _fh:
    LIVE_ENVELOPE = json.load(_fh)


def payload(command, agent_id="a1", agent_type="delegate-author", tool="Bash", cwd=None):
    data = dict(LIVE_ENVELOPE, tool_name=tool, cwd=cwd or guard.ROOT,
                tool_input={"command": command})
    for key, value in (("agent_id", agent_id), ("agent_type", agent_type)):
        if value:
            data[key] = value
        else:
            data.pop(key, None)
    return data


def verdict(command, **kwargs):
    return guard.hook(payload(command, **kwargs))


def case_classifier():
    root = guard.ROOT
    check("the close runner classifies as wide", bool(wide_runs.classify(CLOSE, TMP, root)))
    check("a --help run of the runner is exempt",
          wide_runs.classify("python3 .claude/scripts/harness_tests.py --help", TMP, root) == [])
    for command in ("grep -n harness_tests.py .claude/scripts/harness_tests.py",
                    "cat .claude/scripts/harness_tests.py",
                    "git show HEAD:.claude/scripts/harness_tests.py | head -5",
                    "echo run python3 .claude/scripts/harness_tests.py --close",
                    "wc -l .claude/scripts/harness_tests.py"):
        check("a mention is not a run: " + command,
              wide_runs.classify(command, TMP, root) == [], str(wide_runs.classify(command, TMP, root)))
    wide = wide_runs.classify(CLOSE, TMP, root)
    check("the runner name is reported", wide and "harness_tests.py" in wide[0]["runner"], str(wide))
    check("the reason names the row", wide and wide[0]["reason"], str(wide))


def case_gate_type_owner():
    """The gate kind's agent type is the `guards/tool_profiles.json` row, never a copied constant."""
    import tool_profiles
    row = tool_profiles.load_table()["kinds"]["gate"]["agentType"]
    check("the gate type is the kind row's", wide_runs.gate_agent_type(tool_profiles.load_table()) == row)
    check("no table yields no gate type", wide_runs.gate_agent_type(None) is None)
    check("a native job is refused without the table",
          wide_runs.gate_caller({"agent_id": "a1", "agent_type": row}, {}, None) is False)
    check("the root session is admitted without the table",
          wide_runs.gate_caller({}, {}, None) is True)


def case_unreadable_table():
    """An unreadable kinds table refuses a native gate job loudly; the root session needs no table."""
    clear_env()
    import tool_profiles
    saved = tool_profiles.TABLE_PATH
    try:
        tool_profiles.TABLE_PATH = write("tool_profiles.json", "{ not json")
        denied = verdict(CLOSE, agent_type="delegate-gate")
        check("an unreadable table refuses a native gate job", bool(denied), str(denied))
        check("the refusal names the table", bool(denied) and "tool_profiles.json" in denied, str(denied))
        check("an unreadable table still admits the root session",
              verdict(CLOSE, agent_id=None, agent_type=None) is None)
    finally:
        tool_profiles.TABLE_PATH = saved


def case_delegate_verdicts():
    clear_env()
    denied = verdict(CLOSE)
    check("a delegate-author job's close run is refused", bool(denied), str(denied))
    check("the deny names the runner", bool(denied) and "harness_tests.py" in denied, str(denied))
    check("the deny names the job's checks line",
          bool(denied) and "the edited unit's proof file" in denied, str(denied))
    check("the deny names the next step", bool(denied) and "gate" in denied and "dispatch" in denied, str(denied))
    check("a root session's close run is admitted", verdict(CLOSE, agent_id=None, agent_type=None) is None)
    check("a delegate-gate job's close run is admitted", verdict(CLOSE, agent_type="delegate-gate") is None)
    check("a general-purpose subagent is refused", bool(verdict(CLOSE, agent_type="general-purpose")))
    check("an empty agent type is refused", bool(verdict(CLOSE, agent_type="")))
    check("a tool name outside Bash|PowerShell is admitted",
          verdict(CLOSE, tool="Read") is None)
    both = verdict(CLOSE + " && python3 .claude/scripts/harness_tests.py --for .claude/x.py")
    check("every wide run of the command is refused", bool(both), str(both))


def case_sidecar():
    clear_env()
    os.environ["CLAUDE_CODE_SIDECAR"] = "test-transport"
    check("a sidecar with no kind is refused",
          bool(verdict(CLOSE, agent_id=None, agent_type=None)))
    os.environ["CLAUDE_CODE_SIDECAR_KIND"] = "gate"
    check("a sidecar whose kind is gate is admitted",
          verdict(CLOSE, agent_id=None, agent_type=None) is None)
    os.environ["CLAUDE_CODE_SIDECAR_KIND"] = "author"
    check("a sidecar whose kind is author is refused",
          bool(verdict(CLOSE, agent_id=None, agent_type=None)))
    clear_env()


def case_reach():
    clear_env()
    script = write("run_close.sh", "#!/bin/bash\nset -e\npython3 .claude/scripts/harness_tests.py --close\n")
    check("a wrapper script that runs the runner is refused", bool(verdict("bash " + script)))
    nested = write("outer.sh", "bash %s\n" % script)
    check("a script that runs a script is refused", bool(verdict("bash " + nested)))
    check("bash -c body refused", bool(verdict('bash -c "%s"' % CLOSE)))
    check("sh -c body refused", bool(verdict('sh -c "%s"' % CLOSE)))
    source = "import subprocess; subprocess.run(['python3','.claude/scripts/harness_tests.py','--close'])"
    import shlex
    check("python3 -c subprocess call refused", bool(verdict("python3 -c " + shlex.quote(source))))
    reader = "from pathlib import Path; print(Path('.claude/scripts/harness_tests.py').read_text())"
    check("python3 -c source read admitted", verdict("python3 -c " + shlex.quote(reader)) is None)
    py = write("launch.py", "import subprocess\nsubprocess.run(['python3','.claude/scripts/harness_tests.py','--close'])\n")
    check("a python script that calls the runner is refused", bool(verdict("python3 " + py)))
    check("PowerShell tool command refused", bool(verdict(CLOSE, tool="PowerShell")))
    check("a commented mention in a script is admitted",
          verdict("bash " + write("quiet.sh", "# python3 .claude/scripts/harness_tests.py --close\necho done\n")) is None)


def case_invalid_rows():
    clear_env()
    saved = guard.ROOT
    try:
        bad = planted_root("bad", {"slice": [], "merge": [], "close": [],
                                   "wide": [{"runner": "[", "exempt": [], "narrow": None}]})
        guard.ROOT = bad
        denied = verdict(CLOSE, cwd=bad)
        check("a malformed row refuses the run", bool(denied), str(denied))
        check("the deny carries the validation error", bool(denied) and "wide[0].runner" in denied, str(denied))
        check("a malformed row fails closed for any command", bool(verdict("git status", cwd=bad)))
        unsupported = planted_root("declared", {"slice": [], "merge": [], "close": [],
                                                "wide": [{"runner": "runtests\\.ps1", "exempt": [],
                                                          "narrow": {"declared": {
                                                              "arg": "Filter", "glob": "**/*.cs",
                                                              "decl": ["class\\s+(\\w+)"], "admit": []}}}]})
        guard.ROOT = unsupported
        check("a declared row whose filter carries no term is wide",
              bool(verdict("runtests.ps1 -Filter X", cwd=unsupported)))
    finally:
        guard.ROOT = saved


def case_declared_narrowing():
    clear_env()
    root = planted_root("godot", GODOT_TABLE)
    write("roots/godot/Tests/Logic/FooTests.cs",
          "public class FooTests {\n  [Test] public void Works() {}\n}\n")
    write("roots/godot/Tests/Logic/Lint/ScanTests.cs",
          "public class ScanTests {}\n")
    saved = guard.ROOT
    try:
        guard.ROOT = root
        run = "run_test_suite.ps1 -Filter "
        check("a declared class filter is narrow",
              verdict(run + "FullyQualifiedName~Tests.Logic.FooTests", cwd=root) is None)
        check("a bare declared test method is narrow",
              verdict(run + "FullyQualifiedName~Works", cwd=root) is None)
        check("a Class.Method filter is narrow",
              verdict(run + "FullyQualifiedName~Tests.Logic.FooTests.Works", cwd=root) is None)
        check("an admitted Lint group is narrow",
              verdict(run + "FullyQualifiedName~Tests.Logic.Lint", cwd=root) is None)
        check("a group prefix is wide",
              bool(verdict(run + "FullyQualifiedName~Tests.Logic", cwd=root)))
        check("a trailing dot is wide",
              bool(verdict(run + "FullyQualifiedName~Tests.Logic.", cwd=root)))
        check("a run-time-built filter is wide",
              bool(verdict(run + "FullyQualifiedName~Tests.$Suite.FooTests", cwd=root)))
        check("an undeclared bare name is wide",
              bool(verdict(run + "FullyQualifiedName~Nope", cwd=root)))
        check("a regression_gate -StaticOnly run is exempt",
              verdict("regression_gate.ps1 -StaticOnly", cwd=root) is None)
        check("a unitrun test with no filter is wide",
              bool(verdict("unitrun test", cwd=root)))
        check("a unitrun test with a declared class filter is narrow",
              verdict("unitrun test --filter FullyQualifiedName~Tests.Logic.FooTests", cwd=root) is None)
    finally:
        guard.ROOT = saved


def case_must_allow():
    from _must_allow_cases import must_allow_cases
    cases, cleanup = must_allow_cases("wide_test_guard")
    clear_env()
    try:
        for label, data, _cwd, expected in cases:
            data = dict(data, agent_id="mallow", agent_type="delegate-author")
            check(label, (guard.hook(data) is None) == (expected == "allow"), str(guard.hook(data)))
    finally:
        cleanup()


def through(command, env_extra, agent_type="delegate-author"):
    data = payload(command, cwd=TMP, agent_type=agent_type)
    data["session_id"] = "wide-guard-proof"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_SIDECAR")}
    env.pop("HARNESS_WIDE_RUN_ADMITTED", None)
    env.update(env_extra)
    env["CLAUDE_PROJECT_DIR"] = TMP
    return subprocess.run([sys.executable, os.path.join(HOOKS, "pre_bash_dispatch.py")],
                          input=json.dumps(data), capture_output=True, text=True, timeout=240, env=env)


def case_dispatcher_wiring():
    clear_env()
    r = through(CLOSE, {})
    text = r.stdout + r.stderr
    check("the dispatcher's exit is a decision or a deny", r.returncode in (0, 2), str(r.returncode))
    check("the dispatcher carries no traceback", "Traceback" not in text, text[-500:])
    check("the dispatcher denies a delegate-author job's close run", "Wide test run refused" in text, text[-800:])
    r = through(CLOSE, {}, agent_type="delegate-gate")
    text = r.stdout + r.stderr
    check("the dispatcher admits a delegate-gate job", "Wide test run refused" not in text, text[-800:])
    r = through("git status --short", {})
    text = r.stdout + r.stderr
    check("the dispatcher admits an ordinary command", "Wide test run refused" not in text, text[-800:])


if __name__ == "__main__":
    saved_root = guard.ROOT
    guard.ROOT = planted_root("harness", ROW_TABLE)
    try:
        for fn in (case_classifier, case_delegate_verdicts,
                   case_sidecar, case_reach, case_gate_type_owner, case_unreadable_table,
                   case_invalid_rows, case_declared_narrowing, case_must_allow,
                   case_dispatcher_wiring):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failure, never an allow
                failures.append("CRASH %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    finally:
        clear_env()
        guard.ROOT = saved_root
        shutil.rmtree(TMP, ignore_errors=True)
    for line in failures:
        print("FAIL", line)
    print("FAILED %d" % len(failures) if failures else "OK")
    sys.exit(1 if failures else 0)
