#!/usr/bin/env python3
"""Re-runnable proof for hooks/semantic_search_scope_guard.py — a semantic-search call whose
searchDir is not a git top-level, or whose restrictToDir is non-POSIX, is blocked (exit 2 +
stderr) through both process() directly and the real pre_read_dispatch.py channel. Other tools and
well-formed calls pass.

    python3 .claude/tests/test_semantic_search_scope_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HOOKS_DIR = os.path.join(ROOT, ".claude", "hooks")
GUARD = os.path.join(HOOKS_DIR, "semantic_search_scope_guard.py")
DISPATCH = os.path.join(HOOKS_DIR, "pre_read_dispatch.py")
TOOL = "mcp__plugin_semantic-search_semantic-search__search"

sys.path.insert(0, HOOKS_DIR)
import semantic_search_scope_guard as guard  # noqa: E402


def run_standalone(payload, env=None):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    full_env = dict(os.environ)
    full_env["CLAUDE_PROJECT_DIR"] = ROOT
    if env:
        full_env.update(env)
    r = subprocess.run([sys.executable, GUARD, "--hook"], input=raw, capture_output=True,
                        text=True, encoding="utf-8", timeout=60, env=full_env)
    return r.returncode, r.stderr or ""


def run_dispatch(payload, env=None):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    full_env = dict(os.environ)
    full_env["CLAUDE_PROJECT_DIR"] = ROOT
    if env:
        full_env.update(env)
    r = subprocess.run([sys.executable, DISPATCH], input=raw, capture_output=True,
                        text=True, encoding="utf-8", timeout=60, env=full_env)
    return r.returncode, r.stderr or ""


def main():
    cases = []

    # --- process() direct ---
    cases.append(("project-root searchDir allowed (process)",
                  guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ROOT},
                                  "cwd": ROOT}) is None))
    cases.append((".claude searchDir denied (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": ".claude"}, "cwd": ROOT}) is not None))
    abs_claude = os.path.join(ROOT, ".claude")
    cases.append(("absolute path of .claude denied (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": abs_claude}, "cwd": ROOT}) is not None))
    with tempfile.TemporaryDirectory() as td:
        project = os.path.join(td, "project")
        nested = os.path.join(project, "vendor", "Sub")
        sibling = os.path.join(td, "sibling")
        os.makedirs(os.path.join(project, ".git"))
        os.makedirs(nested)
        os.makedirs(os.path.join(sibling, ".git"))
        with open(os.path.join(nested, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: ../../.git/modules/Sub\n")
        saved_project_dir = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        try:
            cases.append(("submodule git top-level inside the project allowed (process)",
                          guard.process({"tool_name": TOOL, "tool_input": {"searchDir": nested},
                                         "cwd": project}) is None))
            cases.append(("git top-level outside the project denied (process)",
                          guard.process({"tool_name": TOOL, "tool_input": {"searchDir": sibling},
                                         "cwd": project}) is not None))
            cases.append(("relative ../sibling git top-level denied (process)",
                          guard.process({"tool_name": TOOL, "tool_input": {"searchDir": "../sibling"},
                                         "cwd": project}) is not None))
        finally:
            if saved_project_dir is not None:
                os.environ["CLAUDE_PROJECT_DIR"] = saved_project_dir
    cases.append(("restrictToDir .claude/auto-memory allowed (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": ROOT,
                                                 "restrictToDir": ".claude/auto-memory"},
                                  "cwd": ROOT}) is None))
    cases.append(("absolute C:/Users/x/.claude searchDir denied (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": "C:/Users/x/.claude"},
                                  "cwd": ROOT}) is not None))
    cases.append(("backslash restrictToDir .claude\\\\hooks denied (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": ROOT,
                                                 "restrictToDir": ".claude\\hooks"},
                                  "cwd": ROOT}) is not None))
    for bad in ("../Tests", ".claude/../Scripts", "./.claude", ".claude/./hooks", "..", ".claude//hooks"):
        cases.append(("non-canonical restrictToDir %r denied (process)" % bad,
                      guard.process({"tool_name": TOOL,
                                      "tool_input": {"searchDir": ROOT, "restrictToDir": bad},
                                      "cwd": ROOT}) is not None))
    cases.append(("one trailing slash .claude/auto-memory/ allowed (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": ROOT, "restrictToDir": ".claude/auto-memory/"},
                                  "cwd": ROOT}) is None))
    cases.append(("dotted segment name .claude/.cache allowed (process)",
                  guard.process({"tool_name": TOOL,
                                  "tool_input": {"searchDir": ROOT, "restrictToDir": ".claude/.cache"},
                                  "cwd": ROOT}) is None))
    cases.append(("adjacent Grep tool with path .claude untouched (process)",
                  guard.process({"tool_name": "Grep",
                                  "tool_input": {"path": ".claude"}, "cwd": ROOT}) is None))
    cases.append(("non-dict payload no crash (process)", guard.process("not a dict") is None))
    cases.append(("payload missing tool_input no crash (process)",
                  guard.process({"tool_name": TOOL}) is None))
    cases.append(("absent searchDir allowed (process)",
                  guard.process({"tool_name": TOOL, "tool_input": {}, "cwd": ROOT}) is None))

    # --- deny message names the correct call ---
    result = guard.process({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"}, "cwd": ROOT})
    msg = (result or {}).get("deny") or ""
    cases.append(("deny message names project root and a restrictToDir example",
                  bool(msg) and "restrictToDir" in msg and ".claude/auto-memory" in msg))

    # --- standalone --hook channel ---
    rc, err = run_standalone({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"},
                               "cwd": ROOT})
    cases.append(("standalone --hook channel: planted .claude searchDir -> exit 2 + stderr",
                  rc == 2 and bool(err)))
    rc, err = run_standalone({"tool_name": TOOL, "tool_input": {"searchDir": ROOT}, "cwd": ROOT})
    cases.append(("standalone --hook channel: project-root searchDir -> exit 0",
                  rc == 0 and not err))
    rc, err = run_standalone("{not json")
    cases.append(("standalone --hook channel: malformed payload -> exit outside crash range",
                  rc in (0, 2)))

    # --- real pre_read_dispatch.py channel ---
    rc, err = run_dispatch({"tool_name": TOOL, "tool_input": {"searchDir": ".claude"},
                             "cwd": ROOT})
    cases.append(("real pre_read_dispatch.py channel: .claude searchDir denied",
                  rc == 2 and bool(err)))
    rc, err = run_dispatch({"tool_name": TOOL, "tool_input": {"searchDir": ROOT}, "cwd": ROOT})
    cases.append(("real pre_read_dispatch.py channel: project-root searchDir allowed",
                  rc == 0))
    rc, err = run_dispatch({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": ".claude"},
                             "cwd": ROOT})
    cases.append(("real pre_read_dispatch.py channel: adjacent Grep untouched", rc == 0))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
