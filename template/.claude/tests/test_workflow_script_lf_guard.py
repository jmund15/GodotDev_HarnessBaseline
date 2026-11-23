"""Firing proof for hooks/workflow_script_lf_guard.py: a CRLF script under `.claude` is rewritten to LF before the
permission handler inlines it; an LF script, a non-Workflow tool, a path outside `.claude`, a game script inside
a worktree and a malformed payload are left alone. Every case plants its files under pytest's tmp_path.

One row per case: (id, script path under tmp_path, the path as the payload spells it (or None), bytes, tool, bytes expected after, text the hook must print).
"""
import os
from pathlib import Path

import pytest

from _hook_call import call

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "workflow_script_lf_guard.py"
CRLF = b"export const meta = {}\r\n"
LF = b"export const meta = {}\n"

ROWS = [
    ("crlf rewritten and reported", ".claude/workflows/a.js", None, b"export const meta = {}\r\nawait agent('x')\r\n",
     "Workflow", b"export const meta = {}\nawait agent('x')\n", "rewrote 2 CRLF"),
    ("uppercase worktree harness path", ".claude/worktrees/probe/.CLAUDE/Hooks/upper.js",
     ".claude/worktrees/probe/.CLAUDE/./Hooks/upper.js", CRLF, "Workflow", LF, "rewrote 1 CRLF"),
    ("dot-segment escape keeps a game script", ".claude/worktrees/probe/Game.js",
     ".claude/worktrees/probe/.claude/./hooks/../../Game.js", CRLF, "Workflow", CRLF, ""),
    ("lf script silent", ".claude/workflows/b.js", None, LF, "Workflow", LF, ""),
    ("non-Workflow tool ignored", ".claude/workflows/c.js", None, CRLF, "Agent", CRLF, ""),
    ("path outside .claude", "outside/d.js", None, CRLF, "Workflow", CRLF, ""),
]


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_rewrite_rows(tmp_path, row):
    _, rel, spelled, data, tool, after, said = row
    script = tmp_path / rel
    script.parent.mkdir(parents=True)
    script.write_bytes(data)
    given = (tmp_path / (spelled or rel)).as_posix()
    code, out, _err, _o = call(HOOK, {"tool_name": tool, "tool_input": {"scriptPath": given}})
    assert code == 0 and script.read_bytes() == after and (said in out if said else out == "")


@pytest.mark.skipif(os.name != "nt", reason="the /c/... spelling exists only for a path under a drive letter")
def test_msys_drive_path_is_translated(tmp_path):
    script = tmp_path / ".claude" / "workflows" / "m.js"
    script.parent.mkdir(parents=True)
    script.write_bytes(CRLF)
    posix = script.as_posix()
    msys = "/" + posix[0].lower() + posix[2:]
    call(HOOK, {"tool_name": "Workflow", "tool_input": {"scriptPath": msys}})
    assert script.read_bytes() == LF


def test_malformed_payload_exits_zero():
    assert call(HOOK, "not json")[0] == 0
