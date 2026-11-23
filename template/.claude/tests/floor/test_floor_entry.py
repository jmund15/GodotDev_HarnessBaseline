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


def test_the_floor_imports_only_its_own_modules(tree):
    """A floor module's import chain is the floor's: a per-session state module or a v1 tool a later slice reshapes
    must not be able to break the import, which would deny every call (review finding 5)."""
    probe = "import sys; sys.path.insert(0, %r); import _floor; print(' '.join(sorted(sys.modules)))" % str(
        tree / "hooks")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV", "PYTHONSTARTUP")}
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    loaded = set(done.stdout.split())
    assert not loaded & {"_hook_state", "cli_usage", "index_commit", "reap"}, loaded


def test_every_floor_member_has_its_broken_import_case():
    assert set(FLOOR_MODULES) == {name for name, _matches, _action in _floor.FLOOR_MEMBERS}


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


def test_a_decision_whose_git_reads_run_out_of_time_denies(monkeypatch, plant):
    """Review finding 5 of S5: the client treats a hook it kills on timeout as a non-blocking error, so the floor
    ends its own git reads first and denies the call they left unjudged."""
    command = "git commit -q --allow-empty -m x"
    assert bash(command, plant).kind == "allow"
    monkeypatch.setattr(_floor, "BUDGET_S", 0.0)
    got = bash(command, plant)
    assert got.denied and "budget" in got.reason
    assert bash("ls", plant).kind == "allow"  # no git read, nothing cut short


@pytest.mark.parametrize("payload", [None, [1], "x", {"tool_name": "Bash", "tool_input": "notadict"}])
def test_a_payload_the_floor_cannot_read_denies(payload):
    got = decide(payload)
    assert got.denied and "Re-issue the same call" in got.reason


def test_monitor_and_other_tools_pass_the_destructive_checks_as_in_v1(plant):
    assert bash("git reset --hard", plant, tool="Monitor").kind == "allow"
    assert decide({"tool_name": "Read", "tool_input": {"file_path": "x"}}).kind == "allow"


# --- The kept Bash helpers (B10, §4 Q12) through the one entry --------------------------------------------------
@pytest.mark.parametrize("tool,command,kind", [
    ("Bash", "grep -rn needle .", "deny"),
    ("Bash", "find . -name '*.py' | xargs grep needle", "deny"),
    ("PowerShell", "Get-ChildItem -Recurse | Select-String needle", "deny"),
    ("Monitor", "grep -rn needle .claude/", "deny"),
    ("Bash", "git grep -n needle -- .claude", "allow"),
    ("Bash", "echo 'grep -rn needle .'", "allow"),
    ("Bash", "echo " + "x" * 8200, "deny"),
    ("Bash", "echo " + "x" * 8000, "allow"),
], ids=["grep-r", "find-xargs-grep", "pwsh-recurse", "monitor-grep-r", "git-grep", "quoted-mention", "past-cut",
        "under-cut"])
def test_the_helpers_judge_through_the_entry(tmp_path, tool, command, kind):
    assert bash(command, tmp_path, tool=tool).kind == kind


def test_the_backslash_repair_rewrites_through_the_entry(tmp_path, monkeypatch):
    record, transcript = tmp_path / "record.json", tmp_path / "t.jsonl"
    record.write_text(json.dumps({"9.9.9|%s" % sys.platform: {"collapses": True, "collapses_before_quote": False,
                                                               "observed": "2026-09-15"}}), encoding="utf-8")
    transcript.write_text(json.dumps({"type": "user", "version": "9.9.9", "message": {"content": "x"}}) + "\n",
                          encoding="utf-8")
    monkeypatch.setenv("HARNESS_BASH_FIDELITY_RECORD", str(record))
    pair = "\\" * 2
    got = bash("printf 'a" + pair + "b'", tmp_path, transcript_path=transcript)
    assert got.kind == "rewrite" and got.updated["command"] == "printf 'a" + pair * 2 + "b'"


# --- The decision merge: deny > ask > allow, every rewrite and context carried (F34) ----------------------------------
class _Helper:
    """A stub helper module: `check` returns the planted hookSpecificOutput (None allows)."""

    def __init__(self, **hso):
        self.hso = hso

    def check(self, payload):
        return {"hookSpecificOutput": dict(self.hso, hookEventName="PreToolUse")} if self.hso else None


