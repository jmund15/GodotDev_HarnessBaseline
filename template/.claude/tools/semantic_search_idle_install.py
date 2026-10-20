#!/usr/bin/env python3
"""Install, check and roll back the semantic-search idle-unloading frontend shim.

    python3 .claude/tools/semantic_search_idle_install.py --check
    python3 .claude/tools/semantic_search_idle_install.py --install
    python3 .claude/tools/semantic_search_idle_install.py --rollback

`--check` is read-only and silent when healthy: it is the same code the installed
SessionStart checker runs, so drift reaches the user as one warning line and nothing else.

What it installs: a copy of `tools/semantic_search_idle_proxy.mjs` under
`<login>/semantic-search-idle/`, and one rewritten entry — the plugin's own
`mcpServers.semantic-search` — in the ACTIVE user-scope plugin's `.mcp.json`. The plugin
is never patched: its `command` stays `node` and only the script argument changes.

`--config-dir <login>` names the Anthropic login (a `CLAUDE_CONFIG_DIR`; default `<home>/.claude`).
The state dir, `settings.json` and the plugin registry resolve under it, and so does the user config:
`<login>/.claude.json`, except for the default login, whose user config is `<home>/.claude.json`.
The SessionStart command it registers carries `--config-dir` for any login but the default, so each
login's session checks its own install.

Fixture overrides exist so proofs can run against a planted home:
`--home`, `--state-dir`, `--registry`, `--settings`, `--user-config`, `--node`, `--plugin-root`.
"""
from __future__ import annotations

import sys as _cli_usage_sys
from pathlib import Path as _cli_usage_path
_cli_usage_sys.path.insert(0, str(_cli_usage_path(__file__).resolve().parent))
try:
    from cli_usage import usage
except ImportError:
    # The installed checker is a lone copy under ~/.claude/semantic-search-idle, with no cli_usage beside it.
    def usage(doc):
        return doc or ""



import argparse
import datetime
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

