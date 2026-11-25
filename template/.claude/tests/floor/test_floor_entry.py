"""The floor entry: `hooks/pre_bash.py` spawned from its registered command, and the fail-closed paths of the
dispatcher behind it (`hooks/pre_bash_dispatch.py`).

Spawned rows run the entry the way the client does (payload on stdin, exit 0, deny as JSON). They run against a
copy of `hooks/` and `tools/` so a row can break one module's import, or the dispatcher itself, without touching
the checkout. The child gets no PYTHONPATH, so nothing outside the copy loads.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import pre_bash_dispatch
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
def test_spawned_entry_allows_a_harmless_call(tree, tmp_path):
    assert spawn(tree, shell("ls", tmp_path)) == ("allow", "")


def test_spawned_entry_denies_a_hard_reset_over_unsaved_edits(tree, tmp_path):
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


def test_a_broken_kill_guard_denies_a_taskstop(tree, broken, tmp_path):
    broken("hooks/kill_guard.py", "raise ImportError('planted break')\n")
    kind, reason = spawn(tree, {"tool_name": "TaskStop", "tool_input": {"task_id": "bxyz123"},
                                "transcript_path": None, "hook_event_name": "PreToolUse"})
    assert kind == "deny" and "kill_guard" in reason


def test_a_broken_shared_lexer_denies_through_every_module_that_imports_it(tree, broken, tmp_path):
    broken("hooks/_shell_lex.py", "raise ImportError('planted break')\n")
    kind, reason = spawn(tree, shell("taskkill /PID 4242 /F", tmp_path))
    assert kind == "deny" and "_shell_lex" in reason


def test_an_unparseable_dispatcher_denies_through_the_entry(tree, broken, tmp_path):
    broken("hooks/pre_bash_dispatch.py", "def decide(:\n")
    kind, reason = spawn(tree, shell("ls", tmp_path))
    assert kind == "deny" and "pre_bash_dispatch" in reason


def test_a_dispatcher_whose_decide_raises_denies_through_the_entry(tree, broken, tmp_path):
    broken("hooks/pre_bash_dispatch.py", "def decide_text(raw):\n    raise RuntimeError('planted')\n")
    assert spawn(tree, shell("ls", tmp_path))[0] == "deny"


def test_a_dispatcher_bootstrap_import_fault_denies_through_the_entry(tree, broken, tmp_path):
    broken("hooks/_git_commit.py", "raise ImportError('planted break')\n")
    kind, reason = spawn(tree, shell("ls", tmp_path))
    assert kind == "deny" and "planted break" in reason


def test_every_floor_module_has_its_broken_import_case():
    assert set(FLOOR_MODULES) <= set(pre_bash_dispatch._FLOOR_MEMBERS)
    assert set(FLOOR_MODULES) == {"dangerous_shell_guard", "kill_guard"}


# --- the registration ---------------------------------------------------------------------------------------------
def _pre_tool_use():
    settings = json.loads((CLAUDE / "settings.base.json").read_text(encoding="utf-8"))
    return settings["hooks"]["PreToolUse"]


def test_the_entry_is_registered_for_every_tool_the_floor_judges():
    rows = [entry for entry in _pre_tool_use()
            if any("hooks/pre_bash.py" in hook.get("command", "") for hook in entry.get("hooks", []))]
    assert len(rows) == 1
    assert set(rows[0]["matcher"].split("|")) == {"Bash", "PowerShell", "Monitor", "TaskStop"}


def test_no_floor_member_runs_twice():
    """kill_guard and the dispatcher run only through the entry, so no call is judged twice."""
    commands = [hook.get("command", "") for entry in _pre_tool_use() for hook in entry.get("hooks", [])]
    for name in ("kill_guard.py", "dangerous_shell_guard.py", "pre_bash_dispatch.py"):
        assert not any(name in command for command in commands), name


# --- in process: the dispatcher's own rules -----------------------------------------------------------------------
def test_a_floor_check_that_raises_denies_its_call(monkeypatch, tmp_path):
    import dangerous_shell_guard

    def boom(_payload):
        raise RuntimeError("planted fault")
    monkeypatch.setattr(dangerous_shell_guard, "check", boom)
    got = bash("ls", tmp_path)
    assert got.denied and "dangerous_shell_guard" in got.reason


def test_a_kill_check_that_raises_denies_a_taskstop(monkeypatch, tmp_path):
    import kill_guard

    def boom(_payload):
        raise RuntimeError("planted fault")
    monkeypatch.setattr(kill_guard, "check", boom)
    got = decide({"tool_name": "TaskStop", "tool_input": {"task_id": "b1"}, "hook_event_name": "PreToolUse"})
    assert got.denied and "kill_guard" in got.reason


# A loaded floor member that raises denies whatever the call: its matcher only scopes a failed import, so a spelling
# the matcher misses (`k\ill`, `k"i"ll`) cannot turn the fault into an allow (N-F6).
LOADED_FLOOR = sorted(name for name in pre_bash_dispatch._FLOOR_MEMBERS
                      if not isinstance(getattr(pre_bash_dispatch, name, None), (type(None), pre_bash_dispatch._ImportStub)))


@pytest.mark.parametrize("command", ["ls", "k\\ill -9 21764", 'k"i"ll -9 21764'])
@pytest.mark.parametrize("name", LOADED_FLOOR)
def test_a_loaded_floor_member_that_raises_denies_any_call(monkeypatch, tmp_path, name, command):
    def boom(*_args, **_kwargs):
        raise RuntimeError("planted fault")
    member = getattr(pre_bash_dispatch, name)
    monkeypatch.setattr(member, "main", boom)
    monkeypatch.setattr(pre_bash_dispatch, "HOOKS", ((member, ()),))
    got = bash(command, tmp_path)
    assert got.denied and name in got.reason and "planted fault" in got.reason, got


class _Member:
    """A stub chain member with the v1 `main()` contract: prints the planted hookSpecificOutput (None prints nothing)
    or raises the planted fault."""

    def __init__(self, name="planted_member", fault=None, **hso):
        self.__name__, self.fault, self.hso = name, fault, hso

    def main(self):
        if self.fault:
            raise self.fault
        if self.hso:
            print(json.dumps({"hookSpecificOutput": dict(self.hso, hookEventName="PreToolUse")}))
        return 0


def test_a_failing_non_floor_member_is_reported_and_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(pre_bash_dispatch, "HOOKS", ((_Member(fault=RuntimeError("planted")), ()),))
    got = bash("ls", tmp_path)
    assert got.kind == "allow" and "planted_member" in got.reason


def _decision_output(command, cwd):
    out = pre_bash_dispatch.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd),
                                    "hook_event_name": "PreToolUse"})
    return (out or {}).get("hookSpecificOutput") or {}


def test_a_json_deny_carries_no_rewrite_or_context_and_gives_back_once_keys(monkeypatch, tmp_path):
    """A deny printed as JSON is a deny as an exit-2 block is: the denied call runs nothing, so no earlier member's
    rewrite or context rides on it, and the once-keys those members spent go back (N-F7)."""
    given = []
    monkeypatch.setattr(pre_bash_dispatch, "give_back", lambda start, stop: given.append((start, stop)))
    rewrite = _Member("planted_rewriter", updatedInput={"command": "echo rewritten"}, additionalContext="planted line")
    deny = _Member("planted_denier", permissionDecision="deny", permissionDecisionReason="planted deny")
    monkeypatch.setattr(pre_bash_dispatch, "HOOKS", ((rewrite, ()), (deny, ())))
    hso = _decision_output("ls", tmp_path)
    assert hso.get("permissionDecision") == "deny" and "planted deny" in hso.get("permissionDecisionReason", ""), hso
    assert "updatedInput" not in hso and "additionalContext" not in hso and given, (hso, given)


def test_the_commit_fold_rewrite_does_not_ride_on_a_later_floor_deny(plant):
    hso = _decision_output("git add -- README.md && git commit -q -m x && rm -rf /c/Users/planted_important", plant)
    assert hso.get("permissionDecision") == "deny" and "updatedInput" not in hso, hso


class _Mover(_Member):
    """A member that changes the process directory, as the `.tres` guards do to read the repository a commit targets."""

    def __init__(self, where):
        super().__init__("planted_mover")
        self.where = where

    def main(self):
        os.chdir(self.where)
        return 0


def test_a_member_that_changes_directory_does_not_move_the_next_member(monkeypatch, tmp_path):
    seen = []
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    probe = _Member("planted_probe")
    probe.main = lambda: seen.append(os.getcwd()) or 0
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pre_bash_dispatch, "HOOKS", ((_Mover(elsewhere), ()), (probe, ())))
    bash("ls", tmp_path)
    assert seen == [str(tmp_path)] and os.getcwd() == str(tmp_path), (seen, os.getcwd())


def test_a_floor_deny_wins_over_a_later_rewrite(monkeypatch, plant):
    rewrite = _Member(updatedInput={"command": "true"})
    monkeypatch.setattr(pre_bash_dispatch, "HOOKS",
                        ((pre_bash_dispatch.dangerous_shell_guard, ()), (rewrite, ())))
    assert bash("ls", plant).kind == "rewrite"
    write(plant, "README.md", "unsaved edit\n")
    assert bash("git reset --hard", plant).denied


def test_a_taskstop_runs_the_stop_check_alone(monkeypatch):
    deny_all = _Member(permissionDecision="deny", permissionDecisionReason="planted")
    monkeypatch.setattr(pre_bash_dispatch, "HOOKS", ((deny_all, ()),))
    got = decide({"tool_name": "TaskStop", "tool_input": {"task_id": "b1"}, "hook_event_name": "PreToolUse"})
    assert got.kind == "allow"


def test_a_decision_whose_git_reads_run_out_of_time_denies(monkeypatch, plant):
    """The client treats a hook it kills on timeout as a non-blocking error, so the floor ends its own git reads
    first and denies the call they left unjudged."""
    command = "git commit -q --allow-empty -m x"
    assert not bash(command, plant).denied
    monkeypatch.setattr(pre_bash_dispatch, "BUDGET_S", 0.0)
    got = bash(command, plant)
    assert got.denied and "budget" in got.reason
    assert bash("ls", plant).kind == "allow"  # no git read, nothing cut short


def test_a_member_outside_the_floor_keeps_its_own_budget(monkeypatch, plant):
    """b2: only the floor members' git reads are bounded; a v1 member after them (an approver behind a long sidecar
    preflight) reads git on its own budget, so a spent floor budget denies nothing it did not judge."""
    import _git_run

    class Reader(_Member):
        def main(self):
            assert _git_run.run(["rev-parse", "HEAD"], cwd=str(plant)) is not None
            return 0

    monkeypatch.setattr(pre_bash_dispatch, "HOOKS", ((Reader("planted_reader"), ()),))
    monkeypatch.setattr(pre_bash_dispatch, "BUDGET_S", 0.0)
    got = bash("git status --short", plant)
    assert got.kind == "allow" and "budget" not in (got.reason or ""), got.reason


@pytest.mark.parametrize("payload", [None, [1], "x", {"tool_name": "Bash", "tool_input": "notadict"}])
def test_a_payload_the_floor_cannot_read_denies(payload):
    got = decide(payload)
    assert got.denied and "Re-issue the same call" in got.reason


def test_monitor_and_other_tools_pass_the_destructive_checks_as_in_v1(plant):
    assert bash("git reset --hard", plant, tool="Monitor").kind == "allow"
    assert decide({"tool_name": "Read", "tool_input": {"file_path": "x"}}).kind == "allow"


# --- The chain's other members through the one entry --------------------------------------------------------------
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


def test_the_stop_check_runs_beside_the_destructive_check_and_the_rewriter_runs_last():
    order = [module.__name__ for module, _argv in pre_bash_dispatch.HOOKS]
    assert order.index("kill_guard") == order.index("dangerous_shell_guard") + 1
    assert order[-1] == "bash_backslash_fidelity"


def test_a_cd_compound_of_reads_is_allowed_through_the_floor(tmp_path):
    out = pre_bash_dispatch.decide({"tool_name": "Bash", "cwd": str(tmp_path),
                                    "tool_input": {"command": "cd %s && git status --short" % tmp_path.as_posix()}})
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
