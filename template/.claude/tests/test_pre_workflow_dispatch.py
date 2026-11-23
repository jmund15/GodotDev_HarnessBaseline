#!/usr/bin/env python3
"""Proof for hooks/pre_workflow_dispatch.py, the one PreToolUse entry for Workflow|Agent (pytest).

Each case calls `dispatch(raw)` in process with a real PreToolUse payload. The launch hook and the transport's
claude dir are planted (tmp_path, monkeypatch), so no case reads the live seats file or a project's hook. A step
that raises denies the call: a fault never lets an unchecked dispatch through.
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import pre_workflow_dispatch as pwd  # noqa: E402
import workflow_transport as wt  # noqa: E402
from test_workflow_transport import plant  # noqa: E402


@pytest.fixture(autouse=True)
def not_a_delegate(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SIDECAR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SIDECAR_KIND", raising=False)


@pytest.fixture
def planted(tmp_path, monkeypatch):
    """A claude dir with a native block, and a launch hook that runs nothing."""
    claude = plant(tmp_path)
    real_inject = wt.inject
    monkeypatch.setattr(wt, "inject", lambda args, cwd=None, claude_dir=claude: real_inject(args, cwd, claude_dir))
    monkeypatch.setattr(pwd.launch_check, "run_hook", lambda key, args, cwd, lookup=None: (0, ""))
    return claude


def call(tmp_path, tool_input, tool="Workflow"):
    raw = json.dumps({"tool_name": tool, "cwd": str(tmp_path), "tool_input": tool_input})
    code, out, _err = pwd.dispatch(raw)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"] if out else {}


def brief(tmp_path, name="a.md"):
    (tmp_path / name).write_text("the brief", encoding="utf-8")
    return name


def test_a_non_dispatch_tool_is_silent(tmp_path):
    assert pwd.dispatch(json.dumps({"tool_name": "Read", "cwd": str(tmp_path), "tool_input": {}}))[1] == ""


def test_a_delegate_may_not_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SIDECAR", "1")
    decision = call(tmp_path, {"script": "dispatch.js", "args": {"jobs": []}})
    assert decision["permissionDecision"] == "deny"


def test_argsfile_outside_the_scratch_is_denied(tmp_path, planted):
    decision = call(tmp_path, {"script": "dispatch.js", "args": {"argsFile": "../outside.json"}})
    assert decision["permissionDecision"] == "deny" and "outside" in decision["permissionDecisionReason"]


def test_jobsfile_beside_inline_jobs_is_denied(tmp_path, planted):
    decision = call(tmp_path, {"script": "dispatch.js", "args": {"jobsFile": ".claude/scratch/j.json", "jobs": []}})
    assert decision["permissionDecision"] == "deny" and "sits beside" in decision["permissionDecisionReason"]


def test_a_job_whose_brief_is_missing_is_denied_naming_the_job(tmp_path, planted):
    job = {"label": "lost", "promptPath": "absent.md", "model": "opus", "effort": "low", "kind": "author"}
    decision = call(tmp_path, {"script": "dispatch.js", "args": {"jobs": [job]}})
    assert decision["permissionDecision"] == "deny" and "lost:" in decision["permissionDecisionReason"]


def test_a_clean_native_call_is_rewritten_with_the_injected_keys(tmp_path, planted):
    job = {"label": "a", "promptPath": brief(tmp_path), "model": "opus", "effort": "low", "kind": "author"}
    hso = call(tmp_path, {"script": "dispatch.js", "args": {"jobs": [job]}})
    rewritten = hso["updatedInput"]["args"]
    assert rewritten["jobs"] == [job]
    assert rewritten["__transport"]["name"] == "native" and rewritten["__railsText"] == "RAILS-MARK"


def test_a_missing_native_block_denies_the_call(tmp_path, monkeypatch):
    claude = plant(tmp_path, native=False)
    real_inject = wt.inject
    monkeypatch.setattr(wt, "inject", lambda args, cwd=None, claude_dir=claude: real_inject(args, cwd, claude_dir))
    monkeypatch.setattr(pwd.launch_check, "run_hook", lambda key, args, cwd, lookup=None: (0, ""))
    job = {"label": "a", "promptPath": brief(tmp_path), "model": "opus", "effort": "low", "kind": "author"}
    decision = call(tmp_path, {"script": "dispatch.js", "args": {"jobs": [job]}})
    assert decision["permissionDecision"] == "deny" and "native" in decision["permissionDecisionReason"]


def test_a_raising_step_denies_naming_the_step(tmp_path, planted, monkeypatch):
    def boom(args, cwd=None, claude_dir=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(wt, "inject", boom)
    job = {"label": "a", "promptPath": brief(tmp_path), "model": "opus", "effort": "low", "kind": "author"}
    decision = call(tmp_path, {"script": "dispatch.js", "args": {"jobs": [job]}})
    assert decision["permissionDecision"] == "deny" and "transport failed (RuntimeError: boom)" in decision["permissionDecisionReason"]
