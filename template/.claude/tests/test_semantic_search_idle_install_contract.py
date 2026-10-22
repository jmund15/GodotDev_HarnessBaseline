#!/usr/bin/env python3
"""Minimal proof for tools/semantic_search_idle_install.py: the decision path with a stubbed installer.

    python3 .claude/tests/test_semantic_search_idle_install_contract.py

Every path lives under a temp home. The two contract spawns (the shim's and the stock server's) are stubbed to
return the same contract, so no node process starts and nothing is installed on this machine. A
not-installed home refuses; an installed home passes; a shim edited after install refuses.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "semantic_search_idle_install.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("semantic_search_idle_install_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub_spawns(module):
    contract = {"contract": "planted"}
    module.proxy_contract = lambda paths, plugin_root: dict(contract)
    module.stock_contract = lambda paths, plugin_root: dict(contract)


def planted_home(module, directory):
    base = Path(directory)
    home = base / "home"
    claude = home / ".claude"
    install_path = base / "plugin"
    (install_path / "dist").mkdir(parents=True)
    (install_path / "dist" / "mcp-server.js").write_text("// planted backend\n", encoding="utf-8")
    (install_path / "node_modules").mkdir()
    (install_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {module.MCP_ENTRY: module.STOCK_ENTRY}}), encoding="utf-8")
    registry = claude / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"plugins": {module.PLUGIN_KEY: [
        {"scope": "user", "installPath": str(install_path), "version": "1.0.0"}]}}), encoding="utf-8")
    state_dir = claude / "semantic-search-idle"
    backups = state_dir / module.BACKUP_DIR
    backups.mkdir(parents=True)
    (backups / "mcp.json.1.0.0.20261021T000000.bak").write_text(
        json.dumps({"mcpServers": {module.MCP_ENTRY: module.STOCK_ENTRY}}), encoding="utf-8")
    settings = claude / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    user_config = home / ".claude.json"
    user_config.write_text("{}\n", encoding="utf-8")
    repo = base / "repo"
    (repo / ".claude" / "tools").mkdir(parents=True)
    (repo / module.PROXY_SOURCE).write_text("// planted shim\n", encoding="utf-8")
    (repo / module.CHECKER_SOURCE).write_text("# planted checker\n", encoding="utf-8")
    module.repo_root = lambda: repo
    return ["--home", str(home), "--registry", str(registry), "--state-dir", str(state_dir),
            "--settings", str(settings), "--user-config", str(user_config), "--node", "node"], state_dir


def run(module, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = module.main(argv)
    return code, out.getvalue()


def case_not_installed_home_refuses(directory):
    module = load_tool()
    stub_spawns(module)
    args, _ = planted_home(module, directory)
    code, text = run(module, ["--check", *args])
    assert code == 1, "a home with no install state must exit 1"
    assert "idle unload is not installed" in text, text


def case_installed_home_passes_check(directory):
    module = load_tool()
    stub_spawns(module)
    args, _ = planted_home(module, directory)
    code, text = run(module, ["--install", *args])
    assert code == 0, text
    code, text = run(module, ["--check", *args])
    assert code == 0, "a fresh install must pass its own check: " + text


def case_shim_edited_after_install_refuses(directory):
    module = load_tool()
    stub_spawns(module)
    args, state_dir = planted_home(module, directory)
    assert run(module, ["--install", *args])[0] == 0
    (state_dir / module.SHIM_NAME).write_text("// edited after install\n", encoding="utf-8")
    code, text = run(module, ["--check", *args])
    assert code == 1, "an edited shim must exit 1"
    assert "installed shim content changed" in text, text


def main():
    cases = [case_not_installed_home_refuses, case_installed_home_passes_check,
             case_shim_edited_after_install_refuses]
    failures = []
    for case in cases:
        directory = tempfile.mkdtemp(prefix="semantic-search-idle-proof-")
        try:
            case(directory)
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
