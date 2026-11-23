"""Rows for v1 floor branches no v1 test reached (plan R1: the executor reads each floor module's deny branches
against the ported rows). Found by recording the lines the table rows execute in `dangerous_shell_guard.py` and
`kill_guard.py`; each row asserts the answer v1's code gives, so none adds a deny class.
"""
import json

import pytest

import kill_guard as kg
from case import bash
from conftest import git, write

SID = "b48600e4-2ced-4055-895c-18867ef93b99"
OTHER = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("command", ["git push --force-with-lease=main origin main", "git push -fq origin main",
                                     "git push --force-if-includes=main origin main"])
def test_force_push_spellings_deny(plant, command):
    assert bash(command, plant).denied


def test_branch_delete_naming_no_branch_denies(plant):
    assert bash("git branch -D", plant).denied


def test_an_absent_forced_delete_of_another_sessions_folder_is_rewritten_to_a_no_op(plant, tmp_path):
    """The delete check denies a target naming another session's id folder; the target is absent inside
    scratch, so the floor rewrites that segment to `true` (it removes nothing) instead of denying."""
    transcript = tmp_path / (SID + ".jsonl")
    transcript.write_text("", encoding="utf-8")
    got = bash("rm -rf .claude/scratch/session_end/%s/p1" % OTHER, plant, transcript_path=transcript)
    assert got.kind == "rewrite" and got.updated["command"] == "true", got


def test_the_rewrite_is_never_used_for_a_present_target(plant, tmp_path):
    transcript = tmp_path / (SID + ".jsonl")
    transcript.write_text("", encoding="utf-8")
    write(plant, ".claude/scratch/session_end/%s/p1/out.txt" % OTHER)
    assert bash("rm -rf .claude/scratch/session_end/%s/p1" % OTHER, plant, transcript_path=transcript).denied


@pytest.fixture
def table(tmp_path, monkeypatch):
    path = tmp_path / "table.json"
    path.write_text(json.dumps({"4242": {"pid": 4242, "name": "notepad.exe", "cmd": "notepad.exe"}}),
                    encoding="utf-8")
    monkeypatch.setenv("KILL_GUARD_PROCESS_TABLE", str(path))
    monkeypatch.delenv("HARNESS_ALLOW_KILL", raising=False)
    monkeypatch.setattr(kg, "commandline", lambda pid, msys_first=False, deadline=None: None)


@pytest.mark.parametrize("command", [
    "killall -r '('",
    "powershell -Command \"Stop-Process -InputObject $p\"",
    "wmic process delete",
    "ls | xargs -n1 kill",  # the bare `ls | xargs kill` passes in v1 (R1 finding K-XA in test_floor_ported.md)
    "powershell -Command \"Get-CimInstance Win32_Process | Invoke-CimMethod -MethodName Terminate\"",
])
def test_unresolvable_stop_selectors_deny(table, tmp_path, command):
    assert bash(command, tmp_path).denied


def test_a_resolvable_name_kill_of_an_unprotected_process_allows(table, tmp_path):
    assert bash("taskkill /IM notepad.exe /F", tmp_path).kind == "allow"
