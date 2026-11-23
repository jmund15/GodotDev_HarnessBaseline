#!/usr/bin/env python3
"""Proof for tools/semantic_search_idle_install.py: install, drift-check and roll back the shim.

Two fixtures. The planted plugin (`fx`) is a bare plugin root under a planted home whose three contract probes
(C# hook, shim, stock server) are stubbed to agree, so each drift reason and each install step is a decision case
that spawns nothing, with `main()` run in-process. The real plugin (`real_fx`) is a junction farm over the REAL
installed plugin, so the probes run node for real and no live user file is read or written; only the cases whose
subject is a probe or a registered command string use it, and they skip where no plugin is installed.

Run: python3 -m pytest .claude/tests/test_semantic_search_idle_install.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / ".claude" / "tools"))

import semantic_search_idle_install as inst  # noqa: E402

REGISTRY_SOURCE = (Path(os.environ.get("HARNESS_CALLER_HOME") or Path.home())
                   / ".claude" / "plugins" / "installed_plugins.json")
needs_plugin = pytest.mark.skipif(not REGISTRY_SOURCE.exists() or os.name != "nt",
                                  reason="no semantic-search plugin is installed on this machine")
PLUGIN_KEY = "semantic-search@dreb"
STOCK_ENTRY = {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/bin/server.js"]}
CONTRACT = {
    "serverInfo": {"name": "semantic-search", "version": "2.14.0"},
    "capabilities": {"tools": {}, "logging": {}},
    "tool": {"name": "search", "description": "d", "inputSchema": {"type": "object"}},
}


def active_plugin_root() -> Path:
    data = json.loads(REGISTRY_SOURCE.read_text(encoding="utf-8"))
    entries = [e for e in data["plugins"][PLUGIN_KEY] if e.get("scope") == "user"]
    assert len(entries) == 1, "expected exactly one user-scope registration"
    return built_copy(Path(entries[0]["installPath"]))


def built_copy(registered: Path) -> Path:
    """The registered copy when its backend is built, else the newest built version beside it: a plugin
    update leaves the registered copy unbuilt until its first launch, and the fixture needs a real backend."""
    def built(root: Path) -> bool:
        modules = root / "node_modules"
        return (root / "dist" / "mcp-server.js").exists() and modules.is_dir() and any(modules.iterdir())

    def version(root: Path) -> tuple:
        return tuple(int(part) if part.isdigit() else -1 for part in root.name.split("."))

    if built(registered) or not registered.parent.is_dir():
        return registered
    siblings = sorted((p for p in registered.parent.iterdir() if p.is_dir() and built(p)), key=version, reverse=True)
    return siblings[0] if siblings else registered


def junction(link: Path, target: Path) -> None:
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"mklink failed for {link}: {out.stdout} {out.stderr}")


class Fixture:
    """A planted home plus a plugin root. `real` junctions the installed plugin in; otherwise the root is a
    bare built-looking plugin and the probes are the caller's to stub."""

    def __init__(self, real: bool, capsys=None):
        self.tmp = Path(tempfile.mkdtemp(prefix="idle-install-proof-"))
        self.home = self.tmp / "home"
        (self.home / ".claude" / "plugins").mkdir(parents=True)
        self.plugin_root = self.tmp / "pluginroot"
        self.plugin_root.mkdir()
        if real:
            source = active_plugin_root()
            for name in ("bin", "dist", "node_modules", "grammars"):
                if (source / name).exists():
                    junction(self.plugin_root / name, source / name)
            package = (source / "package.json").read_bytes()
        else:
            (self.plugin_root / "dist").mkdir()
            (self.plugin_root / "dist" / "mcp-server.js").write_text("", encoding="utf-8")
            (self.plugin_root / "node_modules").mkdir()
            (self.plugin_root / "node_modules" / "x").write_text("", encoding="utf-8")
            package = b'{"version": "2.14.0"}'
        (self.plugin_root / "package.json").write_bytes(package)
        # The live manifest may already carry the installed shim, so the fixture plants the stock entry.
        inst.write_json_atomic(self.plugin_root / ".mcp.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
        self.stock_bytes = (self.plugin_root / ".mcp.json").read_bytes()
        self.version = json.loads(package)["version"]
        self.write_registry()
        inst.write_json_atomic(self.settings, {"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "python ~/.claude/hooks/ollama_healthcheck.py"}]}
        ]}})
        self.paths = inst.Paths(self.home, node=shutil.which("node") or "node")
        self.real = real
        self.capsys = capsys

    @property
    def settings(self) -> Path:
        return self.home / ".claude" / "settings.json"

    @property
    def registry(self) -> Path:
        return self.home / ".claude" / "plugins" / "installed_plugins.json"

    def write_registry(self, version=None, install_path=None, user_entries=1):
        entries = [{"scope": "user", "installPath": str(install_path or self.plugin_root),
                    "version": version or self.version} for _ in range(user_entries)]
        inst.write_json_atomic(self.registry, {"version": 2, "plugins": {PLUGIN_KEY: entries}})

    def run(self, *argv):
        """(returncode, stdout, stderr): a real child for the real plugin, else `main()` in-process."""
        if self.real:
            proc = subprocess.run([sys.executable, str(REPO / ".claude" / "tools" / "semantic_search_idle_install.py"),
                                   "--home", str(self.home), *argv],
                                  capture_output=True, text=True, encoding="utf-8", timeout=300)
            return proc.returncode, proc.stdout, proc.stderr
        code = inst.main(["--home", str(self.home), "--node", "node", *argv])
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err

    def install(self, *argv):
        code, out, err = self.run("--install", *argv)
        assert code == 0, out + err
        return out

    def check(self, *argv):
        return self.run("--check", *argv)

    def session_start_commands(self, settings=None):
        return [h["command"] for g in json.loads((settings or self.settings).read_text(encoding="utf-8"))
                ["hooks"]["SessionStart"] for h in g["hooks"]]

    def second_login(self):
        """A second login dir holding the registry and settings; the default home's registry goes."""
        login = self.tmp / "second-login"
        (login / "plugins").mkdir(parents=True)
        shutil.copyfile(self.registry, login / "plugins" / "installed_plugins.json")
        inst.write_json_atomic(login / "settings.json", {"hooks": {"SessionStart": []}})
        self.registry.unlink()
        return login


