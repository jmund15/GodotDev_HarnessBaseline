#!/usr/bin/env python3
"""Proof for scripts/sync_plan_config.py: every non-default plan's configDir follows its route's.

Both login dirs and the registry are planted; the real ~/.claude and ~/.claude-secondary are never
read. The script runs in-process with `open` wrapped, so a read of a credential file is observed.

The plugins stage runs against planted login dirs with fake `claude`, `npm` and `node` first on PATH:
each records its argv and CLAUDE_CONFIG_DIR and answers from a planted script, so no proof reaches the
network, a real plugin cache or a real CLI. The plugin guard reads a planted `plugins` table
and a planted process table.

    python3 .claude/tests/test_sync_plan_config.py
"""
import builtins
import contextlib
import copy
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "sync_plan_config.py"
sys.path.insert(0, str(HERE.parent / "tools"))
import model_registry  # noqa: E402

PRIMARY_SETTINGS = {
    "model": "planted-model",
    "autoMode": {"allow": ["planted rule"]},
    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]},
    "permissions": {"allow": ["Read", "Bash(git *)"], "deny": ["Bash(rm -rf *)"]},
    "autoMemoryDirectory": "primary-memory",
    "mcpServers": {"primary-server": {"command": "p"}},
    "effortLevel": "high",
}
SECONDARY_SETTINGS = {
    "permissions": {"allow": ["WebFetch", "Read"], "deny": ["Bash(curl *)"]},
    "autoMemoryDirectory": "secondary-memory",
    "mcpServers": {"secondary-server": {"command": "s"}},
    "effortLevel": "low",
}


def plant():
    root = Path(tempfile.mkdtemp(prefix="sync_plan_config_"))
    primary, secondary = root / "primary", root / "secondary"
    primary.mkdir()
    secondary.mkdir()
    (primary / "CLAUDE.md").write_text("primary instructions\n", encoding="utf-8")
    (primary / "ai_worker_model_guide.md").write_text("primary guide\n", encoding="utf-8")
    (primary / "settings.json").write_text(json.dumps(PRIMARY_SETTINGS, indent=2), encoding="utf-8")
    (primary / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "PRIMARY"}}', encoding="utf-8")
    # An earlier sync's copy: byte-identical to the route's CLAUDE.md, which every project under the
    # home dir already loads as its ancestor .claude/CLAUDE.md.
    (secondary / "CLAUDE.md").write_text("primary instructions\n", encoding="utf-8")
    (secondary / "ai_worker_model_guide.md").write_text("stale guide\n", encoding="utf-8")
    (secondary / "settings.json").write_text(json.dumps(SECONDARY_SETTINGS, indent=2), encoding="utf-8")
    (secondary / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "SECOND"}}', encoding="utf-8")
    (secondary / ".claude.json").write_text('{"planted": true}', encoding="utf-8")
    data = copy.deepcopy(model_registry.load())
    data["transports"]["anthropic"]["configDir"] = str(primary)
    data["transports"]["anthropic-secondary"]["configDir"] = str(secondary)
    # The Console-credit plans authenticate with a key, so their configDirs are not logins and are
    # never created by one. They stay under the planted root: the shipped rows name the real HOME,
    # and a sync of a key plan would create them there.
    data["transports"]["anthropic-console"]["configDir"] = str(root / "console-primary")
    data["transports"]["anthropic-console-secondary"]["configDir"] = str(root / "console-secondary")
    registry = root / "external_models.json"
    registry.write_text(json.dumps(data), encoding="utf-8")
    return root, primary, secondary, registry


def load_script():
    spec = importlib.util.spec_from_file_location("sync_plan_config_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(registry, *argv, table=None, plugins=None, replace_wait=None):
    # `plugins` is the planted `plugins` table; unset plants none, so no run reads the machine's real plugin.
    """(exit code, stdout, every path opened) for one in-process run, over a planted process table.
    `replace_wait` shortens the seconds the script's replace waits out a denied rename (the shipped wait is 10 s per
    file), for a case whose replace stays denied."""
    module = load_script()
    if replace_wait is not None:
        shipped = module.replace_path
        module.replace_path = lambda source, target: shipped(source, target, replace_wait)
    planted = Path(registry).parent / "planted-claude" / "skills" / "project_subsystems"
    planted.mkdir(parents=True, exist_ok=True)
    (planted / "adaptation.json").write_text(json.dumps({"plugins": plugins or {}}), encoding="utf-8")
    module.CLAUDE_DIR = planted.parents[1]
    module.process_table = lambda: dict(table or {})
    opened = []
    real_open = builtins.open

    def spy(file, *args, **kwargs):
        opened.append(os.fspath(file) if not isinstance(file, int) else "")
        return real_open(file, *args, **kwargs)

    old = os.environ.get("HARNESS_MODEL_REGISTRY")
    os.environ["HARNESS_MODEL_REGISTRY"] = str(registry)
    model_registry._cache.clear()
    out = io.StringIO()
    builtins.open = spy
    try:
        with contextlib.redirect_stdout(out):
            code = module.main(list(argv))
    finally:
        builtins.open = real_open
        if old is None:
            os.environ.pop("HARNESS_MODEL_REGISTRY", None)
        else:
            os.environ["HARNESS_MODEL_REGISTRY"] = old
        model_registry._cache.clear()
    return code, out.getvalue(), opened


def snapshot(directory):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(directory.iterdir()) if p.is_file()}