PLUGIN_KEY = "semantic-search@dreb"
PROXY_SOURCE = Path(".claude/tools/semantic_search_idle_proxy.mjs")
CHECKER_SOURCE = Path(".claude/tools/semantic_search_idle_install.py")
SHIM_NAME = "proxy.mjs"
CHECKER_NAME = "check.py"
STATE_NAME = "install.json"
BACKUP_DIR = "backup"
MCP_ENTRY = "semantic-search"
PLUGIN_ROOT_ARG = "${CLAUDE_PLUGIN_ROOT}"
STOCK_ENTRY = {"command": "node", "args": [PLUGIN_ROOT_ARG + "/bin/server.js"]}
CHECK_COMMAND = 'python "%s" --check'


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- paths
def same_dir(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


class Paths:
    def __init__(self, home: Path, state_dir: Path | None = None, registry: Path | None = None,
                 settings: Path | None = None, user_config: Path | None = None, node: str | None = None,
                 config_dir: Path | None = None):
        self.home = Path(home)
        self.claude = Path(config_dir) if config_dir else self.home / ".claude"
        self.default_login = same_dir(self.claude, self.home / ".claude")
        self.state_dir = Path(state_dir) if state_dir else self.claude / "semantic-search-idle"
        self.registry = Path(registry) if registry else self.claude / "plugins" / "installed_plugins.json"
        self.settings = Path(settings) if settings else self.claude / "settings.json"
        self.user_config = Path(user_config) if user_config else (
            self.home / ".claude.json" if self.default_login else self.claude / ".claude.json")
        self.node = node or shutil.which("node") or "node"

    @property
    def proxy(self) -> Path:
        return self.state_dir / SHIM_NAME

    @property
    def checker(self) -> Path:
        return self.state_dir / CHECKER_NAME

    @property
    def state(self) -> Path:
        return self.state_dir / STATE_NAME

    @property
    def backups(self) -> Path:
        return self.state_dir / BACKUP_DIR

    def check_command(self) -> str:
        command = CHECK_COMMAND % self.checker.as_posix()
        if self.default_login:
            return command
        return command + ' --config-dir "%s"' % self.claude.as_posix()


class Drift(Exception):
    """One reason the installed state is not the reviewed state."""


# --------------------------------------------------------------------------- io
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json_atomic(path: Path, data) -> None:
    """Whole-file write of a document nothing reads back first."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- registry / manifest
def active_install(registry: Path) -> tuple[Path, str]:
    """The single active USER-scope install of the plugin."""
    if not Path(registry).exists():
        raise Drift(f"plugin registry missing: {registry}")
    data = read_json(registry)
    entries = [e for e in (data.get("plugins") or {}).get(PLUGIN_KEY, []) if e.get("scope") == "user"]
    if len(entries) != 1:
        raise Drift(f"expected exactly one user-scope {PLUGIN_KEY} registration, found {len(entries)}")
    entry = entries[0]
    install_path = entry.get("installPath")
    if not install_path:
        raise Drift(f"the user-scope {PLUGIN_KEY} registration has no installPath")
    return Path(install_path), str(entry.get("version") or "")


def expected_manifest(paths: Paths) -> dict:
    return {
        "mcpServers": {
            MCP_ENTRY: {
                "command": "node",
                "args": [paths.proxy.as_posix(), PLUGIN_ROOT_ARG],
            }
        }
    }


def manifest_entry(manifest: dict):
    return ((manifest.get("mcpServers") or {}).get(MCP_ENTRY))


# --------------------------------------------------------------------------- contract probes
def _read_message(proc, q: "queue.Queue", timeout: float):
    deadline = datetime.datetime.now().timestamp() + timeout
    while True:
        remaining = deadline - datetime.datetime.now().timestamp()
        if remaining <= 0:
            raise Drift("timed out waiting for an MCP reply")
        try:
            msg = q.get(timeout=remaining)
        except queue.Empty:
            raise Drift("timed out waiting for an MCP reply")
        if msg is None:
            raise Drift("MCP server closed stdout before replying")
        if msg.get("id") is not None:
            return msg


def _mcp_contract(argv: list[str], cwd: Path, timeout: float = 120.0) -> dict:
    """initialize + tools/list against a stdio MCP server; returns {"serverInfo","capabilities","tool"}."""
    proc = subprocess.Popen(
        argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
    )
    q: "queue.Queue" = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    q.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            q.put(None)

    threading.Thread(target=pump, daemon=True).start()

    def send(payload: dict):
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "semantic-search-idle-install", "version": "1.0.0"}}})
        init = _read_message(proc, q, timeout)
        if "result" not in init:
            raise Drift(f"initialize failed: {init.get('error')}")
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _read_message(proc, q, timeout)
        if "result" not in listed:
            raise Drift(f"tools/list failed: {listed.get('error')}")
        tools = listed["result"].get("tools") or []
        if len(tools) != 1:
            raise Drift(f"expected exactly one registered tool, found {len(tools)}")
        return {"serverInfo": init["result"].get("serverInfo"),
                "capabilities": init["result"].get("capabilities"),
                "tool": tools[0]}
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.kill()
        proc.wait(timeout=30)


def proxy_contract(paths: Paths, plugin_root: Path) -> dict:
    out = subprocess.run(
        [paths.node, str(paths.proxy), "--print-contract", str(plugin_root)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if out.returncode != 0:
        raise Drift(f"installed shim failed to print its contract: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout)


def stock_contract(paths: Paths, plugin_root: Path) -> dict:
    return _mcp_contract([paths.node, str(Path(plugin_root) / "bin" / "server.js")], Path(plugin_root))


def contract_drift(installed: dict, live: dict) -> list[str]:
    """Every field of the contract and of its tool, compared by value."""
    problems = []
    for key in sorted((set(installed) | set(live)) - {"tool"}):
        if installed.get(key) != live.get(key):
            problems.append(f"{key} differs: shim {installed.get(key)!r} vs upstream {live.get(key)!r}")
    shim_tool, live_tool = installed.get("tool") or {}, live.get("tool") or {}
    for key in sorted(set(shim_tool) & set(live_tool)):
        if shim_tool[key] != live_tool[key]:
            problems.append(f"tool {key} differs from the pinned upstream snapshot")
    extra = set(shim_tool) - set(live_tool)
    if extra:
        problems.append(f"tool carries properties upstream does not: {sorted(extra)}")
    missing = set(live_tool) - set(shim_tool)
    if missing:
        problems.append(f"upstream carries tool properties the shim does not: {sorted(missing)}")
    return problems


# --------------------------------------------------------------------------- checks
def check(paths: Paths, plugin_root: Path | None = None) -> list[str]:
    """[] when healthy; otherwise one line per drift reason."""
    try:
        install_path, version = active_install(paths.registry)
    except Drift as exc:
        return [str(exc)]
    if plugin_root is not None:
        install_path = Path(plugin_root)

    problems: list[str] = []

    if not paths.state.exists():
        return [f"idle unload is not installed (no {paths.state})"]
    state = read_json(paths.state)

    if plugin_root is None:
        if version != state.get("version"):
            problems.append(f"plugin version moved: registry {version}, installed shim pinned {state.get('version')}")
        if Path(state.get("install_path", "")) != install_path:
            problems.append(f"active install path moved: {install_path}, shim pinned {state.get('install_path')}")

    if not paths.proxy.exists():
        problems.append(f"installed shim missing: {paths.proxy}")
    elif sha256_file(paths.proxy) != state.get("proxy_sha256"):
        problems.append(f"installed shim content changed: {paths.proxy}")

    if not paths.checker.exists():
        problems.append(f"installed checker missing: {paths.checker}")
    elif sha256_file(paths.checker) != state.get("checker_sha256"):
        problems.append(f"installed checker content changed: {paths.checker}")

    manifest_path = Path(state.get("manifest", install_path / ".mcp.json"))
    if not manifest_path.exists():
        problems.append(f"plugin manifest missing: {manifest_path}")
    else:
        entry = manifest_entry(read_json(manifest_path))
        if entry != expected_manifest(paths)["mcpServers"][MCP_ENTRY]:
            problems.append(f"{manifest_path}: {MCP_ENTRY} entry reverted or rewritten: {entry}")

    settings = paths.settings
    if not settings.exists():
        problems.append(f"settings missing, the SessionStart checker is not registered: {settings}")
    else:
        groups = (read_json(settings).get("hooks") or {}).get("SessionStart") or []
        commands = [h.get("command") for g in groups for h in (g.get("hooks") or [])]
        if paths.check_command() not in commands:
            problems.append("the SessionStart checker is not registered in settings.json")

    if not (install_path / "dist" / "mcp-server.js").exists() or not (install_path / "node_modules").exists():
        problems.append(f"backend not ready under {install_path}: run the plugin's install once")

    if paths.user_config.exists():
        servers = (read_json(paths.user_config).get("mcpServers") or {})
        if MCP_ENTRY in servers:
            problems.append(f"duplicate user-scope MCP registration named {MCP_ENTRY} in {paths.user_config}")

    if problems:
        return problems

    try:
        shim = proxy_contract(paths, install_path)
        live = stock_contract(paths, install_path)
    except Drift as exc:
        return [str(exc)]
    return contract_drift(shim, live)


# --------------------------------------------------------------------------- install / rollback
def install(paths: Paths, plugin_root: Path | None = None) -> dict:
    """Back up, write the shim and the single manifest entry, register the checker."""
    install_path, version = active_install(paths.registry)
    if plugin_root is not None:
        install_path = Path(plugin_root)

    root = repo_root()
    source_proxy = root / PROXY_SOURCE
    source_checker = root / CHECKER_SOURCE
    for needed in (source_proxy, source_checker):
        if not needed.exists():
            raise Drift(f"installer source missing: {needed}")

    manifest_path = install_path / ".mcp.json"
    if not manifest_path.exists():
        raise Drift(f"plugin manifest missing: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if MCP_ENTRY not in (manifest.get("mcpServers") or {}):
        raise Drift(f"{manifest_path} has no mcpServers.{MCP_ENTRY} entry to preserve")

    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.backups.mkdir(parents=True, exist_ok=True)
    shim_entry = expected_manifest(paths)["mcpServers"][MCP_ENTRY]
    if manifest_entry(manifest) != shim_entry:
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        write_bytes_atomic(paths.backups / f"mcp.json.{version}.{stamp}.bak", manifest_bytes)
    backup = stock_backup(paths, version)
    stock_bytes = backup.read_bytes()

    write_bytes_atomic(paths.proxy, source_proxy.read_bytes())
    write_bytes_atomic(paths.checker, source_checker.read_bytes())

    rewritten = json.loads(manifest_bytes.decode("utf-8"))
    rewritten.setdefault("mcpServers", {})[MCP_ENTRY] = expected_manifest(paths)["mcpServers"][MCP_ENTRY]
    write_json_atomic(manifest_path, rewritten)

    state = {
        "version": version,
        "install_path": str(install_path),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_bytes(stock_bytes),
        "backup": str(backup),
        "proxy_sha256": sha256_file(paths.proxy),
        "checker_sha256": sha256_file(paths.checker),
        "installed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    write_json_atomic(paths.state, state)

    register_checker(paths)

    problems = check(paths, plugin_root=plugin_root)
    if problems:
        raise Drift("install did not verify: " + "; ".join(problems))
    return {"installed": str(paths.proxy), "manifest": str(manifest_path),
            "backup": str(backup), "version": version}


def stock_backup(paths: Paths, version: str) -> Path:
    """The rollback source: the newest backup for `version` whose entry is the stock server's.

    A reinstall over an installed manifest, this shim's or an earlier one at another path, must
    not make proxied bytes the rollback target.
    """
    candidates = sorted(paths.backups.glob(f"mcp.json.{version}.*.bak"), reverse=True)
    for candidate in candidates:
        try:
            entry = manifest_entry(json.loads(candidate.read_text(encoding="utf-8")))
        except ValueError:
            continue
        if entry == STOCK_ENTRY:
            return candidate
    raise Drift(f"no backup holds the stock {MCP_ENTRY} entry for {version} under {paths.backups}; "
                "restore the plugin's stock manifest (reinstall the plugin), then --install")


def register_checker(paths: Paths) -> None:
    settings = read_json(paths.settings) if paths.settings.exists() else {}
    hooks = settings.setdefault("hooks", {})
    groups = hooks.setdefault("SessionStart", [])
    commands = [h.get("command") for g in groups for h in (g.get("hooks") or [])]
    if paths.check_command() in commands:
        return
    groups.append({"hooks": [{"type": "command", "command": paths.check_command()}]})
    write_json_atomic(paths.settings, settings)


def unregister_checker(paths: Paths) -> None:
    if not paths.settings.exists():
        return
    settings = read_json(paths.settings)
    groups = (settings.get("hooks") or {}).get("SessionStart")
    if not groups:
        return
    kept = []
    for group in groups:
        inner = [h for h in (group.get("hooks") or []) if h.get("command") != paths.check_command()]
        if inner or not (group.get("hooks") or []):
            kept.append({**group, "hooks": inner})
    settings["hooks"]["SessionStart"] = kept
    write_json_atomic(paths.settings, settings)


def rollback(paths: Paths) -> dict:
    """Restore the exact backed-up manifest entry and drop this checker's registration."""
    if not paths.state.exists():
        raise Drift(f"nothing to roll back: no {paths.state}")
    state = read_json(paths.state)
    backup = Path(state.get("backup", ""))
    manifest_path = Path(state.get("manifest", ""))
    if not backup.exists():
        raise Drift(f"backup missing: {backup}")
    restored = backup.read_bytes()
    write_bytes_atomic(manifest_path, restored)
    if sha256_bytes(restored) != state.get("manifest_sha256"):
        raise Drift("restored manifest does not hash to the recorded pre-install bytes")
    unregister_checker(paths)
    return {"restored": str(manifest_path), "from": str(backup),
            "sha256": sha256_bytes(restored)}


# --------------------------------------------------------------------------- cli
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=usage(__doc__),
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="read-only drift check (default)")
    action.add_argument("--install", action="store_true", help="install the shim and register the checker")
    action.add_argument("--rollback", action="store_true", help="restore the backed-up manifest entry")
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--config-dir", help="the login dir to act on (default: <home>/.claude)")
    parser.add_argument("--state-dir")
    parser.add_argument("--registry")
    parser.add_argument("--settings")
    parser.add_argument("--user-config")
    parser.add_argument("--node")
    parser.add_argument("--plugin-root", help="override the registry's active install path (fixtures only)")
    args = parser.parse_args(argv)

    paths = Paths(Path(args.home), args.state_dir, args.registry, args.settings, args.user_config, args.node,
                  Path(args.config_dir) if args.config_dir else None)
    plugin_root = Path(args.plugin_root) if args.plugin_root else None

    try:
        if args.install:
            receipt = install(paths, plugin_root)
            print(json.dumps({"installed": receipt}, indent=2))
            return 0
        if args.rollback:
            receipt = rollback(paths)
            print(json.dumps({"rollback": receipt}, indent=2))
            return 0
        problems = check(paths, plugin_root)
    except Drift as exc:
        print(f"semantic-search idle unload: {exc}", file=sys.stderr)
        return 1

    if problems:
        for line in problems:
            print(f"semantic-search idle unload: {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
