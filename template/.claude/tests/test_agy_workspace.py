#!/usr/bin/env python3
"""Proof for `tools/agy_workspace.py`: the `.agents/` workspace a checkout gets for the Antigravity CLI.

Plants a project root with one CLAUDE layer, a path rule, an invocable and a non-invocable skill, and a `.mcp.json`
holding a project-editing and a read-only server, then checks what `write` produces, that a rerun writes nothing,
that a generated file the plan no longer names goes while an owner's file stays, and that a foreign root is skipped.

    python -m pytest .claude/tests/test_agy_workspace.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "agy_workspace.py")
spec = importlib.util.spec_from_file_location("agy_workspace", TOOL)
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)


def put(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read(root, rel):
    with open(os.path.join(root, ".agents", rel), encoding="utf-8") as fh:
        return fh.read()


def plant(root):
    put(root, ".claude/CLAUDE.core.md", "core\n")
    put(root, ".claude/CLAUDE.godot.md", "a layer the harness no longer ships\n")
    put(root, ".claude/auto-memory/MEMORY.md", "# Memory Index\n")
    put(root, ".claude/rules/cs.md", '---\npaths:\n  - "**/*.cs"\n  - "Jmodot/**"\n---\nbody\n')
    put(root, ".claude/rules/nopaths.md", "no frontmatter\n")
    put(root, ".claude/skills/my_skill/SKILL.md", "---\nname: X\ndescription: >-\n  Use to do the thing. SKIP other.\n---\nbody\n")
    put(root, ".claude/skills/hidden/SKILL.md", "---\ndescription: hidden\ndisable-model-invocation: true\n---\nbody\n")
    put(root, ".mcp.json", json.dumps({"mcpServers": {
        "godot": {"command": "node", "args": ["x"]},
        "git": {"command": "python3", "args": ["${CLAUDE_PROJECT_DIR:-.}/.claude/tools/git_ro_mcp.py"],
                "env": {"GIT_RO_ROOT": "${CLAUDE_PROJECT_DIR:-.}"}}}}))


@pytest.fixture
def project(tmp_path):
    """A planted root with `.agents/` written once; the first write's file count is returned with it."""
    root = str(tmp_path)
    plant(root)
    return root, ws.write(root)


def test_a_claude_layer_is_an_always_on_rule_that_includes_its_source(project):
    root, _ = project
    core = read(root, "rules/claude_core.md")
    assert "trigger: always_on" in core and "@[CLAUDE.core.md](../../.claude/CLAUDE.core.md)" in core, core


def test_a_layer_the_harness_no_longer_ships_gets_no_rule(project):
    root, _ = project
    assert not os.path.exists(os.path.join(root, ".agents", "rules", "claude_godot.md"))


def test_the_memory_index_is_an_always_on_rule_that_includes_its_source(project):
    """Claude Code loads the memory index every session, so the workspace must too."""
    root, _ = project
    memory = read(root, "rules/memory_index.md")
    assert "trigger: always_on" in memory and "(../../.claude/auto-memory/MEMORY.md)" in memory, memory


def test_a_path_rule_is_a_model_decision_pointer_naming_its_globs(project):
    root, _ = project
    rule = read(root, "rules/path_cs.md")
    assert "trigger: model_decision" in rule and "**/*.cs, Jmodot/**" in rule and ".claude/rules/cs.md" in rule, rule


def test_a_rule_with_no_paths_gets_no_pointer(project):
    root, _ = project
    assert not os.path.exists(os.path.join(root, ".agents", "rules", "path_nopaths.md"))


def test_an_invocable_skill_gets_a_stub_with_its_description_and_source_path(project):
    root, _ = project
    stub = read(root, "skills/my-skill/SKILL.md")
    assert 'description: "Use to do the thing. SKIP other."' in stub and ".claude/skills/my_skill/SKILL.md" in stub, stub


def test_a_non_invocable_skill_gets_no_stub(project):
    root, _ = project
    assert not os.path.exists(os.path.join(root, ".agents", "skills", "hidden"))


def test_hooks_route_every_tool_call_through_the_bridge(project):
    root, _ = project
    hooks = json.loads(read(root, "hooks.json"))
    cmd = hooks["project-guards"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "agy_hook_bridge.py" in cmd and cmd.endswith("PreToolUse"), cmd


def test_the_floor_pretooluse_is_the_only_event_registered(project):
    """The bridge answers {} to every other event, so registering one more would be dead weight."""
    root, _ = project
    hooks = json.loads(read(root, "hooks.json"))
    assert [event for group in hooks.values() for event in group] == ["PreToolUse"], json.dumps(hooks)


def test_only_the_read_only_project_server_is_wired_with_the_root_substituted(project):
    root, _ = project
    servers = json.loads(read(root, "mcp_config.json"))["mcpServers"]
    native = root.replace("\\", "/")
    assert ("godot" not in servers and servers["git"]["args"] == [native + "/.claude/tools/git_ro_mcp.py"]
            and servers["git"]["env"]["GIT_RO_ROOT"] == native), json.dumps(servers)


def test_a_rerun_writes_nothing(project):
    root, first = project
    assert first > 0 and ws.write(root) == 0, str(first)


def test_a_generated_file_the_plan_drops_is_removed_and_an_owners_file_stays(tmp_path):
    root = str(tmp_path)
    plant(root)
    ws.write(root)
    os.remove(os.path.join(root, ".claude", "rules", "cs.md"))
    put(root, ".agents/rules/mine.md", "---\ntrigger: always_on\n---\nowner's own rule\n")
    ws.write(root)
    assert not os.path.exists(os.path.join(root, ".agents", "rules", "path_cs.md"))
    assert os.path.exists(os.path.join(root, ".agents", "rules", "mine.md"))


def test_a_root_that_is_not_this_project_is_skipped(tmp_path):
    """Spawned: the CLI's `write --root` entry point is the subject here."""
    foreign = str(tmp_path)
    out = subprocess.run([sys.executable, TOOL, "write", "--root", foreign], capture_output=True, text=True)
    assert out.stdout.startswith("skipped") and not os.path.exists(os.path.join(foreign, ".agents")), out.stdout + out.stderr