def main():
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), detail))

    if not SCRIPT.is_file():
        case("sync_plan_config.py exists", False)
        return report(cases)

    root, primary, secondary, registry = plant()
    before = snapshot(secondary)
    code, out, opened = run(registry, "--check")
    case("--check exits 1 on drift and names each file and settings key it would change",
         code == 1 and "CLAUDE.md" in out and "ai_worker_model_guide.md" in out
         and "settings.json" in out and "autoMode" in out and "hooks" in out, repr((code, out)))
    case("--check writes nothing", snapshot(secondary) == before)
    case("--check never opens a credential file",
         not any(p.endswith(".credentials.json") for p in opened), repr(opened))

    code, out, opened = run(registry)
    settings = json.loads((secondary / "settings.json").read_text(encoding="utf-8"))
    case("a sync exits 0, copies ai_worker_model_guide.md whole and removes the identical CLAUDE.md copy",
         code == 0 and not (secondary / "CLAUDE.md").exists()
         and (secondary / "ai_worker_model_guide.md").read_text(encoding="utf-8") == "primary guide\n",
         repr((code, out)))
    case("settings.json takes the primary's values",
         settings.get("autoMode") == PRIMARY_SETTINGS["autoMode"] and settings.get("hooks") == PRIMARY_SETTINGS["hooks"]
         and settings.get("model") == "planted-model"
         and ((settings.get("permissions") or {}).get("deny") or [])[:1] == ["Bash(rm -rf *)"], repr(settings))
    case("the per-account keys survive from the destination",
         settings.get("autoMemoryDirectory") == "secondary-memory"
         and settings.get("mcpServers") == SECONDARY_SETTINGS["mcpServers"]
         and settings.get("effortLevel") == "low", repr(settings))
    case("with no snapshot yet, permissions.allow is the union of both",
         sorted((settings.get("permissions") or {}).get("allow") or []) == ["Bash(git *)", "Read", "WebFetch"],
         repr(settings.get("permissions")))
    case("with no snapshot yet, a destination-only deny survives the sync",
         sorted((settings.get("permissions") or {}).get("deny") or []) == ["Bash(curl *)", "Bash(rm -rf *)"],
         repr(settings.get("permissions")))
    case("the first write saves each prior file as <name>.pre-sync",
         json.loads((secondary / "settings.json.pre-sync").read_text(encoding="utf-8")) == SECONDARY_SETTINGS
         and (secondary / "ai_worker_model_guide.md.pre-sync").read_text(encoding="utf-8") == "stale guide\n")
    case("credentials and .claude.json are never read or written",
         not any(p.endswith((".credentials.json", ".claude.json")) for p in opened)
         and (secondary / ".credentials.json").read_text(encoding="utf-8") == '{"claudeAiOauth": {"accessToken": "SECOND"}}'
         and (primary / ".credentials.json").read_text(encoding="utf-8") == '{"claudeAiOauth": {"accessToken": "PRIMARY"}}'
         and (secondary / ".claude.json").read_text(encoding="utf-8") == '{"planted": true}', repr(opened))

    code, out, _ = run(registry, "--check")
    case("--check exits 0 after one sync", code == 0, repr((code, out)))
    synced = snapshot(secondary)
    time.sleep(0.05)
    code, out, _ = run(registry)
    case("identical files are left untouched", code == 0 and snapshot(secondary) == synced, repr(out))

    # A key plan's configDir is not a login (nothing ever logs into it), so the sync creates it and
    # copies the route's files; a login plan's missing configDir is still the owner's fault to fix.
    key_registry = root / "external_models_key.json"
    key_data = json.loads(registry.read_text(encoding="utf-8"))
    fresh = root / "console-fresh"
    missing_login = root / "login-fresh"
    key_data["transports"]["anthropic-console"]["configDir"] = str(fresh)
    key_data["transports"]["anthropic-console-secondary"]["configDir"] = str(missing_login)
    key_registry.write_text(json.dumps(key_data), encoding="utf-8")

    code, out, _ = run(key_registry, "--check", "--plan", "anthropic-console")
    case("--check on a key plan whose configDir does not exist reports drift and creates nothing",
         code == 1 and not fresh.exists() and "ai_worker_model_guide.md" in out, repr((code, out, fresh.exists())))

    code, out, _ = run(key_registry, "--plan", "anthropic-console")
    case("a sync CREATES a key plan's configDir and copies the route's files into it",
         code == 0 and (fresh / "ai_worker_model_guide.md").read_text(encoding="utf-8") == "primary guide\n"
         and not (fresh / "CLAUDE.md").exists() and not (fresh / ".credentials.json").exists(), repr((code, out)))

    key_data["transports"]["anthropic-console-secondary"].pop("credentialVar", None)
    key_registry.write_text(json.dumps(key_data), encoding="utf-8")
    code, out, _ = run(key_registry, "--plan", "anthropic-console-secondary")
    case("a LOGIN plan whose configDir does not exist is blocked on the user (exit 3) and creates nothing",
         code == 3 and out.startswith("BLOCKED-ON-USER: anthropic-console-secondary") and not missing_login.exists(),
         repr((code, out)))

    (primary / "ai_worker_model_guide.md").write_text("newer guide\n", encoding="utf-8")
    (secondary / "CLAUDE.md").write_text("own instructions\n", encoding="utf-8")
    run(registry)
    case(".pre-sync is written once and keeps the original",
         (secondary / "ai_worker_model_guide.md").read_text(encoding="utf-8") == "newer guide\n"
         and (secondary / "ai_worker_model_guide.md.pre-sync").read_text(encoding="utf-8") == "stale guide\n")
    case("a destination CLAUDE.md that differs from the route's is the destination's own and stays",
         (secondary / "CLAUDE.md").read_text(encoding="utf-8") == "own instructions\n")
    revoked = copy.deepcopy(PRIMARY_SETTINGS)
    revoked["permissions"] = {"allow": ["Read"], "deny": []}
    (primary / "settings.json").write_text(json.dumps(revoked, indent=2), encoding="utf-8")
    run(registry)
    perms = json.loads((secondary / "settings.json").read_text(encoding="utf-8")).get("permissions") or {}
    case("a rule the owner removes from the source leaves the secondary; the snapshot's own rules stay",
         sorted(perms.get("allow") or []) == ["Read", "WebFetch"] and perms.get("deny") == ["Bash(curl *)"],
         repr(perms))
    case("no temp file is left in the destination",
         not [p.name for p in secondary.iterdir() if p.name.startswith(".tmp")], repr(list(secondary.iterdir())))

    # Concurrent launches sync the same dir; on Windows the loser's os.replace raises
    # PermissionError while the winner has already written the identical text.
    (primary / "ai_worker_model_guide.md").write_text("raced instructions\n", encoding="utf-8")
    real_replace = os.replace

    def racing_replace(src, dst):
        if str(dst).endswith("ai_worker_model_guide.md"):
            Path(dst).write_text("raced instructions\n", encoding="utf-8")
            raise PermissionError(5, "Access is denied", str(src))
        return real_replace(src, dst)

    os.replace = racing_replace
    try:
        code, out, _ = run(registry, replace_wait=0.3)
    finally:
        os.replace = real_replace
    case("a replace that loses a race to an identical concurrent write still syncs",
         code == 0 and (secondary / "ai_worker_model_guide.md").read_text(encoding="utf-8") == "raced instructions\n"
         and not [p.name for p in secondary.iterdir() if p.name.startswith(".tmp")], repr((code, out)))

    def stuck_replace(src, dst):
        if str(dst).endswith("ai_worker_model_guide.md"):
            raise PermissionError(5, "Access is denied", str(src))
        return real_replace(src, dst)

    (primary / "ai_worker_model_guide.md").write_text("blocked instructions\n", encoding="utf-8")
    os.replace = stuck_replace
    try:
        code, out, _ = run(registry, replace_wait=0.3)
    finally:
        os.replace = real_replace
    case("a replace that stays denied with different text still fails",
         code == 2 and (secondary / "ai_worker_model_guide.md").read_text(encoding="utf-8") == "raced instructions\n",
         repr((code, out)))

    # The winner can still hold the file mid-replace, so the loser reads the OLD text: a short
    # retry must let the loser's own replace land once the winner lets go.
    (primary / "ai_worker_model_guide.md").write_text("held instructions\n", encoding="utf-8")
    denials = {"left": 2}

    def held_replace(src, dst):
        if str(dst).endswith("ai_worker_model_guide.md") and denials["left"]:
            denials["left"] -= 1
            raise PermissionError(5, "Access is denied", str(src))
        return real_replace(src, dst)

    os.replace = held_replace
    try:
        code, out, _ = run(registry)
    finally:
        os.replace = real_replace
    case("a replace denied while a concurrent writer holds the file lands on retry",
         code == 0 and (secondary / "ai_worker_model_guide.md").read_text(encoding="utf-8") == "held instructions\n",
         repr((code, out)))

    module = load_script()
    with tempfile.TemporaryDirectory() as raced_dir:
        try:
            module.write(Path(raced_dir), "CLAUDE.md", None)
            raced = None
        except OSError as exc:
            raced = exc
    case("removing a retired copy a concurrent launch already removed still syncs", raced is None, repr(raced))

    os.environ["SYNC_TEST_HOME"] = os.path.expanduser("~")
    same = {"transports": {"anthropic": {"configDir": "~/.claude"},
                           "anthropic-secondary": {"planOf": "anthropic",
                                                   "configDir": "$SYNC_TEST_HOME/.claude/"}}}
    case("a plan whose configDir is its route's login in another spelling has nothing to sync",
         module.pairs(same) == [], repr(module.pairs(same)))
    with tempfile.TemporaryDirectory(prefix="sync_liveness_seam_") as tmp:
        module = load_script()
        module.CLAUDE_DIR = Path(tmp)
        module.process_table = lambda: {55: {"cmd": "bash run_grid.sh"}}
        case("an absent bench plugin means no project runs even with matching processes",
             module.live_benchmarks() == [])
        (Path(tmp) / "bench_root" / ".claude" / "scripts").mkdir(parents=True)
        (Path(tmp) / "bench_root" / ".claude" / "plugin.bench.json").write_text(json.dumps({
            "schema": 1, "core_root": ".", "entry": ".claude/scripts/entry.py", "sidecar_hooks": []}), encoding="utf-8")
        (Path(tmp) / "bench_root" / ".claude" / "scripts" / "entry.py").write_text(
            "def live_runs(process_table):\n    return ['planted run (%d processes)' % len(process_table)]\n",
            encoding="utf-8")
        config_dir = Path(tmp) / "skills" / "project_subsystems"
        config_dir.mkdir(parents=True)
        adaptation_file = config_dir / "adaptation.json"
        adaptation_file.write_text(json.dumps({"plugins": {"bench": str(Path(tmp) / "bench_root")}}), encoding="utf-8")
        import adaptation
        adaptation._CACHE.clear()
        module.process_table = lambda: {55: {"cmd": "x"}, 56: {"cmd": "y"}}
        live = module.live_benchmarks()
        case("a ready bench plugin answers with its entry's live_runs over the process table",
             live == ["planted run (2 processes)"], repr(live))
        adaptation_file.write_text(json.dumps({"plugins": {"bench": str(Path(tmp) / "missing_root")}}), encoding="utf-8")
        adaptation._CACHE.clear()
        live = module.live_benchmarks()
        case("a configured bench root that is not a directory counts as one live run",
             len(live) == 1 and live[0].startswith("plugin bench unreadable ("), repr(live))
    plugin_cases(case)
    return report(cases)


