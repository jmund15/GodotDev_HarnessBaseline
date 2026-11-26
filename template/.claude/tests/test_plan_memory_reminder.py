#!/usr/bin/env python3
"""Proof of hooks/plan_memory_reminder.py: a plan write is judged by the files the plan declares, matched against the
`paths` globs of `adaptation.json` `memory_domains` rows, never by the words in its prose. The declared files are the
write-set cells of the plan's `## Job map` table (`plan_file_format.md`), read through `_job_map`.

Cases run in process: the rows are planted through `adaptation.get`, the plan is a real file on disk, and the
dispatcher case drives the registered chain end to end (`post_edit_dispatch.dispatch`).

    python3 -m pytest -q .claude/tests/test_plan_memory_reminder.py
"""
import importlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(CLAUDE, "hooks"), os.path.join(CLAUDE, "tools")]
import _hook_state as state  # noqa: E402
import adaptation  # noqa: E402

# `return` and `stat` are the prose words that tripped the old keyword matcher on every plan.
ROWS = [
    {"name": "Widgets", "paths": ["**/Widgets/**", "*.widget"],
     "memory_keywords": ["widget", "gizmo"], "skills": ["widget_authoring", "testing"],
     "rules": [".claude/rules/widgets.md"]},
    {"name": "Pipelines", "paths": ["Pipes/**"],
     "memory_keywords": ["conveyor"], "skills": [], "rules": []},
    {"name": "Words only", "memory_keywords": ["gizmo"], "skills": ["gizmo_skill"], "rules": []},
]
PROSE = "This plan will return the stat of each state and status. " * 8


