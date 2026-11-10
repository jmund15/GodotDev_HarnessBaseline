"""Plant the MCP server registrations a tool profile resolves, so a proof reads none of its machine's or project's.

The servers a profile names (`godot`, `git`, `ai-worker`, `semantic-search`) are registrations of the
machine and the project (`.mcp.json`, the login's `.claude.json`, an installed plugin), which differ per
project and are absent on a CI runner. `plant()` writes all four under a temp root and points
`CLAUDE_CONFIG_DIR` and `SC_WORKDIR` at it, so `tool_profiles` finds them in this process and in any
child that inherits the environment.

Not named `test_*.py` on purpose: `harness_tests.py` would run a helper as a proof.

    import _mcp_plant
    _mcp_plant.plant()      # before the first tool_profiles call
"""
import json
import os
import tempfile


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle)


def plant():
    """Plant the four servers and set the two environment variables; returns the repo and login paths."""
    root = tempfile.mkdtemp(prefix="mcp_plant_")
    repo = os.path.join(root, "repo")
    login = os.path.join(root, "login")
    plugin = os.path.join(root, "semantic-search-plugin")
    os.makedirs(os.path.join(repo, ".git"))
    _write_json(os.path.join(repo, ".mcp.json"),
                {"mcpServers": {"godot": {"command": "godot-server"}, "git": {"command": "git-server"}}})
    _write_json(os.path.join(login, ".claude.json"), {"mcpServers": {"ai-worker": {"command": "aiw"}}})
    _write_json(os.path.join(login, "plugins", "installed_plugins.json"),
                {"version": 2, "plugins": {"semantic-search@planted": [
                    {"scope": "user", "installPath": plugin}]}})
    _write_json(os.path.join(plugin, ".claude-plugin", "plugin.json"),
                {"mcpServers": {"semantic-search": {"command": "ss"}}})
    os.environ["CLAUDE_CONFIG_DIR"] = login
    os.environ["SC_WORKDIR"] = repo
    return repo, login
