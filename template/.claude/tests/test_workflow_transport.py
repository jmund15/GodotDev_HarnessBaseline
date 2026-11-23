#!/usr/bin/env python3
"""Proof for hooks/workflow_transport.py: the keys the kept Workflow engines read, injected on each call (T4).

Each case plants its own claude dir (seats.json, guards/rails.md, guards/tool_profiles.json) under tmp_path, so
the proof never depends on the live seats file. A missing file fails closed, and no capacity, tier or effort check
runs here: the injected keys carry only what the engines read.
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import workflow_transport as wt  # noqa: E402

ALLOWED_KEYS = {"__transport", "__toolProfiles", "__kindProfile", "__railsText", "__repoRoot"}


def plant(tmp_path, native=True, rails=True):
    claude = tmp_path / "claude"
    (claude / "guards").mkdir(parents=True)
    seats = {"models": {"claude-sonnet-5-5": {}}}
    if native:
        seats["native"] = {"ids": ["opus", "sonnet"], "efforts": ["low", "high"]}
    (claude / "seats.json").write_text(json.dumps(seats), encoding="utf-8")
    if rails:
        (claude / "guards" / "rails.md").write_text("RAILS-MARK", encoding="utf-8")
    profiles = {"kinds": {
        "author": {"agentType": "delegate-author", "builtins": ["Read", "Edit"], "mcpServers": ["ai-worker"],
                   "writes": True},
        "reviewer": {"agentType": "delegate-review", "builtins": ["Read"], "mcpServers": []},
    }}
    (claude / "guards" / "tool_profiles.json").write_text(json.dumps(profiles), encoding="utf-8")
    return claude


def test_injects_the_native_vocabulary_and_the_one_rails_text(tmp_path):
    out = wt.inject({"jobs": []}, cwd=None, claude_dir=plant(tmp_path))
    assert out["__transport"] == {"name": "native", "ids": ["opus", "sonnet"], "efforts": ["low", "high"]}
    assert out["__railsText"] == "RAILS-MARK"
    assert "__repoRoot" not in out


def test_missing_native_block_fails_closed(tmp_path):
    with pytest.raises(wt.TransportError, match="native"):
        wt.inject({"jobs": []}, cwd=None, claude_dir=plant(tmp_path, native=False))


def test_missing_rails_file_fails_closed(tmp_path):
    with pytest.raises(wt.TransportError, match="rails.md"):
        wt.inject({"jobs": []}, cwd=None, claude_dir=plant(tmp_path, rails=False))


def test_profile_rows_go_only_to_kinds_with_a_row_and_keep_three_fields(tmp_path):
    args = {"jobs": [{"label": "a", "kind": "author"}, {"label": "s", "kind": "scout"}]}
    profiles = wt.inject(args, cwd=None, claude_dir=plant(tmp_path))["__toolProfiles"]
    assert profiles == {"a": {"kind": "author", "agentType": "delegate-author", "builtins": ["Read", "Edit"],
                              "mcpServers": ["ai-worker"]}}


def test_the_calls_own_kind_is_injected_as_the_single_kind_profile(tmp_path):
    claude = plant(tmp_path)
    assert wt.inject({"kind": "author"}, cwd=None, claude_dir=claude)["__kindProfile"] == {
        "kind": "author", "agentType": "delegate-author", "builtins": ["Read", "Edit"], "mcpServers": ["ai-worker"]}
    for args in ({}, {"kind": "scout"}, {"kind": "nope"}):  # none, a kind without a row, an unknown kind
        assert "__kindProfile" not in wt.inject(args, cwd=None, claude_dir=claude)


def test_no_capacity_tier_or_effort_key_is_injected(tmp_path):
    args = {"jobs": [{"label": "a", "kind": "author", "tier": "architect"}]}
    out = wt.inject(args, cwd=None, claude_dir=plant(tmp_path))
    assert set(out) - set(args) <= ALLOWED_KEYS
    assert all("tier" not in row for row in out["__toolProfiles"].values())


def test_every_field_of_the_shipped_profile_table_is_one_a_reader_reads():
    """An unread field looks like enforcement and enforces nothing: `kind_row` reads `writes`, `_profile` the other three."""
    claude = Path(__file__).resolve().parents[1]
    table = json.loads((claude / "guards" / "tool_profiles.json").read_text(encoding="utf-8"))
    readable = {"agentType", "builtins", "mcpServers", "writes"}
    assert not [(k, set(row) - readable) for k, row in table["kinds"].items() if set(row) - readable]
    assert set(table) == {"version", "kinds", "variants"} and "arm" not in table["kinds"]
    assert not [k for k in table["kinds"] if k.startswith("bench")]


def test_a_directory_git_cannot_answer_for_gives_no_repo_root(tmp_path):
    assert wt.repo_root(str(tmp_path / "absent")) is None


def test_every_agent_type_a_profile_names_is_a_shipped_agent_file_with_its_tools():
    """The engines launch each child as `agentType`; a name with no `agents/<name>.md` fails every job at launch."""
    claude = Path(__file__).resolve().parents[1]
    table = json.loads((claude / "guards" / "tool_profiles.json").read_text(encoding="utf-8"))
    rows = list(table["kinds"].values()) + table.get("variants", [])
    named = {}
    for row in rows:
        if row.get("agentType"):
            base = next((r for r in table["kinds"].values() if r.get("agentType") and "kind" in row
                         and r is table["kinds"][row["kind"]]), row)
            named[row["agentType"]] = list(base.get("builtins") or []) + list(row.get("add") or [])
    assert named, "no agent type is named"
    for name, builtins in named.items():
        text = (claude / "agents" / (name + ".md")).read_text(encoding="utf-8")
        front = text.split("---")[1]
        assert ("name: %s" % name) in front.splitlines()
        tools = next(l for l in front.splitlines() if l.startswith("tools:"))[len("tools:"):]
        listed = [t.strip() for t in tools.split(",")]
        assert set(builtins) <= set(listed), (name, builtins, listed)
        assert not ({"Edit", "Write"} & set(listed)) or {"Edit", "Write"} <= set(builtins), name
