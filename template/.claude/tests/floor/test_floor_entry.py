"""The floor entry (T1 Wiring): `hooks/pre_bash.py` spawned from its registered command, and the fail-closed paths.

Spawned rows run the stub the way the client does (payload on stdin, exit 0, deny as JSON). They run against a
copy of `hooks/` and `tools/` so a row can break one module's import, or `_floor.py` itself, without touching
the checkout. The child gets no PYTHONPATH, so nothing outside the copy loads.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import _floor
from case import bash, decide
from conftest import CLAUDE, git, write

FLOOR_MODULES = {
    # module -> (a call its matcher selects, a call it does not select or None when it selects every shell call)
    "dangerous_shell_guard": ("ls", None),
    "kill_guard": ("taskkill /PID 4242 /F", "ls"),
}


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    root = tmp_path_factory.mktemp("entry") / ".claude"
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(CLAUDE / "hooks", root / "hooks", ignore=skip)
    shutil.copytree(CLAUDE / "tools", root / "tools", ignore=skip)
    return root


def spawn(tree, payload, raw=None):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "PYTHONSTARTUP")}
    env["CLAUDE_PROJECT_DIR"] = str(tree.parent)
    text = raw if raw is not None else json.dumps(payload)
    done = subprocess.run([sys.executable, str(tree / "hooks" / "pre_bash.py")], input=text, capture_output=True,
                          text=True, env=env, cwd=str(tree.parent), timeout=60)
    assert done.returncode == 0, "CRASH exit %s: %s" % (done.returncode, done.stderr)
    out = done.stdout.strip()
    if not out:
        return "allow", ""
    hso = json.loads(out).get("hookSpecificOutput") or {}
    return hso.get("permissionDecision") or "allow", hso.get("permissionDecisionReason") or ""


def shell(command, cwd, tool="Bash"):
    return {"tool_name": tool, "tool_input": {"command": command}, "cwd": str(cwd), "hook_event_name": "PreToolUse"}


@pytest.fixture
def broken(tree):
    """Replace one file of the copy for the length of a test, then put it back."""
    saved = {}

    def plant(rel, text):
        path = tree / rel
        saved.setdefault(path, path.read_bytes() if path.exists() else None)
        path.write_text(text, encoding="utf-8", newline="\n")
        shutil.rmtree(path.parent / "__pycache__", ignore_errors=True)
    yield plant
    for path, data in saved.items():
        if data is None:
            path.unlink()
        else:
            path.write_bytes(data)
        shutil.rmtree(path.parent / "__pycache__", ignore_errors=True)


# --- spawned: the registered command ------------------------------------------------------------------------------
def test_spawned_stub_allows_a_harmless_call(tree, tmp_path):
    assert spawn(tree, shell("ls", tmp_path)) == ("allow", "")


def test_spawned_stub_denies_a_hard_reset_over_unsaved_edits(tree, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, "a.txt", "one\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    write(repo, "a.txt", "two\n")
    kind, reason = spawn(tree, shell("git reset --hard", repo))
    assert kind == "deny" and "reset" in reason


def test_spawned_taskstop_on_a_launch_this_session_did_not_make_denies(tree, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8")
    kind, reason = spawn(tree, {"tool_name": "TaskStop", "tool_input": {"task_id": "bxyz123"},
                                "transcript_path": str(transcript), "hook_event_name": "PreToolUse"})
    assert kind == "deny" and "bxyz123" in reason


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", ""])
def test_spawned_unreadable_payload_denies(tree, raw):
    kind, reason = spawn(tree, None, raw=raw)
    assert kind == "deny" and "Re-issue the same call" in reason


# --- spawned: fail closed -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("module", sorted(FLOOR_MODULES))
def test_a_floor_module_that_fails_to_import_denies_what_its_matcher_selects(tree, broken, tmp_path, module):
    selected, unselected = FLOOR_MODULES[module]
    broken("hooks/%s.py" % module, "raise ImportError('planted break')\n")
    kind, reason = spawn(tree, shell(selected, tmp_path))
    assert kind == "deny" and module in reason
    if unselected is not None:
        assert spawn(tree, shell(unselected, tmp_path))[0] == "allow"


def test_a_broken_shared_lexer_denies_through_every_module_that_imports_it(tree, broken, tmp_path):
    broken("hooks/_shell_lex.py", "raise ImportError('planted break')\n")
    kind, reason = spawn(tree, shell("taskkill /PID 4242 /F", tmp_path))
    assert kind == "deny" and "_shell_lex" in reason


def test_an_unparseable_floor_dispatcher_denies_through_the_stub(tree, broken, tmp_path):
    broken("hooks/_floor.py", "def decide(:\n")
    kind, reason = spawn(tree, shell("ls", tmp_path))
    assert kind == "deny" and "_floor" in reason


def test_a_floor_dispatcher_whose_decide_raises_denies_through_the_stub(tree, broken, tmp_path):
    broken("hooks/_floor.py", "def decide_text(raw):\n    raise RuntimeError('planted')\n")
    assert spawn(tree, shell("ls", tmp_path))[0] == "deny"


# --- in process: the dispatcher's own rules -----------------------------------------------------------------------
def test_the_matcher_registers_every_tool_the_floor_judges():
    assert set(_floor.MATCHER.split("|")) == {"Bash", "PowerShell", "Monitor", "TaskStop"}


def test_a_floor_check_that_raises_denies_its_call(monkeypatch, tmp_path):
    import dangerous_shell_guard

    def boom(_payload):
        raise RuntimeError("planted fault")
    monkeypatch.setattr(dangerous_shell_guard, "check", boom)
    got = bash("ls", tmp_path)
    assert got.denied and "dangerous_shell_guard" in got.reason


def test_a_failing_helper_is_skipped(monkeypatch, tmp_path):
    class Helper:
        __name__ = "planted_helper"

        @staticmethod
        def check(_payload):
            raise RuntimeError("planted helper fault")
    monkeypatch.setattr(_floor, "HELPERS", (Helper,))
    assert bash("ls", tmp_path).kind == "allow"


def test_a_floor_deny_wins_over_a_helper_rewrite(monkeypatch, plant):
    class Rewrites:
        @staticmethod
        def check(payload):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "updatedInput": dict(payload["tool_input"], command="true")}}
    monkeypatch.setattr(_floor, "HELPERS", (Rewrites,))
    assert bash("ls", plant).kind == "rewrite"
    write(plant, "README.md", "unsaved edit\n")
    assert bash("git reset --hard", plant).denied


@pytest.mark.parametrize("payload", [None, [1], "x", {"tool_name": "Bash", "tool_input": "notadict"}])
def test_a_payload_the_floor_cannot_read_denies(payload):
    got = decide(payload)
    assert got.denied and "Re-issue the same call" in got.reason


def test_monitor_and_other_tools_pass_the_destructive_checks_as_in_v1(plant):
    assert bash("git reset --hard", plant, tool="Monitor").kind == "allow"
    assert decide({"tool_name": "Read", "tool_input": {"file_path": "x"}}).kind == "allow"