def agreeing_probes(monkeypatch):
    monkeypatch.setattr(inst, "csharp_contract", lambda *a, **k: {})
    monkeypatch.setattr(inst, "proxy_contract", lambda *a, **k: json.loads(json.dumps(CONTRACT)))
    monkeypatch.setattr(inst, "stock_contract", lambda *a, **k: json.loads(json.dumps(CONTRACT)))


@pytest.fixture
def fx(monkeypatch, capsys):
    """The planted plugin with agreeing stubbed probes."""
    agreeing_probes(monkeypatch)
    fixture = Fixture(real=False, capsys=capsys)
    yield fixture
    shutil.rmtree(fixture.tmp, ignore_errors=True)


@pytest.fixture
def real_fx():
    fixture = Fixture(real=True)
    yield fixture
    shutil.rmtree(fixture.tmp, ignore_errors=True)


def drift(fixture, *needles, code=1):
    got, out, err = fixture.check()
    assert got == code, out + err
    for needle in needles:
        assert needle in out, out


def config_dir_command(login: Path) -> str:
    return 'python "%s" --check --config-dir "%s"' % (
        (login / "semantic-search-idle" / "check.py").as_posix(), login.as_posix())


# -- decision cases (stubbed probes, in-process) ------------------------------------------------------------------
def test_csharp_assets_are_installed_and_missing_grammar_is_drift(fx):
    fx.install()
    grammar = fx.paths.state_dir / "tree-sitter-c_sharp.wasm"
    assert grammar.exists(), "C# grammar must travel with the installed shim"
    assert (fx.paths.state_dir / "semantic_search_csharp_hook.mjs").exists()
    grammar.unlink()
    drift(fx, "tree-sitter-c_sharp.wasm")


def test_01_check_on_an_uninstalled_home_reports_it(fx):
    drift(fx, "installed")


def test_02_install_then_check_is_silent_and_clean_with_agreeing_probes(fx):
    fx.install()
    assert fx.check()[:2] == (0, ""), "a healthy check must print nothing at all"