# ----------------------------------------------------------------------------- plugins stage
PLUGIN = "semantic-search@dreb"
TARGET = "2.69.0"
CONTRACT = {"serverInfo": {"name": "semantic-search", "version": TARGET},
            "capabilities": {"tools": {}},
            "tool": {"name": "search", "description": "planted", "inputSchema": {"type": "object"}}}
FAKE = r"""
import json, os, sys
from pathlib import Path
tool, args = sys.argv[1], sys.argv[2:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tool": tool, "argv": args, "configDir": os.environ.get("CLAUDE_CONFIG_DIR")}) + "\n")
script = json.loads(Path(os.environ["FAKE_SCRIPT"]).read_text(encoding="utf-8"))
CONTRACT = script["contract"]

def plant_version(cfg, plugin, version):
    name, market = plugin.split("@")
    d = cfg / "plugins" / "cache" / market / name / version
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "bin" / "server.js").write_text("// planted\n", encoding="utf-8")
    (d / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {"semantic-search": {
        "command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/bin/server.js"]}}}), encoding="utf-8")
    reg = cfg / "plugins" / "installed_plugins.json"
    data = json.loads(reg.read_text(encoding="utf-8")) if reg.exists() else {"version": 2, "plugins": {}}
    data["plugins"][plugin] = [{"scope": "user", "installPath": str(d), "version": version}]
    reg.write_text(json.dumps(data), encoding="utf-8")

if tool == "claude":
    cfg = Path(os.environ["CLAUDE_CONFIG_DIR"])
    if args[:3] == ["plugin", "marketplace", "update"]:
        if script.get("market_fail"):
            print("network unreachable", file=sys.stderr)
            sys.exit(1)
        for plugin, latest in script["latest"].items():
            name, market = plugin.split("@")
            f = cfg / "plugins" / "marketplaces" / market / "plugins" / name / ".claude-plugin" / "plugin.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"name": name, "version": latest}), encoding="utf-8")
        print("updated marketplace")
        sys.exit(0)
    if args[:2] in (["plugin", "update"], ["plugin", "install"]):
        plugin = args[2]
        if plugin in script.get("refuse", []):
            print("Plugin %s declares a marketplace command: npm run postinstall-hook" % plugin, file=sys.stderr)
            print("Review it, then re-run with --accept-command 3f2a9c", file=sys.stderr)
            sys.exit(1)
        plant_version(cfg, plugin, script["latest"][plugin])
        print("updated %s" % plugin)
        sys.exit(0)
    sys.exit(9)
if tool == "npm":
    prefix = Path(args[args.index("--prefix") + 1])
    if args[0] == "install":
        (prefix / "node_modules").mkdir(exist_ok=True)
    elif args[:2] == ["run", "build"]:
        (prefix / "dist").mkdir(exist_ok=True)
        (prefix / "dist" / "mcp-server.js").write_text("// built\n", encoding="utf-8")
    sys.exit(0)
if tool == "node":
    if "--print-contract" in args:
        print(json.dumps(CONTRACT))
        sys.exit(0)
    if args[0].endswith("semantic_search_csharp_hook.mjs") and args[1:2] == ["--check"]:
        # Captured from the real hook's --check against DreB 2.71.0.
        print('{"fileType":"csharp","nonCsharpType":"python","chunks":[{"name":"Armor","kind":"class"},'
              '{"name":"Armor","kind":"constructor"},{"name":"Value","kind":"property"},'
              '{"name":"ApplyDamageReduction","kind":"method"},{"name":"Health","kind":"struct"},'
              '{"name":"Value","kind":"property"},{"name":"IArmor","kind":"interface"},'
              '{"name":"Value","kind":"property"},{"name":"Element","kind":"enum"},'
              '{"name":"Hit","kind":"record"},{"name":"Packet","kind":"record"}],"parseFailureRejected":true}')
        sys.exit(0)
    target = Path(args[0])
    root = target.parent.parent
    if not target.exists() or not (root / "dist" / "mcp-server.js").exists():
        print("Error: Cannot find module dist/mcp-server.js", file=sys.stderr)
        sys.exit(1)
    for line in sys.stdin:
        msg = json.loads(line) if line.strip() else {}
        if msg.get("id") == 1:
            result = {"protocolVersion": "2024-11-05", "capabilities": CONTRACT["capabilities"],
                      "serverInfo": CONTRACT["serverInfo"]}
        elif msg.get("id") == 2:
            result = {"tools": [CONTRACT["tool"]]}
        else:
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
        sys.stdout.flush()
    sys.exit(0)
sys.exit(9)
"""


