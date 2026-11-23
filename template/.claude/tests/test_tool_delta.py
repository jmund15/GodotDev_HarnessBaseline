#!/usr/bin/env python3
"""A job's `tools` delta resolves at the one entry (hooks/pre_workflow_dispatch.py) before any agent starts (pytest).

Each case plants a tool_profiles.json under tmp_path and calls `dispatch(raw)` in process. The last two cases read the
live table: every variant names an existing agent file, and every delegate agent is reachable from a row.
"""
import json
import sys
from pathlib import Path

import pytest

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "hooks"))
import pre_workflow_dispatch as pwd  # noqa: E402
import workflow_transport as wt  # noqa: E402
from test_workflow_transport import plant  # noqa: E402

TABLE = {
    "kinds": {
        "scout": {"agentType": "delegate-survey", "builtins": ["Read", "Glob", "Grep"], "mcpServers": []},
        "reviewer": {"agentType": "delegate-review", "builtins": ["Read", "Glob", "Grep"], "mcpServers": []},
        "gate": {"agentType": "delegate-gate", "builtins": ["Read", "Grep", "Bash"], "mcpServers": []},
        "author": {"agentType": "delegate-author", "builtins": ["Read", "Edit", "Bash"], "mcpServers": [],
                   "writes": True},
    },
    "variants": [{"kind": "reviewer", "add": ["Bash"], "agentType": "delegate-review-sh"}],
}


@pytest.fixture
def claude(tmp_path, monkeypatch):
    root = plant(tmp_path)
    (root / "guards" / "tool_profiles.json").write_text(json.dumps(TABLE), encoding="utf-8")
    real_inject = wt.inject
    monkeypatch.setattr(wt, "inject", lambda args, cwd=None, claude_dir=root: real_inject(args, cwd, claude_dir))
    monkeypatch.setattr(pwd, "CLAUDE_DIR", root)
    monkeypatch.setattr(pwd.launch_check, "run_hook", lambda key, args, cwd, lookup=None: (0, ""))
    monkeypatch.delenv("CLAUDE_CODE_SIDECAR", raising=False)
    return root


def run(tmp_path, kind, tools="unset"):
    (tmp_path / "a.md").write_text("the brief", encoding="utf-8")
    job = {"label": "t6q", "promptPath": "a.md", "model": "sonnet", "effort": "low", "kind": kind}
    if tools != "unset":
        job["tools"] = tools
    raw = json.dumps({"tool_name": "Workflow", "cwd": str(tmp_path), "tool_input": {"args": {"jobs": [job]}}})
    code, out, _err = pwd.dispatch(raw)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"]


@pytest.mark.parametrize("tools", ["+Bash", " +Bash ", ["+Bash"]])
def test_a_covered_delta_selects_the_variant_agent_and_builtins(tmp_path, claude, tools):
    out = run(tmp_path, "reviewer", tools)
    row = out["updatedInput"]["args"]["__toolProfiles"]["t6q"]
    assert row["agentType"] == "delegate-review-sh"
    assert row["builtins"] == ["Read", "Glob", "Grep", "Bash"]


def test_no_delta_is_the_kind_row_unchanged(tmp_path, claude):
    row = run(tmp_path, "reviewer")["updatedInput"]["args"]["__toolProfiles"]["t6q"]
    assert row["agentType"] == "delegate-review" and row["builtins"] == ["Read", "Glob", "Grep"]


def test_an_uncovered_delta_is_refused_with_the_fix_first(tmp_path, claude):
    out = run(tmp_path, "scout", "+Bash")
    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert reason.startswith("t6q: scout +Bash has no profile; use kind ")
    assert "gate (Read, Grep, Bash)" in reason and "reviewer +Bash" in reason and "author" in reason
    assert "scout" not in reason.split("use ", 1)[1]


@pytest.mark.parametrize("tools", ["Bash", "+", "", [], 5, "+Bash,Grep", {"add": "Bash"}])
def test_a_malformed_delta_is_refused(tmp_path, claude, tools):
    assert run(tmp_path, "reviewer", tools)["permissionDecision"] == "deny"


def _table():
    return json.loads((CLAUDE / "guards" / "tool_profiles.json").read_text(encoding="utf-8"))


def test_every_variant_names_an_existing_agent_file():
    for row in _table()["variants"]:
        assert (CLAUDE / "agents" / (row["agentType"] + ".md")).is_file(), row


def test_every_delegate_agent_is_reachable_from_a_row():
    table = _table()
    used = {r["agentType"] for r in table["kinds"].values()} | {r["agentType"] for r in table["variants"]}
    for path in (CLAUDE / "agents").glob("delegate-*.md"):
        assert path.stem in used, "%s is a dead profile" % path.name
