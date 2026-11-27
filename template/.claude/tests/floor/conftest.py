"""Shared fixtures for the loss-floor table tests (T1).

Every row calls the floor the way the client does, in process: `_floor.decide(payload)` with a PreToolUse
payload, through `case.decide`. One spawned case per entry point lives in `test_floor_entry.py`.
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

CLAUDE = Path(__file__).resolve().parents[2]
HOOKS = CLAUDE / "hooks"
TOOLS = CLAUDE / "tools"
for _p in (str(HOOKS), str(TOOLS), str(CLAUDE / "tests"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Per test file: seconds and process spawns by image, printed after every run (K2: the suite reads its own timings, so
# a slow file is diagnosed by cause as it lands, not found at the end).
_COST = {}
_FILE = [None]
_popen_init = subprocess.Popen.__init__


def _popen_counted(self, *args, **kwargs):
    if os.name == "nt" and len(args) < 5 and kwargs.get("stdin") is None:
        # A runner with no console stdin hands children an invalid handle; nothing here reads stdin it was not given.
        kwargs["stdin"] = subprocess.DEVNULL
    if _FILE[0] is not None:
        argv = args[0] if args else kwargs.get("args")
        first = (argv[0] if isinstance(argv, (list, tuple)) and argv else str(argv or "").split(" ")[0])
        image = os.path.splitext(os.path.basename(str(first)))[0].lower()
        cost = _COST.setdefault(_FILE[0], {"s": 0.0, "git": 0, "other": 0})
        cost["git" if image == "git" else "other"] += 1
    _popen_init(self, *args, **kwargs)


subprocess.Popen.__init__ = _popen_counted


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _FILE[0] = os.path.basename(str(item.fspath))
    yield
    _FILE[0] = None


def pytest_runtest_logreport(report):
    name = os.path.basename(report.location[0])
    _COST.setdefault(name, {"s": 0.0, "git": 0, "other": 0})["s"] += report.duration


def pytest_terminal_summary(terminalreporter):
    if not _COST:
        return
    terminalreporter.write_sep("-", "floor cost per file (seconds incl. setup, git spawns, other spawns)")
    for name, cost in sorted(_COST.items(), key=lambda kv: -kv[1]["s"]):
        terminalreporter.write_line("%7.1f s %6d git %5d other  %s" % (cost["s"], cost["git"], cost["other"], name))

GIT_ID = ["-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]


def git(cwd, *args, check=True):
    """Run git in `cwd` with a fixed identity; returns stdout text."""
    done = subprocess.run(["git", *GIT_ID, *args], cwd=str(cwd), capture_output=True, text=True)
    if check and done.returncode:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, done.stderr))
    return done.stdout


def write(root, rel, text="x\n"):
    path = Path(root, *rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


_SEEDS = {}


def seed_repo(dest, files=(("README.md", "seed\n"),), branch=None):
    """A repository at `dest` with one commit "seed" of `files`, its identity set and signing off. Built once per shape
    per process and copied (`.git` included) after that, so no row pays a `git init`, three configs and a commit
    (about 0.2 s each); every copy shares the seed commit and nothing else, and a row that needs a second history
    builds it on top. `branch` None is `main`."""
    key = (tuple(files), branch)
    if key not in _SEEDS:
        template = Path(tempfile.mkdtemp(prefix="floor_seed_"))
        atexit.register(shutil.rmtree, template, True)
        git(template, "init", "-q", *(["-b", branch] if branch else []))
        for name, value in (("user.email", "t@t.example"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(template, "config", name, value)
        for name, text in files:
            write(template, name, text)
        git(template, "add", "-A")
        git(template, "commit", "-qm", "seed")
        _SEEDS[key] = template
    shutil.copytree(_SEEDS[key], dest, symlinks=True)
    return Path(dest)


@pytest.fixture(scope="session")
def _planted_template(tmp_path_factory):
    project = tmp_path_factory.mktemp("plant_template") / "project"
    (project / ".claude" / "scratch").mkdir(parents=True)
    git(project, "init", "-q")
    write(project, "README.md", "seed\n")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "seed")
    return project


@pytest.fixture
def plant(tmp_path, monkeypatch, _planted_template):
    """A planted project root with one commit and an empty `.claude/scratch`, and a guard temp dir beside it
    (so the project is never "a plain temp path"). CLAUDE_PROJECT_DIR and the cwd point at the project. The repo
    is built once and copied per case, `.git` included, so no state crosses cases."""
    project = tmp_path / "project"
    guard_temp = tmp_path / "guard_temp"
    shutil.copytree(_planted_template, project)
    guard_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(guard_temp))
    for var in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(var, str(guard_temp))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.chdir(project)
    return project


# The game project's own `adaptation.json` `pre_bash` values: what the floor's kill rows and run router read as project data
# (T7), planted here so a row proves a project's value is denied or routed without the shared code naming it.
PROJECT_PRE_BASH = {
    "protected": [
        {"pattern": r"regression_gate|gate_chain|verify\.ps1", "why": "a gate/verification run"},
        {"pattern": r"vstest|dotnet\s+test", "why": "a test run"},
        {"pattern": r"claude-code-proxy", "why": "a transport proxy other sessions may be routing through"},
        {"pattern": r"(?:^|[;&|(\n]\s*)(?:(?:nohup|exec|setsid)\s+)?(?:(?:bash|sh|pwsh|powershell)\s+(?:-\S+\s+)*)?[\"']?"
                    r"(?:\S*[/\])?run_grid(?:\.snapshot[\w.-]*)?\.sh",
         "why": "a benchmark grid — its in-flight cell is a billed arm"},
    ],
    "router_entrypoints": [{"name": "run_test_suite"}, {"name": "verify"},
                           {"name": "regression_gate", "detach_flag": "-Detach"}, {"name": "run_integration_batched"},
                           {"name": "engine_run", "redirect": False}],
}


@pytest.fixture
def project_values(tmp_path, monkeypatch):
    """Plant `adaptation.json` `pre_bash` as the floor's reader finds it; returns a setter for another table."""
    import json
    import _delete_rule
    root = tmp_path / "adapted_project"

    def plant_values(pre_bash):
        target = root / ".claude" / "skills" / "project_subsystems" / "adaptation.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"pre_bash": pre_bash}), encoding="utf-8")
        monkeypatch.setattr(_delete_rule, "PROJECT_ROOT", root)

    plant_values(PROJECT_PRE_BASH)
    return plant_values


