"""Shared fixtures for the loss-floor table tests (T1).

Every row calls the floor the way the client does, in process: `_floor.decide(payload)` with a PreToolUse
payload, through `case.decide`. One spawned case per entry point lives in `test_floor_entry.py`.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

CLAUDE = Path(__file__).resolve().parents[2]
HOOKS = CLAUDE / "hooks"
TOOLS = CLAUDE / "tools"
for _p in (str(HOOKS), str(TOOLS), str(Path(__file__).resolve().parent)):
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
    import shutil
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
