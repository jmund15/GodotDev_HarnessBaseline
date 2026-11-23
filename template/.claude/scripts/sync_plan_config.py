#!/usr/bin/env python3
"""Keep every seat's login dir in step with the default login's, so a delegate behaves the same
on either account: the same config files, and the same plugins at the same version.

A seat with an existing `configDir` in `seats.json` is a destination; the default login `~/.claude` is the
source. Per destination:
  ai_worker_model_guide.md              copied whole when the source has it
  CLAUDE.md                             removed when byte-identical to the source's (RETIRED_COPIES)
  settings.json                         the source's values, except the per-account keys
                                        (PER_ACCOUNT_KEYS) kept from the destination, and
                                        permissions.allow and .deny as the source's rules plus
                                        the destination's own; a hook command that runs the
                                        source login's idle-shim check runs the destination's
Writes go through a temp file in the destination dir and `os.replace`. Concurrent launches sync the
same dir, and on Windows the replace that loses the race is denied; it retries for about two
seconds, then counts as synced when the file already holds the same text, and fails otherwise. The first write of a file
saves the prior copy as `<name>.pre-sync`, once. A destination's OWN permission rules are the
`allow` and `deny` entries of its `settings.json.pre-sync` that the source lacks, so a rule the
owner removes from the source leaves the destination on the next sync unless the destination had
it before its first sync. With no snapshot yet, the destination's current rules are its own.
The file stage opens only the files named here: credentials, `.claude.json` and history are never
read or written.

The plugins stage follows, over the default login and each seat's login that exists. A plugin the
default login's `settings.json` enables (`enabledPlugins`, so user scope) has one target: the version its
marketplace clone in the default login declares (the marketplace entry's `version`, else the plugin's
`plugin.json`), read after `claude plugin marketplace update` under each login, at most once per
REFRESH_SECONDS (the time is kept in `~/.claude/sync_plan_config.state.json`). A plugin whose
marketplace declares no version is updated on every login at each refresh, and between refreshes its
logins must hold one version.
  - A login without the plugin runs `claude plugin install`; one below target `claude plugin update`,
    under CLAUDE_CONFIG_DIR=<login> (unset for `~/.claude`). A refusal that asks for
    `--accept-command` prints one BLOCKED-ON-USER line; the tool never supplies it.
  - POST_INSTALL lists, per plugin, steps each with a check and a repair; a plugin with no row gets
    none. `semantic-search@dreb`: the build (`node <dir>/bin/server.js` exits 0 with empty stderr
    within 3 s, else `npm install` and `npm run build` with `--prefix <dir>`), then the idle shim for
    that login (`tools/semantic_search_idle_install.py --check`, else `--install`, with
    `--config-dir <login>`). A login whose version and install dir passed every check since the last
    refresh is not checked again until the next one.

Usage:
    sync_plan_config.py [--check] [--files-only] [--seat <name>]
      --check   list what a sync would change and exit 1 on any drift; it changes no config file, no
                plugin and no marketplace clone, and makes no network call. An elapsed refresh window
                is drift: the sync refreshes the clones and starts the next window
      --seat    only that seat (default: every seat with a `configDir` that exists here and is not the
                default login's; a seat whose login dir is absent is not in the table `launch_check.seat_rows`
                returns, so it is skipped silently). The plugins stage runs on the default login with no seat.
      --files-only  skip the plugins stage. A sidecar launch passes it: a plugin update replaces the
                cache dir under sibling sessions on that login, and concurrent launches would race
                it; the SessionStart hook (hooks/login_parity_check.py) owns plugin repair
Exit: 0 in step (or synced, or plugin work deferred), 1 drift under --check, 2 bad usage or an
unreadable file, 3 a plugin step failed, or a step is BLOCKED-ON-USER (each named on its own line),
unless a failure already sets exit 2. Exit 3 holds in --check too.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
CLAUDE_DIR = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parent / "hooks"))
from _bounded_run import run_bounded  # noqa: E402
from _hook_state import replace_path  # noqa: E402
import launch_check  # noqa: E402
import plugin_registry  # noqa: E402
import semantic_search_idle_install as idle_shim  # noqa: E402

WHOLE_FILES = ("ai_worker_model_guide.md",)
# Every project under the home dir loads the default login's `~/.claude/CLAUDE.md` as its ancestor
# `.claude/CLAUDE.md`, whatever CLAUDE_CONFIG_DIR says, so a seat's copy loads it a second time.
# A sync removes a copy byte-identical to the default login's; a different file is the seat's own and stays.
RETIRED_COPIES = ("CLAUDE.md",)
SETTINGS = "settings.json"
PER_ACCOUNT_KEYS = ("autoMemoryDirectory", "mcpServers", "effortLevel")
STATE = "sync_plan_config.state.json"
REFRESH_SECONDS = 24 * 60 * 60
EXIT_PLUGINS = 3
DEFAULT_LOGIN = Path(os.path.expanduser("~/.claude"))


class SyncError(RuntimeError):
    pass


def _same_login(a, b):
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def pairs(only=None):
    """[(seat, source dir, destination dir)] for each `seats.json` seat whose login dir is not the default's."""
    try:
        seats = launch_check.seat_rows(CLAUDE_DIR)
    except (OSError, ValueError) as exc:
        raise SyncError("seats.json is unreadable: %s" % exc)
    if only and only not in seats:
        raise SyncError("no seat %r in seats.json" % only)
    return [(name, DEFAULT_LOGIN, Path(os.path.expanduser(row["configDir"]))) for name, row in sorted(seats.items())
            if row.get("configDir") and not (only and name != only)
            and not _same_login(os.path.expanduser(row["configDir"]), DEFAULT_LOGIN)]


