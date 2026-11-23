#!/usr/bin/env python3
"""Proof for `tools/agy_workspace.py`: the `.agents/` workspace a checkout gets for the Antigravity CLI.

Plants a project root with one CLAUDE layer, a path rule, an invocable and a non-invocable skill, and a `.mcp.json`
holding a project-editing and a read-only server, then checks what `write` produces, that a rerun writes nothing,
that a generated file the plan no longer names goes while an owner's file stays, and that a foreign root is skipped.

    python3 .claude/tests/test_agy_workspace.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "agy_workspace.py")
spec = importlib.util.spec_from_file_location("agy_workspace", TOOL)
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)

ROOT = tempfile.mkdtemp(prefix="agy_ws_")


def put(rel, text):
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


put(".claude/CLAUDE.core.md", "core\n")
put(".claude/auto-memory/MEMORY.md", "# Memory Index\n")
put(".claude/rules/cs.md", '---\npaths:\n  - "**/*.cs"\n  - "Jmodot/**"\n---\nbody\n')
put(".claude/rules/nopaths.md", "no frontmatter\n")
put(".claude/skills/my_skill/SKILL.md", "---\nname: X\ndescription: >-\n  Use to do the thing. SKIP other.\n---\nbody\n")
put(".claude/skills/hidden/SKILL.md", "---\ndescription: hidden\ndisable-model-invocation: true\n---\nbody\n")
put(".mcp.json", json.dumps({"mcpServers": {
    "godot": {"command": "node", "args": ["x"]},
    "git": {"command": "python3", "args": ["${CLAUDE_PROJECT_DIR:-.}/.claude/tools/git_ro_mcp.py"],
            "env": {"GIT_RO_ROOT": "${CLAUDE_PROJECT_DIR:-.}"}}}}))

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok else f"   [{detail}]"))
    fails += not ok


def read(rel):
    with open(os.path.join(ROOT, ".agents", rel), encoding="utf-8") as fh:
        return fh.read()


first = ws.write(ROOT)
agents = os.path.join(ROOT, ".agents")
core = read("rules/claude_core.md")
check("a CLAUDE layer is an always-on rule that includes its source",
      "trigger: always_on" in core and "@[CLAUDE.core.md](../../.claude/CLAUDE.core.md)" in core, core)
check("an absent layer gets no rule", not os.path.exists(os.path.join(agents, "rules", "claude_godot.md")))
memory = read("rules/memory_index.md")
check("the memory index is an always-on rule that includes its source, as Claude Code loads it every session",
      "trigger: always_on" in memory and "(../../.claude/auto-memory/MEMORY.md)" in memory, memory)
rule = read("rules/path_cs.md")
check("a path rule is a model-decision pointer naming its globs",
      "trigger: model_decision" in rule and "**/*.cs, Jmodot/**" in rule and ".claude/rules/cs.md" in rule, rule)
check("a rule with no paths gets no pointer", not os.path.exists(os.path.join(agents, "rules", "path_nopaths.md")))
stub = read("skills/my-skill/SKILL.md")
check("an invocable skill gets a stub with its description and its source path",
      'description: "Use to do the thing. SKIP other."' in stub and ".claude/skills/my_skill/SKILL.md" in stub, stub)
check("a non-invocable skill gets no stub", not os.path.exists(os.path.join(agents, "skills", "hidden")))
hooks = json.loads(read("hooks.json"))
cmd = hooks["project-guards"]["PreToolUse"][0]["hooks"][0]["command"]
check("hooks route every tool call through the bridge", "agy_hook_bridge.py" in cmd and cmd.endswith("PreToolUse"), cmd)
check("the floor's PreToolUse is the only event registered: the bridge answers {} to every other",
      [event for group in hooks.values() for event in group] == ["PreToolUse"], json.dumps(hooks))
servers = json.loads(read("mcp_config.json"))["mcpServers"]
native = ROOT.replace("\\", "/")
check("only the read-only project server is wired, with the root substituted",
      "godot" not in servers and servers["git"]["args"] == [native + "/.claude/tools/git_ro_mcp.py"]
      and servers["git"]["env"]["GIT_RO_ROOT"] == native, json.dumps(servers))
check("a rerun writes nothing", first > 0 and ws.write(ROOT) == 0, str(first))

os.remove(os.path.join(ROOT, ".claude", "rules", "cs.md"))
put(".agents/rules/mine.md", "---\ntrigger: always_on\n---\nowner's own rule\n")
ws.write(ROOT)
check("a generated file the plan drops is removed, an owner's file stays",
      not os.path.exists(os.path.join(agents, "rules", "path_cs.md"))
      and os.path.exists(os.path.join(agents, "rules", "mine.md")))

foreign = tempfile.mkdtemp(prefix="agy_ws_foreign_")
out = subprocess.run([sys.executable, TOOL, "write", "--root", foreign], capture_output=True, text=True)
check("a root that is not this project is skipped", out.stdout.startswith("skipped")
      and not os.path.exists(os.path.join(foreign, ".agents")), out.stdout + out.stderr)

print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
sys.exit(1 if fails else 0)
