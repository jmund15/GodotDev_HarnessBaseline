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

if os.name == "nt":
    # A runner with no console stdin hands children an invalid handle; nothing here reads stdin it was not given.
    _popen_init = subprocess.Popen.__init__

    def _popen_with_stdin(self, *args, **kwargs):
        if len(args) < 5 and kwargs.get("stdin") is None:
            kwargs["stdin"] = subprocess.DEVNULL
        _popen_init(self, *args, **kwargs)
    subprocess.Popen.__init__ = _popen_with_stdin

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
