"""Proof for scripts/seat.sh and its two adapters. Every case spawns the launcher once, in a fixture checkout whose
`.claude` holds the shipped scripts, tools and seats.json, with stub `claude` and `agy` binaries first on PATH.
The project launch hooks are proven in test_launch_check.py; the one case here is what the launcher does with a
failing dispatch.post_return: the run fails and the answer file stays.

    python -m pytest .claude/tests/test_seat.py -q --durations=0
"""
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

CLAUDE = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")  # Git Bash: a bare `bash` from Python is WSL on Windows
FILES = ("scripts/seat.sh", "scripts/seat_claude.sh", "scripts/seat_agy.sh", "tools/launch_check.py",
         "tools/agy_seat.py", "tools/agy_workspace.py", "tools/adaptation.py", "scripts/lib/json_schema_lite.py",
         "hooks/_hook_state.py", "guards/rails.md", "seats.json")
STUB_CLAUDE = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$STUB_DIR/argv.txt"
cat > "$STUB_DIR/stdin.txt"
printf '%s' "${CLAUDE_CONFIG_DIR:-}" > "$STUB_DIR/config.txt"
printf '%s' "${CLAUDE_CODE_SIDECAR:-}" > "$STUB_DIR/sidecar.txt"
while [ "$#" -gt 0 ]; do
  if [ "$1" = --settings ]; then cp "$2" "$STUB_DIR/settings.txt"; fi
  shift