def test_03_install_rewrites_only_the_plugin_entry(fx):
    fx.install()
    manifest = json.loads((fx.plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    assert sorted(manifest) == ["mcpServers"]
    assert manifest["mcpServers"]["semantic-search"]["command"] == "node"
    assert manifest["mcpServers"]["semantic-search"]["args"] == [fx.paths.proxy.as_posix(), "${CLAUDE_PLUGIN_ROOT}"]
    assert fx.paths.check_command() in fx.session_start_commands()


def test_04_rollback_restores_the_exact_entry(fx):
    fx.install()
    code, out, err = fx.run("--rollback")
    assert code == 0, err
    assert (fx.plugin_root / ".mcp.json").read_bytes() == fx.stock_bytes, "rollback must restore the pre-install bytes"
    assert fx.paths.check_command() not in fx.session_start_commands()


def test_05_a_reverted_plugin_entry_is_detected(fx):
    fx.install()
    inst.write_json_atomic(fx.plugin_root / ".mcp.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
    drift(fx, "reverted")


def test_06_a_plugin_version_change_is_detected(fx):
    fx.install()
    fx.write_registry(version="9.9.9")
    drift(fx, "version")


def test_07_an_active_install_path_change_is_detected(fx):
    fx.install()
    other = fx.tmp / "elsewhere"
    other.mkdir()
    fx.write_registry(install_path=other)
    drift(fx, "install path")


def test_08_a_duplicate_user_registration_is_refused(fx):
    fx.install()
    fx.write_registry(user_entries=2)
    drift(fx, "user-scope")


def test_09_a_rewritten_shim_is_detected(fx):
    fx.install()
    fx.paths.proxy.write_bytes(fx.paths.proxy.read_bytes() + b"\n// planted\n")
    drift(fx, "changed")


def test_24_an_install_older_than_the_repo_source_is_drift(fx):
    """A landed hook change reaches no backend until --install; the repo's check names the stale copy."""
    fx.install()
    hook = fx.paths.state_dir / "semantic_search_csharp_hook.mjs"
    hook.write_bytes(hook.read_bytes() + b"\n// an older release\n")
    state = json.loads(fx.paths.state.read_text(encoding="utf-8"))
    state["csharp_sha256"][hook.name] = inst.sha256_file(hook)
    inst.write_json_atomic(fx.paths.state, state)
    drift(fx, hook.name)


def test_10_the_installed_shim_is_compared_with_the_live_upstream_contract(fx, monkeypatch):
    """Decision half: a shim contract that differs from the live one is reported by field. The real half is
    test_10_real_a_stale_schema_is_reported_even_when_the_hash_matches."""
    fx.install()
    stale = json.loads(json.dumps(CONTRACT))
    stale["tool"]["description"] = "a stale description"
    monkeypatch.setattr(inst, "proxy_contract", lambda *a, **k: stale)
    drift(fx, "description")


def test_11_an_extra_user_scope_mcp_registration_is_detected(fx):
    fx.install()
    inst.write_json_atomic(fx.home / ".claude.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
    drift(fx, "duplicate")


def test_12_a_missing_session_start_registration_is_detected(fx):
    fx.install()
    settings = inst.read_json(fx.settings)
    settings["hooks"]["SessionStart"] = [g for g in settings["hooks"]["SessionStart"]
                                         if not any(h["command"] == fx.paths.check_command() for h in g["hooks"])]
    inst.write_json_atomic(fx.settings, settings)
    drift(fx, "settings.json")


def test_a_failing_probe_is_reported_as_the_check_result(fx, monkeypatch):
    fx.install()

    def broken(*a, **k):
        raise inst.Drift("C# hook probe failed: planted")
    monkeypatch.setattr(inst, "csharp_contract", broken)
    drift(fx, "C# hook probe failed: planted")


def test_17_rollback_after_a_reinstall_restores_the_stock_entry(fx):
    fx.install()
    fx.install()
    code, out, err = fx.run("--rollback")
    assert code == 0, err
    assert (fx.plugin_root / ".mcp.json").read_bytes() == fx.stock_bytes, \
        "rollback after a reinstall must restore the pre-install stock bytes"


def test_18_install_over_an_installed_manifest_with_no_stock_backup_is_refused(fx):
    fx.install()
    for bak in fx.paths.backups.iterdir():
        bak.unlink()
    code, out, err = fx.run("--install")
    assert code == 1
    assert "backup" in err, err


def test_19_a_newer_backup_of_an_earlier_shim_is_never_the_rollback_source(fx):
    fx.install()
    earlier_shim = {"command": "node", "args": ["C:/old-home/.claude/semantic-search-idle/proxy.mjs",
                                                "${CLAUDE_PLUGIN_ROOT}"]}
    inst.write_json_atomic(fx.paths.backups / f"mcp.json.{fx.version}.99991231T235959.bak",
                           {"mcpServers": {"semantic-search": earlier_shim}})
    fx.install()
    code, out, err = fx.run("--rollback")
    assert code == 0, err
    assert (fx.plugin_root / ".mcp.json").read_bytes() == fx.stock_bytes, \
        "rollback must restore the stock entry, not an earlier shim's"


def test_22_install_and_check_resolve_under_the_config_dir(fx):
    """`--config-dir` puts the state dir, settings, plugin registry and user config under that login, and its
    SessionStart command checks that login, not `<home>/.claude`."""
    login = fx.second_login()
    fx.install("--config-dir", str(login))
    assert (login / "semantic-search-idle" / "proxy.mjs").exists()
    assert not (fx.home / ".claude" / "semantic-search-idle").exists(), \
        "nothing may land under <home>/.claude for another login"
    manifest = json.loads((fx.plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    assert manifest["mcpServers"]["semantic-search"]["args"][0] == \
        (login / "semantic-search-idle" / "proxy.mjs").as_posix()
    assert fx.session_start_commands(login / "settings.json") == [config_dir_command(login)]
    assert not fx.settings.read_text(encoding="utf-8").count("second-login")
    assert fx.check("--config-dir", str(login))[:2] == (0, "")


# -- contract comparison (pure) -----------------------------------------------------------------------------------
def live(**changes):
    out = json.loads(json.dumps(CONTRACT))
    out.update(changes)
    return out


def test_14_equal_contracts_report_nothing():
    assert inst.contract_drift(CONTRACT, live()) == []


def test_15_an_upstream_only_capability_is_drift():
    problems = inst.contract_drift(CONTRACT, live(capabilities={"tools": {}, "logging": {}, "resources": {}}))
    assert any("capabilities" in p for p in problems), problems


def test_16_an_upstream_only_tool_key_is_drift():
    tool = dict(CONTRACT["tool"], annotations={"readOnlyHint": True})
    assert inst.contract_drift(CONTRACT, live(tool=tool))


def test_20_a_changed_value_under_a_shared_tool_key_is_drift():
    shim = json.loads(json.dumps(CONTRACT))
    shim["tool"]["annotations"] = {"readOnlyHint": True}
    tool = dict(shim["tool"], annotations={"readOnlyHint": False})
    assert any("annotations" in p for p in inst.contract_drift(shim, live(tool=tool)))


def test_21_a_changed_top_level_field_is_drift():
    shim = dict(CONTRACT, instructions="use search")
    assert any("instructions" in p for p in inst.contract_drift(shim, dict(CONTRACT, instructions="use search first")))


# -- backend repair (a stub tsc; one node spawn per case that builds) ---------------------------------------------
STUB_TSC = ("const fs = require('fs'); fs.mkdirSync('dist', {recursive: true}); "
            "fs.writeFileSync('dist/mcp-server.js', '');\n")


def plant_version(cache: Path, name: str, deps: dict, built: bool) -> Path:
    root = cache / name
    (root / "node_modules").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"version": name, "dependencies": deps}), encoding="utf-8")
    (root / "tsconfig.build.json").write_text("{}", encoding="utf-8")
    if built:
        tsc = root / "node_modules" / "typescript" / "bin" / "tsc"
        tsc.parent.mkdir(parents=True)
        tsc.write_text(STUB_TSC, encoding="utf-8")
        (root / "dist").mkdir()
        (root / "dist" / "mcp-server.js").write_text("", encoding="utf-8")
    return root


def test_same_packages_builds_from_the_built_sibling(tmp_path):
    plant_version(tmp_path, "1.0.0", {"sdk": "^1"}, built=True)
    unbuilt = plant_version(tmp_path, "1.1.0", {"sdk": "^1"}, built=False)
    used = inst.repair_backend(unbuilt, shutil.which("node") or "node")
    assert Path(used).name == "1.0.0"
    assert inst.backend_ready(unbuilt)


def test_different_packages_leave_it_unbuilt(tmp_path):
    plant_version(tmp_path, "1.0.0", {"sdk": "^1"}, built=True)
    unbuilt = plant_version(tmp_path, "1.1.0", {"sdk": "^2"}, built=False)
    assert inst.repair_backend(unbuilt, shutil.which("node") or "node") is None
    assert not inst.backend_ready(unbuilt)


def test_a_ready_install_is_left_alone(tmp_path):
    built = plant_version(tmp_path, "1.0.0", {"sdk": "^1"}, built=True)
    assert inst.repair_backend(built, shutil.which("node") or "node") is None


# -- real probes: node runs against the installed plugin (skipped where none is installed) ------------------------
@needs_plugin
def test_02_real_install_then_check_is_silent_and_clean(real_fx):
    real_fx.install()
    assert real_fx.check()[:2] == (0, "")


@needs_plugin
def test_10_real_a_stale_schema_is_reported_even_when_the_hash_matches(real_fx):
    """The static snapshot must be compared with the LIVE upstream contract, not trusted."""
    real_fx.install()
    shim = real_fx.paths.proxy
    text = shim.read_text(encoding="utf-8")
    mutated = text.replace("Search the codebase using natural language queries.",
                           "Search the codebase with a stale description.")
    assert mutated != text, "the planted schema change did not apply"
    shim.write_text(mutated, encoding="utf-8", newline="\n")
    state = inst.read_json(real_fx.paths.state)
    state["proxy_sha256"] = inst.sha256_file(shim)
    inst.write_json_atomic(real_fx.paths.state, state)
    drift(real_fx, "description")


def private_dist(real_fx):
    """Replace the dist junction with a private copy so a case may plant drift in it."""
    os.rmdir(real_fx.plugin_root / "dist")
    shutil.copytree(active_plugin_root() / "dist", real_fx.plugin_root / "dist")
    return real_fx.plugin_root / "dist"


@needs_plugin
def test_upstream_byte_drift_is_reported(real_fx):
    real_fx.install()
    scanner = private_dist(real_fx) / "scanner.js"
    text = scanner.read_text(encoding="utf-8")
    assert "EXTENSION_MAP" in text
    scanner.write_text(text.replace("EXTENSION_MAP", "UPSTREAM_EXTENSION_MAP"), encoding="utf-8", newline="\n")
    drift(real_fx, "scanner.js")


@needs_plugin
@pytest.mark.parametrize("name", ["format.js", "mcp-server.js"])
def test_result_format_drift_is_reported_for_each_output_module(real_fx, name):
    real_fx.install()
    module = private_dist(real_fx) / name
    module.write_bytes(module.read_bytes() + b"\n// result format drift\n")
    code, out, err = real_fx.check()
    assert code == 1, out + err
    for needle in (name, "RESULT_MODULE_HASHES", "--install"):
        assert needle in out, out
    assert out.count("\n") == 1, "one line, not the shim's wrapper plus a cut copy"


@needs_plugin
def test_13_the_installed_checker_warns_through_the_registered_hook_command(real_fx):
    """The exact command string settings.json carries, run as the hook runner would."""
    real_fx.install()
    command = real_fx.paths.check_command()
    env = {**os.environ, "USERPROFILE": str(real_fx.home), "HOME": str(real_fx.home)}
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert proc.stdout == "", "healthy: the SessionStart checker must stay silent"
    inst.write_json_atomic(real_fx.plugin_root / ".mcp.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert proc.returncode == 1
    assert "reverted" in proc.stdout, proc.stdout
    assert proc.stdout.count("\n") == 1, f"one warning line, got: {proc.stdout!r}"


@needs_plugin
def test_23_the_registered_command_checks_its_own_login(real_fx):
    login = real_fx.second_login()
    real_fx.install("--config-dir", str(login))
    command = config_dir_command(login)
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert (proc.returncode, proc.stdout) == (0, ""), proc.stderr
    inst.write_json_atomic(login / ".claude.json", {"mcpServers": {"semantic-search": STOCK_ENTRY}})
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 1
    assert "duplicate user-scope MCP registration" in proc.stdout, \
        "a non-default login's user config is <config-dir>/.claude.json"