def _merge(monkeypatch, *helpers, command="ls"):
    monkeypatch.setattr(_floor, "HELPERS", tuple(helpers))
    out = _floor.decide({"tool_name": "Bash", "tool_input": {"command": command, "timeout": 5}, "cwd": "."})
    return (out or {}).get("hookSpecificOutput") or {}


REWRITE = _Helper(updatedInput={"command": "ls -l", "timeout": 5}, additionalContext="rewritten")
ASK = _Helper(permissionDecision="ask", permissionDecisionReason="check with the owner")
ALLOW = _Helper(permissionDecision="allow", permissionDecisionReason="read-only")
DENY = _Helper(permissionDecision="deny", permissionDecisionReason="no")


def test_an_ask_after_a_rewrite_keeps_the_rewrite_and_its_context(monkeypatch):
    hso = _merge(monkeypatch, REWRITE, ASK)
    assert hso["permissionDecision"] == "ask" and hso["updatedInput"]["command"] == "ls -l"
    assert "rewritten" in hso["additionalContext"]


def test_an_ask_before_a_rewrite_keeps_the_rewrite_and_its_context(monkeypatch):
    hso = _merge(monkeypatch, ASK, REWRITE)
    assert hso["permissionDecision"] == "ask" and hso["updatedInput"]["command"] == "ls -l"
    assert "rewritten" in hso["additionalContext"]


def test_an_allow_counts_only_when_nothing_asked_or_denied(monkeypatch):
    assert _merge(monkeypatch, ALLOW)["permissionDecision"] == "allow"
    assert _merge(monkeypatch, ALLOW, ASK)["permissionDecision"] == "ask"
    assert _merge(monkeypatch, ASK, ALLOW)["permissionDecision"] == "ask"
    assert _merge(monkeypatch, ALLOW, DENY)["permissionDecision"] == "deny"


def test_an_allow_carries_the_rewrite_that_ran_with_it(monkeypatch):
    hso = _merge(monkeypatch, REWRITE, ALLOW)
    assert hso["permissionDecision"] == "allow" and hso["updatedInput"]["command"] == "ls -l"


def test_a_deny_wins_over_an_earlier_ask_and_rewrite(monkeypatch):
    assert _merge(monkeypatch, REWRITE, ASK, DENY)["permissionDecision"] == "deny"


# --- The wired helpers: the cd approver and the wait guard (F14, F15) -------------------------------------------------
def test_the_helpers_are_registered_in_run_order():
    assert _floor.HELPER_NAMES[-4:] == ("run_call_router", "foreground_wait_guard", "bash_backslash_fidelity",
                                         "compound_cd_approver")
    assert [h.__name__ for h in _floor.HELPERS] == list(_floor.HELPER_NAMES)


def test_a_cd_compound_of_reads_is_allowed_through_the_floor(tmp_path):
    out = _floor.decide({"tool_name": "Bash", "tool_input": {"command": "cd %s && git status --short" % tmp_path.as_posix()},
                         "cwd": str(tmp_path)})
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_a_deny_in_the_same_call_beats_the_cd_approvers_allow(tmp_path):
    command = "cd %s && git status && echo %s" % (tmp_path.as_posix(), "x" * 8200)
    assert bash(command, tmp_path).denied


def test_a_cd_compound_is_allowed_through_the_spawned_entry(tree, tmp_path):
    verdict, reason = spawn(tree, shell("cd %s && git log --oneline -3" % tmp_path.as_posix(), tmp_path))
    assert verdict == "allow" and "Auto-approved" in reason


def test_a_native_delegates_long_foreground_wait_is_clamped_through_the_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    payload = {"tool_name": "Bash", "tool_input": {"command": "make build", "timeout": 600000}, "cwd": str(tmp_path),
               "session_id": "floorh01", "agent_id": "agent-wf"}
    updated = decide(payload).updated
    assert updated["timeout"] == 270000 and updated["command"].endswith('echo "EXIT $?"')


def test_an_interactive_sessions_long_wait_passes_the_floor_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    payload = {"tool_name": "Bash", "tool_input": {"command": "make build", "timeout": 600000}, "cwd": str(tmp_path),
               "session_id": "floorh02"}
    assert decide(payload).kind == "allow"