done
if [ -n "${STUB_FAIL:-}" ]; then echo "claude: forced failure" >&2; exit 5; fi
echo "STUB ANSWER"
"""
FLOOR_CAPACITY = "import sys\nprint('5-hour window at the exhaust floor')\nsys.exit(3)\n"
STUB_AGY_BAT = "@echo off\r\necho agy stub refused 1>&2\r\nexit /b 1\r\n"
BRIEF = "inputs:\n- a.md\n"
AGY_BRIEF = "inputs:\n- a.md\ntargets:\n- out.md\n"


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def _template(tmp_path_factory):
    """One committed checkout per module; each case copies it, so no case pays `git init` and `git commit`."""
    repo = tmp_path_factory.mktemp("seat_template") / "repo"
    (repo / ".claude").mkdir(parents=True)
    for rel in FILES:
        target = repo / ".claude" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CLAUDE / rel, target)
    (repo / ".claude" / "CLAUDE.core.md").write_text("core", encoding="utf-8")  # agy_workspace writes for a checkout of the project
    (repo / "a.md").write_text("input\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture(autouse=True)
def _checkout_of(_template, tmp_path, monkeypatch):
    shutil.copytree(_template, tmp_path / "repo")


def _checkout(tmp_path):
    return tmp_path / "repo"


def _stub(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _env(tmp_path, extra=None):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    (home / ".claude-secondary").mkdir(parents=True, exist_ok=True)  # the seat row names this login
    bin_dir.mkdir(exist_ok=True)
    _stub(bin_dir / "claude", STUB_CLAUDE)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), STUB_DIR=str(tmp_path),
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    env.pop("STUB_FAIL", None)
    env.update(extra or {})
    return env


def _brief(workdir, body=BRIEF):
    (workdir / "brief.md").write_text(body, encoding="utf-8")
    return "brief.md"


def _msys(path):
    """A Windows path as Git Bash reads it: C:/x becomes /c/x."""
    posix = Path(path).as_posix()
    return "/" + posix[0].lower() + posix[2:] if posix[1:3] == ":/" else posix


AGY_EFFORT = "high"  # seats.json lists efforts for the neon model, so its seat requires -e


def _seat(repo, workdir, env, seat, brief, out):
    effort_args = ["-e", AGY_EFFORT] if seat == "antigravity" else []
    return subprocess.run([BASH, _msys(repo / ".claude" / "scripts" / "seat.sh"), *effort_args, seat, brief, out],
                          cwd=str(workdir), env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


def _linked_worktree(repo, tmp_path):
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(worktree))
    return worktree


def test_unknown_seat_refuses_before_anything_else(tmp_path):
    repo = _checkout(tmp_path)
    done = _seat(repo, repo, _env(tmp_path), "nobody", _brief(repo), "out.md")
    assert done.returncode == 2 and "unknown seat 'nobody'" in done.stderr


def test_read_only_seat_naming_another_write_target_refuses_before_spawn(tmp_path):
    repo = _checkout(tmp_path)
    done = _seat(repo, repo, _env(tmp_path), "antigravity",
                 _brief(repo, "inputs:\n- a.md\ntargets:\n- elsewhere.md\n"), "out.md")
    assert done.returncode == 2 and "write target" in done.stderr
    assert not (tmp_path / "argv.txt").exists()


def test_writing_seat_outside_its_linked_worktree_refuses(tmp_path):
    repo = _checkout(tmp_path)
    done = _seat(repo, repo, _env(tmp_path), "claude-secondary", _brief(repo), "out.md")
    assert done.returncode == 2 and "linked worktree" in done.stderr
    assert not (tmp_path / "argv.txt").exists()


def test_full_seat_refuses_while_its_slot_pid_runs(tmp_path):
    repo = _checkout(tmp_path)
    env = _env(tmp_path)
    # The holder records its own Git Bash pid (the pid seat.sh checks with kill -0), then becomes the sleep.
    holder = subprocess.Popen([BASH, "-c", 'echo $$ > "$STUB_DIR/holder.pid"; exec sleep 120'], env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if (tmp_path / "holder.pid").exists() and (tmp_path / "holder.pid").read_text().strip():
                break
            time.sleep(0.05)
        slot = Path(env["HOME"]) / ".claude" / "seat_slots" / "antigravity" / "1"
        slot.mkdir(parents=True)
        (slot / "pid").write_text((tmp_path / "holder.pid").read_text(encoding="utf-8"), encoding="utf-8")
        done = _seat(repo, repo, env, "antigravity", _brief(repo), "out.md")
        assert done.returncode == 2 and "is full (1 running)" in done.stderr
    finally:
        holder.kill()
        holder.wait()


def test_capacity_at_floor_refuses_before_spawn(tmp_path):
    repo = _checkout(tmp_path)
    (repo / ".claude" / "tools" / "capacity.py").write_text(FLOOR_CAPACITY, encoding="utf-8")
    worktree = _linked_worktree(repo, tmp_path)
    done = _seat(repo, worktree, _env(tmp_path), "claude-secondary", _brief(worktree), "out.md")
    assert done.returncode == 2 and "exhaust floor" in done.stderr
    assert not (tmp_path / "argv.txt").exists()


def test_unreadable_capacity_warns_launches_and_writes_the_answer(tmp_path):
    repo = _checkout(tmp_path)
    worktree = _linked_worktree(repo, tmp_path)
    done = _seat(repo, worktree, _env(tmp_path), "claude-secondary", _brief(worktree), "out.md")
    assert "capacity unreadable for 'claude-secondary'" in done.stderr
    assert done.returncode == 0, done.stderr
    assert (worktree / "out.md").read_text(encoding="utf-8").strip() == "STUB ANSWER"


def test_writing_seat_runs_claude_with_its_login_settings_and_the_brief_on_stdin(tmp_path):
    repo = _checkout(tmp_path)
    worktree = _linked_worktree(repo, tmp_path)
    env = _env(tmp_path)
    done = _seat(repo, worktree, env, "claude-secondary", _brief(worktree), "out.md")
    assert done.returncode == 0, done.stderr
    argv = (tmp_path / "argv.txt").read_text(encoding="utf-8").split("\n")
    assert argv[0] == "-p" and "claude-sonnet-5-5" in argv and "acceptEdits" in argv
    assert "--disallowedTools" not in argv
    settings = (tmp_path / "settings.txt").read_text(encoding="utf-8")
    assert "pre_bash.py" in settings and "sidecar_workdir_guard.py" in settings
    assert (tmp_path / "sidecar.txt").read_text(encoding="utf-8") == "1"
    assert Path((tmp_path / "config.txt").read_text(encoding="utf-8")) == Path(env["HOME"]) / ".claude-secondary"
    rails = (CLAUDE / "guards" / "rails.md").read_text(encoding="utf-8")
    stdin = (tmp_path / "stdin.txt").read_text(encoding="utf-8")
    assert stdin.startswith(rails.rstrip()) and stdin.endswith(BRIEF) and stdin.count(rails.splitlines()[0]) == 1


def test_forced_cli_error_comes_back_verbatim_and_writes_no_answer(tmp_path):
    repo = _checkout(tmp_path)
    worktree = _linked_worktree(repo, tmp_path)
    done = _seat(repo, worktree, _env(tmp_path, {"STUB_FAIL": "1"}), "claude-secondary", _brief(worktree), "out.md")
    assert done.returncode == 5 and "claude: forced failure" in done.stderr
    assert not (worktree / "out.md").exists()


def test_read_only_seat_failing_agy_run_exits_non_zero_and_writes_no_answer(tmp_path):
    repo = _checkout(tmp_path)
    env = _env(tmp_path)
    agy = tmp_path / "bin" / "agy.bat"
    agy.write_bytes(STUB_AGY_BAT.encode("ascii"))
    env["AGY_BIN"] = str(agy)
    done = _seat(repo, repo, env, "antigravity", _brief(repo, "inputs:\n- a.md\ntargets:\n- out.md\n"), "out.md")
    assert done.returncode != 0
    assert not (repo / "out.md").exists()


def test_failing_post_return_fails_the_run_and_keeps_the_answer(tmp_path):
    repo = _checkout(tmp_path)
    hook = repo / ".claude" / "skills" / "project_subsystems" / "adaptation.json"
    hook.parent.mkdir(parents=True)
    hook.write_text(json.dumps({"dispatch": {"post_return": "echo answer-rejected >&2; exit 1"}}), encoding="utf-8")
    worktree = _linked_worktree(repo, tmp_path)
    done = _seat(repo, worktree, _env(tmp_path), "claude-secondary", _brief(worktree), "out.md")
    assert done.returncode == 2 and "answer-rejected" in done.stderr
    assert (worktree / "out.md").read_text(encoding="utf-8").strip() == "STUB ANSWER"


def test_agy_seat_writes_the_floor_bridge_before_launch(tmp_path):
    repo = _checkout(tmp_path)
    env = _env(tmp_path)
    agy = tmp_path / "bin" / "agy.bat"
    agy.write_bytes(STUB_AGY_BAT.encode("ascii"))
    env["AGY_BIN"] = str(agy)
    _seat(repo, repo, env, "antigravity", _brief(repo, AGY_BRIEF), "out.md")
    bridge = json.loads((repo / ".agents" / "hooks.json").read_text(encoding="utf-8"))
    assert "agy_hook_bridge.py" in json.dumps(bridge)


def test_a_writing_agy_row_is_refused_for_want_of_a_fence(tmp_path):
    repo = _checkout(tmp_path)
    seats = repo / ".claude" / "seats.json"
    data = json.loads(seats.read_text(encoding="utf-8"))
    data["seats"]["antigravity"]["writes"] = "yes"
    seats.write_text(json.dumps(data), encoding="utf-8")
    worktree = _linked_worktree(repo, tmp_path)
    done = _seat(repo, worktree, _env(tmp_path), "antigravity", _brief(worktree), "out.md")
    assert done.returncode != 0 and "fence" in done.stderr
    assert not (worktree / "out.md").exists()