def fake_bin(root):
    """A dir of fake `claude`, `npm` and `node` that log to root/calls.jsonl and obey root/script.json."""
    bindir = root / "fakebin"
    bindir.mkdir()
    (bindir / "fake.py").write_text(FAKE, encoding="utf-8")
    for tool in ("claude", "npm", "node"):
        if os.name == "nt":
            (bindir / (tool + ".cmd")).write_text('@echo off\r\n"%s" "%s" %s %%*\r\n'
                                                  % (sys.executable, bindir / "fake.py", tool), encoding="utf-8")
        else:
            sh = bindir / tool
            sh.write_text('#!/bin/sh\nexec "%s" "%s" %s "$@"\n' % (sys.executable, bindir / "fake.py", tool),
                          encoding="utf-8")
            sh.chmod(0o755)
    return bindir


def plant_login_plugins(cfg, version, built=True):
    """One login dir holding `semantic-search@dreb` at `version` (built or not), with its dreb marketplace clone
    declaring that same version."""
    name, market = PLUGIN.split("@")
    mkt = cfg / "plugins" / "marketplaces" / market
    (mkt / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (mkt / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": market, "plugins": [{"name": name, "source": "./plugins/" + name}]}), encoding="utf-8")
    pj = mkt / "plugins" / name / ".claude-plugin" / "plugin.json"
    pj.parent.mkdir(parents=True, exist_ok=True)
    pj.write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")
    (cfg / "plugins" / "known_marketplaces.json").write_text(json.dumps(
        {market: {"source": {"source": "github", "repo": "aebrer/dreb"}, "installLocation": str(mkt)}}),
        encoding="utf-8")
    d = cfg / "plugins" / "cache" / market / name / version
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "bin" / "server.js").write_text("// planted\n", encoding="utf-8")
    (d / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {"semantic-search": {
        "command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/bin/server.js"]}}}), encoding="utf-8")
    if built:
        (d / "node_modules").mkdir()
        (d / "dist").mkdir()
        (d / "dist" / "mcp-server.js").write_text("// built\n", encoding="utf-8")
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {PLUGIN: [{"scope": "user", "installPath": str(d), "version": version}]}}),
        encoding="utf-8")
    return d


