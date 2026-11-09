"""PreToolUse guard: the runtime MCP bridge only ever targets a linked worktree.

`mcp__godot__run_project` / `attach_project` (godot-mcp-runtime) write an `[autoload] McpBridge` line into
the target's project.godot; the bridge removes it at `stop_project`, when the game exits or when its
connection drops. While it is present in the shared main checkout it reaches peer sessions' test runs and
commits, so a call whose `projectPath` resolves to a main worktree (git-dir equal to git-common-dir) is
denied. A linked worktree passes. A missing or unreadable path, or a git probe that fails or times out,
is denied (fail closed). Home: rules/godot_files.md §MCP tools. Proof: tests/test_runtime_mcp_worktree_guard.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _checkout_root import git_dirs  # noqa: E402

GUARDED = {"mcp__godot__run_project", "mcp__godot__attach_project"}
FIX = ("run the MCP bridge in a linked worktree: create one under .claude/worktrees/ "
       "(git worktree add --detach .claude/worktrees/<name>) and pass its path as projectPath. "
       "The bridge writes an McpBridge autoload into the target's project.godot while the game runs "
       "(rules/godot_files.md §MCP tools).")


def verdict(payload):
    if payload.get("tool_name") not in GUARDED:
        return None
    raw = (payload.get("tool_input") or {}).get("projectPath")
    if not isinstance(raw, str) or not raw.strip():
        return "projectPath is missing; " + FIX
    base = payload.get("cwd") or os.getcwd()
    path = os.path.normpath(os.path.join(base, raw))
    if not os.path.isdir(path):
        return f"projectPath '{raw}' is not a readable directory; " + FIX
    dirs = git_dirs(path)
    if dirs is None:
        return f"projectPath '{raw}' is not inside a git worktree; " + FIX
    if dirs[0] == dirs[1]:
        return f"projectPath '{raw}' is a main checkout that other sessions share; " + FIX
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        reason = verdict(payload) if isinstance(payload, dict) else None
    except Exception as exc:
        reason = f"guard could not read the call ({exc}); " + FIX
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