@pytest.fixture
def create_tools(tmp_path, monkeypatch):
    """Plant `adaptation.json` `worktree_create_tools` as the floor's reader finds it; returns the setter."""
    import json
    import _delete_rule
    root = tmp_path / "tool_project"

    def plant(*names):
        target = root / ".claude" / "skills" / "project_subsystems" / "adaptation.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"worktree_create_tools": list(names)}), encoding="utf-8")
        monkeypatch.setattr(_delete_rule, "PROJECT_ROOT", root)

    plant("example_lane_tool.py")
    return plant


# Live rows cut from a session transcript: a background-start result, the completion
# notification, and the creating calls with their results. A name, path or id is a `<token>` the case fills in.
LIVE_BACKGROUND_NOTICE = (
    "Command running in background with ID: <task>. Output is being written to: <output>. You will be notified when "
    "it completes \u2014 do not poll that file or sleep while waiting. Read the file only if the user asks for "
    "progress, or if you need a line it prints before you can go on, such as a server's ready line or port.\n"
    "Session cwd remains <cwd>; directory changes made by the backgrounded command do not apply to subsequent "
    "commands.")
LIVE_TASK_NOTIFICATION = (
    "<task-notification>\n<task-id><task></task-id>\n<tool-use-id><call></tool-use-id>\n"
    "<output-file><output></output-file>\n<status>completed</status>\n"
    "<summary>Background command \"Create two lane worktrees with an asset import\" completed (exit code 0)</summary>\n"
    "</task-notification>")
LIVE_LANE_CHAIN = ('cd "<root>" && python3 .claude/tools/example_lane_tool.py create <first> --from main --import && '
                   'python3 .claude/tools/example_lane_tool.py create <name> --from main --import')
LIVE_LANE_HEREDOC = ('cd "<root>" && python3 .claude/tools/example_lane_tool.py create <name> --from main 2>&1 | tail -1; '
                     "python3 - <<'EOF'\nimport json\nj={\"jobs\":[{\"label\":\"wlc-cleanup\"}]}\n"
                     "open(\".claude/scratch/jobs_cleanup.json\",\"w\",newline=\"\\n\").write(json.dumps(j,indent=1))\n"
                     "EOF\npython3 .claude/tools/dispatch_size.py .claude/scratch/jobs_cleanup.json 2>&1 "
                     "| grep -E 'preflight' | cut -c1-300")
LIVE_LANE_CREATED = "created <path> (branch claude/<name> from main); assets imported"


def fill(text, **tokens):
    """`text` with each `<token>` replaced by its value."""
    for key, value in tokens.items():
        text = text.replace("<%s>" % key, str(value))
    return text