def _read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _read_json(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise SyncError("%s is not JSON: %s" % (path, exc))
    if not isinstance(value, dict):
        raise SyncError("%s is not a JSON object" % path)
    return value


def merged_settings(source, dest, snapshot=None, commands=None):
    """The source's settings with the destination's per-account keys, and for `allow` and `deny` the
    source's rules followed by the destination's own: those of `snapshot` (its pre-sync settings),
    else of `dest`, that the source lacks. A hook command that is a key of `commands` becomes its
    value."""
    out = copy.deepcopy(source)
    hooks = out.get("hooks") if isinstance(out.get("hooks"), dict) else {}
    for groups in hooks.values():
        for group in groups if isinstance(groups, list) else ():
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else ():
                if isinstance(hook, dict) and hook.get("command") in (commands or {}):
                    hook["command"] = commands[hook["command"]]
    dest = dest or {}
    own = dest if snapshot is None else snapshot
    for key in PER_ACCOUNT_KEYS:
        if key in dest:
            out[key] = copy.deepcopy(dest[key])
        else:
            out.pop(key, None)
    for kind in ("allow", "deny"):
        rules = list(((source.get("permissions") or {}).get(kind)) or [])
        for rule in ((own.get("permissions") or {}).get(kind)) or []:
            if rule not in rules:
                rules.append(rule)
        if rules:
            out.setdefault("permissions", {})[kind] = rules
    return out


def changes(source_dir, dest_dir):
    """[(file name, new text, description)] for every file a sync would rewrite; text None removes it."""
    out = []
    for name in WHOLE_FILES:
        text = _read_text(source_dir / name)
        if text is not None and text != _read_text(dest_dir / name):
            out.append((name, text, "differs from %s" % (source_dir / name)))
    for name in RETIRED_COPIES:
        text = _read_text(dest_dir / name)
        if text is not None and text == _read_text(source_dir / name):
            out.append((name, None, "is a copy of %s, already loaded as an ancestor file" % (source_dir / name)))
    source = _read_json(source_dir / SETTINGS)
    if source is not None:
        current = _read_json(dest_dir / SETTINGS)
        target = merged_settings(source, current, _read_json(dest_dir / (SETTINGS + ".pre-sync")),
                                 {shim_command(source_dir): shim_command(dest_dir)})
        if target != current:
            keys = sorted(k for k in set(target) | set(current or {})
                          if target.get(k) != (current or {}).get(k))
            out.append((SETTINGS, json.dumps(target, indent=2, ensure_ascii=False) + "\n",
                        "keys " + ", ".join(keys)))
    return out


def shim_command(login):
    """The SessionStart command that checks `login`'s idle shim."""
    return idle_shim.Paths(Path.home(), config_dir=Path(login)).check_command()


# ----------------------------------------------------------------------------- plugins stage
def _cli(name):
    return shutil.which(name) or name


def _tail(proc):
    lines = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines()
    return lines[-1].strip() if lines else "exit %d" % proc.returncode


def _run(argv, timeout, env=None):
    """The completed process, or None when it outlived `timeout` seconds; its whole process tree ends either way."""
    proc = run_bounded(argv, timeout, env=env)
    return None if proc.returncode is None else proc


def claude(login, *args):
    """`claude <args>` on `login`: CLAUDE_CONFIG_DIR set to it, or unset for the default login."""
    env = dict(os.environ)
    if _same_login(login, DEFAULT_LOGIN):
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = str(login)
    proc = _run([_cli("claude"), *args], 900, env)
    if proc is None:
        raise SyncError("`claude %s` on %s ran past 900 s" % (" ".join(args), login))
    return proc


def check_build(login, install_dir):
    """None when `node <dir>/bin/server.js` exits 0 with empty stderr within 3 s (stdin at EOF), else why not."""
    proc = _run([_cli("node"), str(Path(install_dir) / "bin" / "server.js")], 3)
    if proc is None:
        return "node bin/server.js did not exit within 3 s"
    if proc.returncode != 0 or (proc.stderr or "").strip():
        return "node bin/server.js: %s" % _tail(proc)
    return None


def repair_build(login, install_dir):
    # --legacy-peer-deps: npm 11's arborist crashes (`edgesOut` of null) resolving the plugin's optional dev peers.
    for argv in (["install", "--prefix", str(install_dir), "--legacy-peer-deps"],
                 ["run", "build", "--prefix", str(install_dir)]):
        proc = _run([_cli("npm"), *argv], 900)
        if proc is None or proc.returncode != 0:
            return "npm %s: %s" % (" ".join(argv[:argv.index("--prefix")]), "timed out" if proc is None else _tail(proc))
    return None


def _shim(action, login):
    return _run([sys.executable, str(TOOLS / "semantic_search_idle_install.py"), action,
                 "--config-dir", str(login)], 600)


def check_shim(login, install_dir):
    proc = _shim("--check", login)
    if proc is None:
        return "idle shim check timed out"
    return None if proc.returncode == 0 and not proc.stdout.strip() else _tail(proc)


def repair_shim(login, install_dir):
    proc = _shim("--install", login)
    if proc is None:
        return "idle shim install timed out"
    return None if proc.returncode == 0 else _tail(proc)


# Authored per plugin, in order: (step, check, repair). A check returns None when healthy, else why not;
# a repair returns None when it ran, else why it failed. A plugin with no row has no steps.
POST_INSTALL = {
    "semantic-search@dreb": (("build", check_build, repair_build),
                             ("idle shim", check_shim, repair_shim)),
}


def enabled_plugins(source_dir):
    enabled = (_read_json(source_dir / SETTINGS) or {}).get("enabledPlugins") or {}
    return sorted(name for name, on in enabled.items() if on is True and "@" in name)


def declared_version(login, plugin):
    """The version `login`'s clone of the plugin's marketplace declares, or None when it declares none."""
    name, market = plugin.rsplit("@", 1)
    known = _read_json(login / "plugins" / "known_marketplaces.json") or {}
    location = (known.get(market) or {}).get("installLocation")
    if not location:
        return None
    manifest = _read_json(Path(location) / ".claude-plugin" / "marketplace.json") or {}
    entry = next((p for p in manifest.get("plugins") or [] if isinstance(p, dict) and p.get("name") == name), None)
    if not entry:
        return None
    if entry.get("version"):
        return str(entry["version"])
    source = entry.get("source")
    if isinstance(source, str) and source.startswith("./"):
        version = (_read_json(Path(location) / source / ".claude-plugin" / "plugin.json") or {}).get("version")
        return str(version) if version else None
    return None


def user_install(login, plugin):
    """`login`'s one user-scope registration of `plugin` ({version, installPath, ...}), or None when absent."""
    try:
        entries = plugin_registry.user_rows(login, plugin)
    except ValueError as exc:
        raise SyncError("%s is not JSON: %s" % (login / "plugins" / "installed_plugins.json", exc))
    if len(entries) > 1:
        raise SyncError("%s holds %d user-scope registrations of %s" % (login, len(entries), plugin))
    return entries[0] if entries else None


def _verified_key(login, entry):
    return "%s|%s|%s" % (os.path.normcase(os.path.realpath(str(login))), entry.get("version"), entry.get("installPath"))


def _pending(logins, plugins, refresh, check, verified):
    """[(login name, login dir, plugin, action, line)]: action `install`/`update`, or the post-install step a
    login at target fails. A login that passed every step at its version and install dir is skipped."""
    out = []
    source_dir = logins[0][1]
    for plugin in plugins:
        target = declared_version(source_dir, plugin)
        found = [(name, login, user_install(login, plugin)) for name, login in logins]
        spread = len({(e or {}).get("version") for _n, _l, e in found}) > 1
        for name, login, entry in found:
            if entry is None:
                out.append((name, login, plugin, "install", "%s: %s missing (install %s)"
                            % (name, plugin, target or "its latest")))
            elif target and entry.get("version") != target:
                out.append((name, login, plugin, "update", "%s: %s %s -> %s (update)"
                            % (name, plugin, entry.get("version"), target)))
            elif not target and (spread or (refresh and not check)):
                out.append((name, login, plugin, "update", "%s: %s %s, unversioned in its marketplace (update)"
                            % (name, plugin, entry.get("version"))))
            elif verified.get(_verified_key(login, entry)) != plugin:
                for step, test, _repair in POST_INSTALL.get(plugin, ()):
                    why = test(login, entry.get("installPath"))
                    if why:
                        out.append((name, login, plugin, step, "%s: %s %s needs repair: %s" % (name, plugin, step, why)))
                        break
                else:
                    verified[_verified_key(login, entry)] = plugin
    return out


def _apply(name, login, plugin, action, source_dir, verified):
    """Bring one login's plugin to target and through its post-install steps; None, else the failure line."""
    if action in ("install", "update"):
        proc = claude(login, "plugin", action, plugin)
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0 and "--accept-command" in text:
            said = " ".join(ln.strip() for ln in text.splitlines() if ln.strip())
            return ("BLOCKED-ON-USER: %s: `claude plugin %s %s` refused until a person accepts its marketplace "
                    "command: %s -- review that command, then run `claude plugin %s %s --accept-command <sha256>` "
                    "with CLAUDE_CONFIG_DIR=%s" % (name, action, plugin, said, action, plugin, login))
        if proc.returncode != 0:
            return "plugins: %s: `claude plugin %s %s` failed: %s" % (name, action, plugin, _tail(proc))
        print("plugins: %s: %s %s done" % (name, plugin, action))
    entry = user_install(login, plugin)
    target = declared_version(source_dir, plugin)
    if entry is None or (target and entry.get("version") != target):
        return "plugins: %s: %s is %s after %s; target %s" % (
            name, plugin, (entry or {}).get("version", "absent"), action, target)
    for step, test, repair in POST_INSTALL.get(plugin, ()):
        if test(login, entry.get("installPath")) is None:
            continue
        why = repair(login, entry.get("installPath")) or test(login, entry.get("installPath"))
        if why:
            return "plugins: %s: %s %s failed: %s" % (name, plugin, step, why)
        print("plugins: %s: %s %s repaired" % (name, plugin, step))
    verified[_verified_key(login, entry)] = plugin
    return None


def plugins_stage(logins, check, now=None):
    """(drift count, failure count) for the logins [(name, dir)], the default login first. Prints a line
    per drift, deferral, change and failure."""
    source_dir = logins[0][1]
    plugins = enabled_plugins(source_dir)
    if not plugins:
        return 0, 0
    now = time.time() if now is None else now
    state_path = source_dir / STATE
    state = _read_json(state_path) or {}
    refresh = now - float(state.get("marketplacesUpdatedAt") or 0) >= REFRESH_SECONDS
    due = 1 if refresh and check else 0
    settled = False
    refresh_failures = 0
    if due:
        print("plugins: marketplace refresh due (every %d h); a sync runs it" % (REFRESH_SECONDS // 3600))
    elif refresh:
        state["verified"] = {}
        for market in sorted({p.rsplit("@", 1)[1] for p in plugins}):
            for name, login in logins:
                proc = claude(login, "plugin", "marketplace", "update", market)
                if proc.returncode != 0:
                    refresh_failures += 1
                    print("plugins: %s: marketplace update %s failed (%s); targets come from the existing clone"
                          % (name, market, _tail(proc)))
        settled = not refresh_failures
    verified = state.setdefault("verified", {})
    try:
        todo = _pending(logins, plugins, refresh, check, verified)
        if check:
            for item in todo:
                print(item[4])
            return len(todo) + due, refresh_failures
        failures = []
        for name, login, plugin, action, _line in todo:
            line = _apply(name, login, plugin, action, source_dir, verified)
            if line:
                failures.append(line)
        for plugin in plugins:
            held = {(user_install(login, plugin) or {}).get("version") for _n, login in logins}
            if declared_version(source_dir, plugin) is None and len(held) > 1:
                failures.append("plugins: %s is at %s across the logins after updating each"
                                % (plugin, ", ".join(sorted(str(v) for v in held))))
        for line in failures:
            print(line)
        if settled and not failures:
            state["marketplacesUpdatedAt"] = now
        return 0, len(failures) + refresh_failures
    finally:
        if not check:
            _atomic(state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def write(dest_dir, name, text):
    """Replace `dest_dir/name` atomically, saving the prior file as `<name>.pre-sync` the first time.
    `text` None removes the file: `changes` passes None only for a byte-identical retired copy."""
    path = dest_dir / name
    if text is None:
        path.unlink(missing_ok=True)
        return
    backup = dest_dir / (name + ".pre-sync")
    prior = _read_text(path)
    if prior is not None and not backup.exists():
        _atomic(backup, prior)
    _atomic(path, text)


def _atomic(path, text):
    fd, tmp = tempfile.mkstemp(prefix=".tmp_sync_", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        try:
            replace_path(tmp, path)
        except PermissionError as original:
            try:
                current = _read_text(path)
            except OSError:
                raise original from None
            if current != text:
                raise
            os.unlink(tmp)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv):
    check = "--check" in argv
    files_only = "--files-only" in argv
    rest = [a for a in argv if a not in ("--check", "--files-only")]
    only = None
    if rest[:1] == ["--seat"] and len(rest) == 2:
        only = rest[1]
    elif rest:
        print(__doc__.split("Usage:")[1].strip(), file=sys.stderr)
        return 2
    try:
        drift = failed = 0
        pending = pairs(only)
        for seat, source_dir, dest_dir in pending:
            for name, text, what in changes(source_dir, dest_dir):
                drift += 1
                done = "removed" if text is None else "synced"
                print("%s: %s %s" % (seat, name, what) if check else "%s: %s %s (%s)" % (seat, done, name, what))
                if not check:
                    write(dest_dir, name, text)
        if not files_only:
            logins = [("default", DEFAULT_LOGIN)] + [(seat, dest) for seat, _src, dest in pending]
            more, bad = plugins_stage(logins, check)
            drift += more
            failed += bad
    except (SyncError, OSError, subprocess.SubprocessError) as exc:
        print("sync_plan_config: %s" % exc, file=sys.stderr)
        return 2
    if failed:
        return EXIT_PLUGINS
    return 1 if check and drift else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