def plant_plugins(refuse=False):
    """(root, primary, secondary, registry): the plugin enabled; primary at TARGET and built (no shim yet),
    secondary at 2.28.0; the marketplace's latest is TARGET; an empty planted daemon registry."""
    root, primary, secondary, registry = plant()
    settings = json.loads((primary / "settings.json").read_text(encoding="utf-8"))
    settings["enabledPlugins"] = {PLUGIN: True, "other@dreb": False}
    (primary / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    plant_login_plugins(primary, TARGET)
    plant_login_plugins(secondary, "2.28.0")
    fake_bin(root)
    (root / "script.json").write_text(json.dumps({"latest": {PLUGIN: TARGET}, "contract": CONTRACT,
                                                  "refuse": [PLUGIN] if refuse else []}), encoding="utf-8")
    (root / "calls.jsonl").write_text("", encoding="utf-8")
    return root, primary, secondary, registry


@contextlib.contextmanager
def faked(root):
    """Fake CLIs first on PATH, the planted script and log."""
    saved = {k: os.environ.get(k) for k in ("PATH", "FAKE_LOG", "FAKE_SCRIPT")}
    os.environ["PATH"] = str(root / "fakebin") + os.pathsep + os.environ.get("PATH", "")
    os.environ["FAKE_LOG"] = str(root / "calls.jsonl")
    os.environ["FAKE_SCRIPT"] = str(root / "script.json")
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def calls(root):
    """Every fake-CLI call since the last read, then clear the log."""
    rows = [json.loads(ln) for ln in (root / "calls.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    (root / "calls.jsonl").write_text("", encoding="utf-8")
    return rows


def installed(cfg):
    data = json.loads((cfg / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    return [e for e in data["plugins"][PLUGIN] if e["scope"] == "user"][0]


def same_dir(a, b):
    return a is not None and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(str(b)))


def changing(log):
    """The calls that change a login: a plugin update or install, or any npm step."""
    return [c for c in log if c["tool"] == "npm" or c["argv"][:2] in (["plugin", "update"], ["plugin", "install"])]


def session_start_commands(cfg):
    groups = (json.loads((cfg / "settings.json").read_text(encoding="utf-8")).get("hooks") or {}).get("SessionStart", [])
    return [h.get("command") for g in groups for h in g.get("hooks", [])]


def scenario_below_target_updates_and_builds(case):
    root, primary, secondary, registry = plant_plugins()
    with faked(root):
        code, out, _ = run(registry, "--check")
        log = calls(root)
        case("with the window elapsed, --check makes no claude call and reports the refresh as drift, so the "
             "SessionStart hook's detached sync runs it",
             not [c for c in log if c["tool"] == "claude"] and "marketplace refresh due" in out, repr((out, log)))
        case("--check reports a login below its marketplace's latest, naming the login, plugin and both versions",
             code == 1 and PLUGIN in out and "2.28.0" in out and TARGET in out and "anthropic-secondary" in out,
             repr((code, out)))
        case("--check updates no plugin and runs no build",
             installed(secondary)["version"] == "2.28.0" and not changing(log), repr(log))
        case("--check writes no state file: it changes no config file",
             not (primary / "sync_plan_config.state.json").exists(), repr(sorted(os.listdir(primary))))

        code, out, _ = run(registry)
        log = calls(root)
        updates = [c for c in log if c["argv"][:2] == ["plugin", "update"]]
        sec = installed(secondary)
        case("a login below target runs `claude plugin update` under its own CLAUDE_CONFIG_DIR, and only that login",
             code == 0 and len(updates) == 1 and updates[0]["argv"][2:] == [PLUGIN]
             and same_dir(updates[0]["configDir"], secondary) and sec["version"] == TARGET,
             repr((code, out, updates)))
        npm = [c for c in log if c["tool"] == "npm"]
        case("the updated login's new version is built: npm install then npm run build, --prefix its cache dir",
             [c["argv"][:2] for c in npm] == [["install", "--prefix"], ["run", "build"]]
             and all(same_dir(c["argv"][c["argv"].index("--prefix") + 1], sec["installPath"]) for c in npm)
             and (Path(sec["installPath"]) / "dist" / "mcp-server.js").exists(), repr(npm))
        case("the install skips peer resolution, which crashes npm 11's arborist on the plugin's optional dev peers",
             "--legacy-peer-deps" in npm[0]["argv"] if npm else False, repr(npm))
        state = secondary / "semantic-search-idle" / "install.json"
        case("the updated login gets the idle shim under its own login dir, pinned to the new version",
             state.exists() and json.loads(state.read_text(encoding="utf-8")).get("version") == TARGET, repr(out))
        case("a login missing only its shim gets it, with no plugin update or build",
             (primary / "semantic-search-idle" / "install.json").exists()
             and not [c for c in changing(log) if same_dir(c.get("configDir"), primary)
                      or str(primary) in " ".join(c["argv"])], repr(log))
        case("a --check does not spend the refresh window: the sync after it refreshes each login's marketplace clone",
             sorted(os.path.normcase(c["configDir"]) for c in log
                    if c["argv"][:3] == ["plugin", "marketplace", "update"])
             == sorted(os.path.normcase(str(d)) for d in (primary, secondary)), repr(log))
        own = 'python "%s" --check --config-dir "%s"' % ((secondary / "semantic-search-idle" / "check.py").as_posix(),
                                                         secondary.as_posix())
        route = 'python "%s" --check --config-dir "%s"' % ((primary / "semantic-search-idle" / "check.py").as_posix(),
                                                           primary.as_posix())
        first = session_start_commands(secondary)
        code, out, _ = run(registry)
        log = calls(root)
        case("each login's SessionStart runs its own login's shim check, never the route's, across a re-sync",
             own in first and route not in first and session_start_commands(secondary) == first
             and route in session_start_commands(primary), repr((first, session_start_commands(primary))))
        case("a login at target inside the refresh window runs nothing: no claude call, no npm",
             code == 0 and not [c for c in log if c["tool"] in ("claude", "npm")], repr((code, out, log)))
        code, out, _ = run(registry, "--check")
        case("--check is clean after the sync", code == 0, repr((code, out)))


def scenario_files_only_syncs_no_plugin_step(case):
    root, primary, secondary, registry = plant_plugins()
    with faked(root):
        code, out, _ = run(registry, "--files-only")
        log = calls(root)
        case("--files-only syncs the files and runs no plugin step: no claude call, no npm, the drifted login unchanged",
             code == 0 and not [c for c in log if c["tool"] in ("claude", "npm")]
             and installed(secondary)["version"] == "2.28.0" and (secondary / "settings.json").exists(),
             repr((code, out, log)))
    sidecar = (Path(__file__).resolve().parents[1] / "scripts" / "anthropic_sidecar.sh").read_text(encoding="utf-8")
    case("a sidecar launch syncs files only, so concurrent launches never update a plugin under a running sibling",
         re.search(r"sync_plan_config\.py\"? --plan \"\$SC_PLAN\" --files-only", sidecar) is not None,
         "anthropic_sidecar.sh calls sync_plan_config.py without --files-only")


def scenario_login_without_plugin_installs(case):
    root, primary, secondary, registry = plant_plugins()
    (secondary / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}),
                                                                  encoding="utf-8")
    with faked(root):
        code, out, _ = run(registry)
        log = calls(root)
        installs = [c for c in log if c["argv"][:2] == ["plugin", "install"]]
        case("a login without the plugin installs it through the sync, then builds it and gets its shim",
             code == 0 and len(installs) == 1 and installs[0]["argv"][2:] == [PLUGIN]
             and same_dir(installs[0]["configDir"], secondary) and installed(secondary)["version"] == TARGET
             and (secondary / "semantic-search-idle" / "install.json").exists(), repr((code, out, installs)))


def scenario_key_plan_syncs_files_only(case):
    """A key plan's configDir is not a login, so the plugin stage leaves it alone while the files
    stage still fills it."""
    root, primary, secondary, registry = plant_plugins()
    console = root / "console"
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["transports"]["anthropic-console"]["configDir"] = str(console)
    registry.write_text(json.dumps(data), encoding="utf-8")
    with faked(root):
        code, out, _ = run(registry)
        touched = {os.path.normcase(str(c.get("configDir"))) for c in calls(root)}
        case("the plugins stage acts on no key plan; the files stage still fills its configDir",
             code == 0 and os.path.normcase(str(console)) not in touched and (console / "ai_worker_model_guide.md").exists(),
             repr((code, sorted(touched))))


def scenario_refused_update_blocks_on_user(case):
    root, primary, secondary, registry = plant_plugins(refuse=True)
    with faked(root):
        code, out, _ = run(registry)
        log = calls(root)
        blocked = [ln for ln in out.splitlines() if ln.startswith("BLOCKED-ON-USER")]
        case("an --accept-command refusal prints one BLOCKED-ON-USER line naming the login, plugin and command; exit nonzero",
             code not in (0, 1) and len(blocked) == 1 and PLUGIN in blocked[0] and "--accept-command" in blocked[0]
             and "anthropic-secondary" in blocked[0], repr((code, out)))
        case("the tool never supplies --accept-command, and a refused update builds nothing",
             not [c for c in log if "--accept-command" in c["argv"]] and not [c for c in log if c["tool"] == "npm"],
             repr(log))


def scenario_deferral_keeps_the_refresh_pending(case):
    root, primary, secondary, registry = plant_plugins()
    state = primary / "sync_plan_config.state.json"
    with faked(root):
        run(registry, plugins={"bench": str(root / "not-installed")})
        calls(root)
        stamped = json.loads(state.read_text(encoding="utf-8")).get("marketplacesUpdatedAt") if state.exists() else None
        case("a deferred sync does not spend the refresh window", not stamped, repr(stamped))
        code, out, _ = run(registry, plugins={})
        log = calls(root)
        case("the sync after a deferral refreshes the marketplaces again and applies the update",
             [c for c in log if c["argv"][:3] == ["plugin", "marketplace", "update"]]
             and installed(secondary)["version"] == TARGET, repr((code, out, log)))
        stamped = json.loads(state.read_text(encoding="utf-8")).get("marketplacesUpdatedAt")
        case("a sync that applies every change spends the refresh window", bool(stamped), repr(stamped))


def scenario_absent_benchmark_directory(case):
    root, primary, secondary, registry = plant_plugins()
    module = load_script()
    planted = root / "planted-claude" / "skills" / "project_subsystems"
    planted.mkdir(parents=True)
    (planted / "adaptation.json").write_text(json.dumps({"plugins": {}}), encoding="utf-8")
    module.CLAUDE_DIR = planted.parents[1]
    module.process_table = lambda: {55: {"cmd": "bash run_grid.sh"}}
    live = module.live_benchmarks()
    case("an absent bench plugin means no project runs", live == [], repr(live))
    with faked(root):
        code, out, _ = run(registry, plugins={})
        case("a consumer without the bench plugin applies plugin work rather than deferring",
             code == 0 and "deferred" not in out and bool(changing(calls(root)))
             and installed(secondary)["version"] == TARGET, repr((code, out)))


def scenario_missing_bench_root_defers(case):
    root, primary, secondary, registry = plant_plugins()
    with faked(root):
        code, out, _ = run(registry, plugins={"bench": str(root / "not-installed")})
        case("a configured bench root that is not a directory defers the plugins stage",
             code == 0 and "deferred" in out and not changing(calls(root))
             and installed(secondary)["version"] == "2.28.0", repr((code, out)))


def plant_failing_refresh(aligned):
    """plant_plugins() whose marketplace update fails; `aligned` puts the secondary at TARGET as well."""
    root, primary, secondary, registry = plant_plugins()
    if aligned:
        plant_login_plugins(secondary, TARGET)
    script = json.loads((root / "script.json").read_text(encoding="utf-8"))
    script["market_fail"] = True
    (root / "script.json").write_text(json.dumps(script), encoding="utf-8")
    return root, registry


def scenario_failed_refresh_exits_three(case):
    root, registry = plant_failing_refresh(aligned=True)
    with faked(root):
        code, out, _ = run(registry)
        case("a failed marketplace refresh exits 3 even when every login already matches the stale clone",
             code == 3 and "marketplace update" in out and "failed" in out, repr((code, out)))
    root, registry = plant_failing_refresh(aligned=False)
    with faked(root):
        code, out, _ = run(registry, plugins={"bench": str(root / "not-installed")})
        case("a failed marketplace refresh exits 3 when the plugin step defers too",
             code == 3 and "deferred" in out and "marketplace update" in out, repr((code, out)))


def scenario_deadline_holds_past_a_descendant_pipe(case):
    module = load_script()
    child = ("import subprocess, sys, time; time.sleep(1); "
             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)")
    start = time.monotonic()
    proc = module._run([sys.executable, "-c", child], 2)
    elapsed = time.monotonic() - start
    case("a command whose grandchild holds its output past the deadline returns None within the deadline plus cleanup",
         proc is None and elapsed < 15, repr((proc, round(elapsed, 1))))


def scenario_missing_login_dir_blocks_only_that_plan(case):
    root, primary, secondary, registry = plant_plugins()
    shutil.rmtree(secondary)
    shutil.rmtree(primary / "plugins" / "cache")
    plant_login_plugins(primary, TARGET, built=False)
    with faked(root):
        code, out, _ = run(registry, "--check")
        lines = out.splitlines()
        case("--check on a missing non-key login dir prints one BLOCKED-ON-USER line, last, and exits 3",
             code == 3 and len([ln for ln in lines if ln.startswith("BLOCKED-ON-USER")]) == 1
             and lines[-1].startswith("BLOCKED-ON-USER: anthropic-secondary configDir %s does not exist; "
                                      "log in to it once with `claude` under that CLAUDE_CONFIG_DIR" % secondary),
             repr((code, out)))
        calls(root)
        code, out, _ = run(registry)
        log = calls(root)
        blocked = [ln for ln in out.splitlines() if ln.startswith("BLOCKED-ON-USER")]
        case("a sync prints the BLOCKED line, exits 3 and creates nothing at the missing dir",
             code == 3 and len(blocked) == 1 and "anthropic-secondary" in blocked[0] and not secondary.exists(),
             repr((code, out)))
        npm = [c for c in log if c["tool"] == "npm"]
        case("the route's own login still gets its npm build and idle shim",
             [c["argv"][:2] for c in npm] == [["install", "--prefix"], ["run", "build"]]
             and (primary / "semantic-search-idle" / "install.json").exists(), repr((npm, out)))
        case("no claude call names the missing dir, and key plans still get their files",
             not [c for c in log if same_dir(c.get("configDir"), secondary)]
             and (root / "console-primary" / "ai_worker_model_guide.md").exists(), repr(log))


def _scenario_cases(scenario):
    found = []
    scenario(lambda name, ok, detail="": found.append((name, bool(ok), detail)))
    return found


def plugin_cases(case):
    """Each scenario plants its own root and fakes PATH in its own process, so they run at once."""
    from concurrent.futures import ProcessPoolExecutor
    scenarios = (scenario_missing_login_dir_blocks_only_that_plan,
                 scenario_failed_refresh_exits_three,
                 scenario_deadline_holds_past_a_descendant_pipe,
                 scenario_below_target_updates_and_builds,
                 scenario_files_only_syncs_no_plugin_step,
                 scenario_login_without_plugin_installs,
                 scenario_key_plan_syncs_files_only,
                 scenario_refused_update_blocks_on_user,
                 scenario_deferral_keeps_the_refresh_pending,
                 scenario_absent_benchmark_directory,
                 scenario_missing_bench_root_defers)
    jobs = int(os.environ.get("HARNESS_CASE_JOBS") or 4)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for found in pool.map(_scenario_cases, scenarios):
            for name, ok, detail in found:
                case(name, ok, detail)


def report(cases):
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
