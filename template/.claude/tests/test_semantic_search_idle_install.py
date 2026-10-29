#!/usr/bin/env python3
"""Proof for tools/semantic_search_idle_install.py — install, drift-check and roll back the shim.

The plugin root is a junction farm over the REAL installed plugin under a planted home, so the
stock-child contract probe runs for real and no live user file is read or written.

Run: python3 -m unittest .claude/tests/test_semantic_search_idle_install.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / ".claude" / "tools"))

import semantic_search_idle_install as inst  # noqa: E402

REGISTRY_SOURCE = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
if not REGISTRY_SOURCE.exists():
    print("PLATFORM-SKIP-PROOF test_semantic_search_idle_install: no semantic-search plugin is installed on this machine")
    sys.exit(0)
PLUGIN_KEY = "semantic-search@dreb"
STOCK_ENTRY = {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/bin/server.js"]}


def active_plugin_root() -> Path:
    data = json.loads(REGISTRY_SOURCE.read_text(encoding="utf-8"))
    entries = [e for e in data["plugins"][PLUGIN_KEY] if e.get("scope") == "user"]
    assert len(entries) == 1, "expected exactly one user-scope registration"
    return Path(entries[0]["installPath"])


def junction(link: Path, target: Path) -> None:
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"mklink failed for {link}: {out.stdout} {out.stderr}")


class Fixture:
    """A planted home plus a plugin root that behaves like the real install."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="idle-install-proof-"))
        self.home = self.tmp / "home"
        (self.home / ".claude" / "plugins").mkdir(parents=True)
        self.plugin_root = self.tmp / "pluginroot"
        self.plugin_root.mkdir()
        real = active_plugin_root()
        for name in ("bin", "dist", "node_modules", "grammars"):
            if (real / name).exists():
                junction(self.plugin_root / name, real / name)
        (self.plugin_root / "package.json").write_bytes((real / "package.json").read_bytes())
        # The live manifest may already carry the installed shim, so the fixture plants the stock entry.
        inst.write_json_atomic(self.plugin_root / ".mcp.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
        self.stock_bytes = (self.plugin_root / ".mcp.json").read_bytes()
        self.version = json.loads((real / "package.json").read_text(encoding="utf-8"))["version"]
        self.write_registry()
        inst.write_json_atomic(self.settings, {"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "python ~/.claude/hooks/ollama_healthcheck.py"}]}
        ]}})
        self.paths = inst.Paths(self.home, node=shutil.which("node") or "node")

    @property
    def settings(self) -> Path:
        return self.home / ".claude" / "settings.json"

    @property
    def registry(self) -> Path:
        return self.home / ".claude" / "plugins" / "installed_plugins.json"

    def write_registry(self, version=None, install_path=None, user_entries=1):
        entries = [{
            "scope": "user",
            "installPath": str(install_path or self.plugin_root),
            "version": version or self.version,
        } for _ in range(user_entries)]
        inst.write_json_atomic(self.registry, {"version": 2, "plugins": {PLUGIN_KEY: entries}})

    def run(self, *argv):
        proc = subprocess.run([sys.executable, str(REPO / ".claude" / "tools" / "semantic_search_idle_install.py"),
                               "--home", str(self.home), *argv],
                              capture_output=True, text=True, encoding="utf-8", timeout=300)
        return proc

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class InstallerProof(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    # -- helpers -----------------------------------------------------------
    def install(self):
        proc = self.fx.run("--install")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def check(self):
        return self.fx.run("--check")

    # -- cases -------------------------------------------------------------
    def test_csharp_assets_are_installed_and_missing_grammar_is_drift(self):
        self.install()
        grammar = self.fx.paths.state_dir / "tree-sitter-c_sharp.wasm"
        self.assertTrue(grammar.exists(), "C# grammar must travel with the installed shim")
        self.assertTrue((self.fx.paths.state_dir / "semantic_search_csharp_hook.mjs").exists())
        grammar.unlink()
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("C# asset missing", proc.stdout)

    def test_upstream_byte_drift_is_reported(self):
        self.install()
        # Replace the junction with a private copy before planting drift.
        real_dist = (active_plugin_root() / "dist")
        os.rmdir(self.fx.plugin_root / "dist")
        shutil.copytree(real_dist, self.fx.plugin_root / "dist")
        scanner = self.fx.plugin_root / "dist" / "scanner.js"
        text = scanner.read_text(encoding="utf-8")
        self.assertIn("EXTENSION_MAP", text)
        scanner.write_text(text.replace("EXTENSION_MAP", "UPSTREAM_EXTENSION_MAP"), encoding="utf-8", newline="\n")
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("C# hook drift: scanner.js", proc.stdout)

    def test_result_format_drift_is_reported_for_each_output_module(self):
        self.install()
        real_dist = active_plugin_root() / "dist"
        os.rmdir(self.fx.plugin_root / "dist")
        shutil.copytree(real_dist, self.fx.plugin_root / "dist")
        for name in ("format.js", "mcp-server.js"):
            with self.subTest(module=name):
                module = self.fx.plugin_root / "dist" / name
                original = module.read_bytes()
                module.write_bytes(original + b"\n// result format drift\n")
                try:
                    proc = self.check()
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn("result format drift", proc.stdout)
                    self.assertIn(name, proc.stdout)
                finally:
                    module.write_bytes(original)

    def test_01_check_on_an_uninstalled_home_reports_it(self):
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not installed", proc.stdout)

    def test_02_install_then_check_is_silent_and_clean(self):
        self.install()
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "", "a healthy check must print nothing at all")

    def test_03_install_rewrites_only_the_plugin_entry(self):
        self.install()
        manifest = json.loads((self.fx.plugin_root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest), ["mcpServers"])
        self.assertEqual(manifest["mcpServers"]["semantic-search"]["command"], "node")
        self.assertEqual(manifest["mcpServers"]["semantic-search"]["args"],
                         [self.fx.paths.proxy.as_posix(), "${CLAUDE_PLUGIN_ROOT}"])
        self.assertIn(self.fx.paths.check_command(),
                      [h["command"] for g in json.loads(self.fx.settings.read_text(encoding="utf-8"))
                       ["hooks"]["SessionStart"] for h in g["hooks"]])

    def test_04_rollback_restores_the_exact_entry(self):
        self.install()
        proc = self.fx.run("--rollback")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.fx.plugin_root / ".mcp.json").read_bytes(), self.fx.stock_bytes,
                         "rollback must restore the pre-install bytes exactly")
        commands = [h["command"] for g in json.loads(self.fx.settings.read_text(encoding="utf-8"))
                    ["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertNotIn(self.fx.paths.check_command(), commands)

    def test_05_a_reverted_plugin_entry_is_detected(self):
        self.install()
        inst.write_json_atomic(self.fx.plugin_root / ".mcp.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("reverted or rewritten", proc.stdout)

    def test_06_a_plugin_version_change_is_detected(self):
        self.install()
        self.fx.write_registry(version="9.9.9")
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("version moved", proc.stdout)

    def test_07_an_active_install_path_change_is_detected(self):
        self.install()
        other = self.fx.tmp / "elsewhere"
        other.mkdir()
        self.fx.write_registry(install_path=other)
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("install path moved", proc.stdout)

    def test_08_a_duplicate_user_registration_is_refused(self):
        self.install()
        self.fx.write_registry(user_entries=2)
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("exactly one user-scope", proc.stdout)

    def test_09_a_rewritten_shim_is_detected(self):
        self.install()
        shim = self.fx.paths.proxy
        shim.write_bytes(shim.read_bytes() + b"\n// planted\n")
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("installed shim content changed", proc.stdout)

    def test_10_a_stale_schema_is_reported_even_when_the_hash_matches(self):
        """The static snapshot must be compared with the LIVE upstream contract, not trusted."""
        self.install()
        shim = self.fx.paths.proxy
        text = shim.read_text(encoding="utf-8")
        mutated = text.replace("Search the codebase using natural language queries.",
                               "Search the codebase with a stale description.")
        self.assertNotEqual(mutated, text, "the planted schema change did not apply")
        shim.write_text(mutated, encoding="utf-8", newline="\n")
        state = inst.read_json(self.fx.paths.state)
        state["proxy_sha256"] = inst.sha256_file(shim)
        inst.write_json_atomic(self.fx.paths.state, state)
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("tool description differs", proc.stdout)

    def test_11_an_extra_user_scope_mcp_registration_is_detected(self):
        self.install()
        inst.write_json_atomic(self.fx.home / ".claude.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("duplicate user-scope MCP registration", proc.stdout)

    def test_12_a_missing_session_start_registration_is_detected(self):
        self.install()
        settings = inst.read_json(self.fx.settings)
        settings["hooks"]["SessionStart"] = [g for g in settings["hooks"]["SessionStart"]
                                             if not any(h["command"] == self.fx.paths.check_command()
                                                        for h in g["hooks"])]
        inst.write_json_atomic(self.fx.settings, settings)
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not registered in settings.json", proc.stdout)

    def test_13_the_installed_checker_warns_through_the_registered_hook_command(self):
        """The exact command string settings.json carries, run as the hook runner would."""
        self.install()
        command = self.fx.paths.check_command()
        env = {**os.environ, "USERPROFILE": str(self.fx.home), "HOME": str(self.fx.home)}
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(proc.stdout, "", "healthy: the SessionStart checker must stay silent")

        inst.write_json_atomic(self.fx.plugin_root / ".mcp.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("reverted or rewritten", proc.stdout)
        self.assertEqual(proc.stdout.count("\n"), 1, f"one warning line, got: {proc.stdout!r}")


class ReinstallProof(unittest.TestCase):
    """A reinstall over an installed manifest must keep rollback pointed at the stock entry."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_17_rollback_after_a_reinstall_restores_the_stock_entry(self):
        for _ in range(2):
            proc = self.fx.run("--install")
            self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.fx.run("--rollback")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.fx.plugin_root / ".mcp.json").read_bytes(), self.fx.stock_bytes,
                         "rollback after a reinstall must restore the pre-install stock bytes")

    def test_18_install_over_an_installed_manifest_with_no_stock_backup_is_refused(self):
        proc = self.fx.run("--install")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for bak in self.fx.paths.backups.iterdir():
            bak.unlink()
        proc = self.fx.run("--install")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no backup holds the stock", proc.stderr)

    def test_19_a_newer_backup_of_an_earlier_shim_is_never_the_rollback_source(self):
        proc = self.fx.run("--install")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        earlier_shim = {"command": "node", "args": ["C:/old-home/.claude/semantic-search-idle/proxy.mjs",
                                                    "${CLAUDE_PLUGIN_ROOT}"]}
        inst.write_json_atomic(self.fx.paths.backups / f"mcp.json.{self.fx.version}.99991231T235959.bak",
                               {"mcpServers": {"semantic-search": earlier_shim}})
        proc = self.fx.run("--install")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.fx.run("--rollback")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.fx.plugin_root / ".mcp.json").read_bytes(), self.fx.stock_bytes,
                         "rollback must restore the stock entry, not an earlier shim's")


class ConfigDirProof(unittest.TestCase):
    """`--config-dir` puts the state dir, settings, plugin registry and user config under that login, and its
    SessionStart command checks that login, not `<home>/.claude`."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.login = self.fx.tmp / "second-login"
        (self.login / "plugins").mkdir(parents=True)
        shutil.copyfile(self.fx.registry, self.login / "plugins" / "installed_plugins.json")
        inst.write_json_atomic(self.login / "settings.json", {"hooks": {"SessionStart": []}})
        self.fx.registry.unlink()

    def command(self):
        return 'python "%s" --check --config-dir "%s"' % (
            (self.login / "semantic-search-idle" / "check.py").as_posix(), self.login.as_posix())

    def test_22_install_and_check_resolve_under_the_config_dir(self):
        proc = self.fx.run("--install", "--config-dir", str(self.login))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((self.login / "semantic-search-idle" / "proxy.mjs").exists())
        self.assertFalse((self.fx.home / ".claude" / "semantic-search-idle").exists(),
                         "nothing may land under <home>/.claude for another login")
        manifest = json.loads((self.fx.plugin_root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["mcpServers"]["semantic-search"]["args"][0],
                         (self.login / "semantic-search-idle" / "proxy.mjs").as_posix())
        commands = [h["command"] for g in json.loads((self.login / "settings.json").read_text(encoding="utf-8"))
                    ["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertEqual(commands, [self.command()])
        self.assertFalse(self.fx.settings.read_text(encoding="utf-8").count("second-login"))
        proc = self.fx.run("--check", "--config-dir", str(self.login))
        self.assertEqual((proc.returncode, proc.stdout), (0, ""), proc.stderr)

    def test_23_the_registered_command_checks_its_own_login(self):
        self.assertEqual(self.fx.run("--install", "--config-dir", str(self.login)).returncode, 0)
        proc = subprocess.run(self.command(), shell=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""), proc.stderr)
        inst.write_json_atomic(self.login / ".claude.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
        proc = subprocess.run(self.command(), shell=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("duplicate user-scope MCP registration", proc.stdout,
                      "a non-default login's user config is <config-dir>/.claude.json")


class ContractParityProof(unittest.TestCase):
    """contract_drift compares the whole contract, so an upstream ADDITION is drift too."""

    SHIM = {
        "serverInfo": {"name": "semantic-search", "version": "2.14.0"},
        "capabilities": {"tools": {}, "logging": {}},
        "tool": {"name": "search", "description": "d", "inputSchema": {"type": "object"}},
    }

    def live(self, **changes):
        live = json.loads(json.dumps(self.SHIM))
        for key, value in changes.items():
            live[key] = value
        return live

    def test_14_equal_contracts_report_nothing(self):
        self.assertEqual(inst.contract_drift(self.SHIM, self.live()), [])

    def test_15_an_upstream_only_capability_is_drift(self):
        live = self.live(capabilities={"tools": {}, "logging": {}, "resources": {}})
        problems = inst.contract_drift(self.SHIM, live)
        self.assertTrue(any(p.startswith("capabilities differs") for p in problems), problems)

    def test_16_an_upstream_only_tool_key_is_drift(self):
        tool = dict(self.SHIM["tool"], annotations={"readOnlyHint": True})
        problems = inst.contract_drift(self.SHIM, self.live(tool=tool))
        self.assertTrue(any("upstream carries" in p for p in problems), problems)

    def test_20_a_changed_value_under_a_shared_tool_key_is_drift(self):
        shim = json.loads(json.dumps(self.SHIM))
        shim["tool"]["annotations"] = {"readOnlyHint": True}
        tool = dict(shim["tool"], annotations={"readOnlyHint": False})
        problems = inst.contract_drift(shim, self.live(tool=tool))
        self.assertTrue(any("tool annotations differs" in p for p in problems), problems)

    def test_21_a_changed_top_level_field_is_drift(self):
        shim = dict(self.SHIM, instructions="use search")
        problems = inst.contract_drift(shim, dict(self.SHIM, instructions="use search first"))
        self.assertTrue(any(p.startswith("instructions differs") for p in problems), problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)