@pytest.fixture
def hook(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(adaptation, "get", lambda _dir, key: ROWS if key == "memory_domains" else [])
    import plan_memory_reminder
    importlib.reload(plan_memory_reminder)
    plan_memory_reminder.plans_dir = tmp_path / ".claude" / "plans"
    return plan_memory_reminder


def write(hook, name, body):
    path = hook.plans_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return str(path)


def call(hook, path, session="s1", tool="Write", fragment=None):
    inputs = {"file_path": path, "content": fragment} if tool == "Write" else {
        "file_path": path, "old_string": "x", "new_string": fragment or "x"}
    return hook.process({"tool_name": tool, "session_id": session, "tool_input": inputs})


def files_plan(*paths, prose=PROSE):
    """A plan whose Job map has one slice per path, each path in the Write set cell."""
    rows = "\n".join("| %d | `%s` | — | inline: small |" % (n, p) for n, p in enumerate(paths, 1))
    return ("# Plan\n\n%s\n\n## Job map\n\n| Slice | Write set | Waits on | Route |\n|---|---|---|---|\n%s\n\n"
            "## Verification\n\nrun it\n" % (prose, rows))


def test_declared_file_fires_without_any_trigger_word(hook):
    got = call(hook, write(hook, "a.md", files_plan("src/Widgets/Core.cs", prose="Rework the thing.")))
    ctx = got["context"]
    assert "Widgets" in ctx and "widget_authoring" in ctx and "gizmo" in ctx
    assert ".claude/rules/widgets.md" in ctx and "src/Widgets/Core.cs" in ctx
    assert "Pipelines" not in ctx and "gizmo_skill" not in ctx


def test_prose_words_do_not_fire(hook):
    assert call(hook, write(hook, "a.md", files_plan("docs/notes.md", ".claude/hooks/x.py"))) is None
    assert call(hook, write(hook, "b.md", files_plan("docs/notes.md") + "widget gizmo pipeline " + PROSE)) is None


def test_harness_paths_are_not_project_scope_even_where_a_glob_would_match(hook):
    body = files_plan(".claude/tests/test_x.py", ".claude/hooks/Widgets/y.py", "C:/repo/.claude/Widgets/z.md")
    assert call(hook, write(hook, "a.md", body)) is None
    assert call(hook, write(hook, "b.md", files_plan(".claude/tests/test_x.py", "src/Widgets/Core.cs")))


def test_plan_with_no_declared_file_is_silent(hook):
    assert call(hook, write(hook, "a.md", "# Plan\n\nwidget widget pipeline gizmo " + PROSE)) is None
    assert call(hook, write(hook, "b.md", "# Plan\n\nEdit Pipes/belt.cs and src/Widgets/Core.cs.\n")) is None


def test_only_the_write_set_cells_declare_files(hook):
    body = files_plan("docs/notes.md", prose="Reads Pipes/belt.cs for reference.")
    body = body.replace("| 1 | `docs/notes.md` | — | inline: small |",
                        "| 1 | `docs/notes.md` | Pipes/belt.cs | inline: reads src/Widgets/Core.cs |")
    assert call(hook, write(hook, "a.md", body)) is None
    two = files_plan("docs/a.md", "src/Widgets/Core.cs").replace("## Verification", "## Out of scope\n\n- `Pipes/x.cs`\n\n## Verification")
    ctx = call(hook, write(hook, "b.md", two))["context"]
    assert "Widgets" in ctx and "Pipelines" not in ctx


def test_a_plan_without_a_job_map_write_set_declares_nothing(hook):
    names = ("Files to modify", "Critical files", "Files changed", "Files affected", "Bounded file list")
    for n, name in enumerate(names):
        body = "# Plan\n\n## %s\n\n- `src/Widgets/Core.cs`\n\nEdit Pipes/belt.cs here.\n" % name
        assert call(hook, write(hook, "a%d.md" % n, body)) is None
    no_column = ("# Plan\n\n## Job map\n\n| Job | Work class | Route |\n|---|---|---|\n"
                 "| J1 | clear | edits `src/Widgets/Core.cs` |\n")
    assert call(hook, write(hook, "b.md", no_column)) is None
    prose_map = "# Plan\n\n## Job map\n\n- Wave 1: src/Widgets/Core.cs\n"
    assert call(hook, write(hook, "c.md", prose_map)) is None


def test_a_numbered_job_map_heading_and_column_case_are_read(hook):
    body = ("# Plan\n\n### 4. Job map\n\n| Slice | WRITE SET |\n|---|---|\n| 1 | src/Widgets/Core.cs, their tests |\n")
    assert "Widgets" in call(hook, write(hook, "a.md", body))["context"]


def test_section_edit_reads_the_declared_files_from_disk(hook):
    path = write(hook, "a.md", files_plan("src/Widgets/Core.cs") + "\n## Phase B\n\nTidy.\n")
    got = call(hook, path, tool="Edit", fragment="## Phase B\n\nTidy it.\n")
    assert got and "Widgets" in got["context"]


def test_once_per_domain_set_and_a_new_set_fires_again(hook):
    widgets = write(hook, "a.md", files_plan("src/Widgets/Core.cs"))
    assert call(hook, widgets)
    assert call(hook, widgets) is None
    assert call(hook, write(hook, "b.md", files_plan("src/Widgets/Other.cs"))) is None
    assert call(hook, write(hook, "c.md", files_plan("src/Widgets/Core.cs", "Pipes/belt.cs")))
    assert call(hook, widgets, session="s2")


def test_skills_already_loaded_are_omitted(hook):
    state.write_json_atomic(state.state_path("s1"), {"skills_loaded": ["testing"]})
    ctx = call(hook, write(hook, "a.md", files_plan("src/Widgets/Core.cs")))["context"]
    assert "widget_authoring" in ctx and "testing" not in ctx


def test_not_a_plan_write_is_silent(hook, tmp_path):
    body = files_plan("src/Widgets/Core.cs")
    other = tmp_path / "notes" / "a.md"
    other.parent.mkdir()
    other.write_text(body)
    assert call(hook, str(other)) is None
    assert call(hook, write(hook, "a.txt", body)) is None
    assert hook.process({"tool_name": "Read", "tool_input": {"file_path": write(hook, "b.md", body)}}) is None


def test_dispatcher_delivers_it_as_post_tool_use_context(hook):
    import post_edit_dispatch
    assert "plan_memory_reminder" in [name for name, _ in post_edit_dispatch.CHAIN]
    path = write(hook, "a.md", files_plan("src/Widgets/Core.cs"))
    code, out, _err = post_edit_dispatch.dispatch(
        {"tool_name": "Write", "session_id": "d1", "tool_input": {"file_path": path, "content": "x"}})
    specific = json.loads(out)["hookSpecificOutput"]
    assert code == 0 and specific["hookEventName"] == "PostToolUse" and "widget_authoring" in specific["additionalContext"]
