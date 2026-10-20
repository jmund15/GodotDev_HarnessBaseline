#!/usr/bin/env python3
"""Check, install, adopt and lint the home-folder files that ship as repo templates.

    python3 .claude/tools/user_scope.py check   [--home DIR] [--scope-dir DIR] [--json]
    python3 .claude/tools/user_scope.py install [--home DIR] [--scope-dir DIR] [--json]
    python3 .claude/tools/user_scope.py adopt <id> [--home DIR] [--scope-dir DIR]
    python3 .claude/tools/user_scope.py lint [--home DIR] [--scope-dir DIR]

Items come from `.claude/user_scope/manifest.json` and the optional `manifest.local.json`.
`--home` scopes the files and `settings.json`; an `env` item is a machine-wide user variable and ignores it.
A `setting` item is one top-level key with a JSON value in a `store`: `settings` (~/.claude/settings.json, the
default), `local` (the checkout's .claude/settings.local.json, with `{repo}` in a string value replaced by the
checkout path) or `claude-json` (~/.claude.json, read for that one key and never written). `check` reports
`unset` (exit 1, settings and local), `set`, `differs` (the home value is kept, exit 0), `manual` (the key is
absent from ~/.claude.json: turn it on in the app) or `unreadable`; `install` adds only an absent key.
`--repo` names the checkout.
Exit codes: 0 green; 1 check finds a missing, unregistered or unset item, or lint and adopt find a
secret-shaped string, the home path or the user name; 2 a manifest, settings, write or refusal error
(a manifest error writes nothing).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import _hook_state  # noqa: E402

PLATFORM = sys.platform
REPO = Path(__file__).resolve().parents[2]
SCOPE_DEFAULT = REPO / ".claude" / "user_scope"
MANIFESTS = ("manifest.json", "manifest.local.json")
KIND_FIELDS = {
    "file": {"src", "dest", "platform", "register"},
    "env": {"name", "value", "platform"},
    "setting": {"key", "value", "store"},
    "local": {"entries"},
}
COMMON_FIELDS = {"id", "kind", "note"}
RED = {"missing", "unregistered", "unset"}
STORES = ("settings", "local", "claude-json")
UNREADABLE = object()
_replace = _hook_state.replace_path


class Fail(Exception):
    """A manifest, settings or write error. The message names the item and the rule."""


def under(path, root):
    child = Path(os.path.normcase(os.path.abspath(path)))
    base = Path(os.path.normcase(os.path.abspath(root)))
    return child != base and child.is_relative_to(base)


def text_field(raw, key, where):
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise Fail(f"{where}: {key} must be a non-empty string")
    return value


def dest_path(item, home):
    return home / item["dest"][2:]


def validate_register(reg, where):
    if not isinstance(reg, dict):
        raise Fail(f"{where}: register must be an object")
    if "hook" in reg:
        hook = reg["hook"]
        if set(reg) != {"hook"} or not isinstance(hook, dict) \
                or not {"event", "command"} <= set(hook) <= {"event", "command", "matcher"} \
                or not all(isinstance(hook[key], str) and hook[key] for key in hook):
            raise Fail(f"{where}: register.hook takes event and command (strings) and an optional matcher")
    elif "setting" in reg:
        if set(reg) != {"setting", "value"} or not isinstance(reg["setting"], str) or not reg["setting"]:
            raise Fail(f"{where}: a setting register takes a setting name and a value")
    else:
        raise Fail(f"{where}: register needs a hook or a setting key")


def validate_file(raw, where, scope, home):
    src = text_field(raw, "src", where)
    dest = text_field(raw, "dest", where)
    if Path(src).name.casefold() == "claude.md":
        raise Fail(f"{where}: src must not be named CLAUDE.md (a folder read loads it as nested project memory)")
    if not under(scope / src, scope) or not (scope / src).is_file():
        raise Fail(f"{where}: src {src!r} must be an existing file under {scope}")
    if not dest.startswith("~/") or not under(home / dest[2:], home):
        raise Fail(f"{where}: dest must start with ~/ and resolve under the home dir")
    if "platform" in raw and not isinstance(raw["platform"], str):
        raise Fail(f"{where}: platform must be a string")
    if "register" in raw:
        validate_register(raw["register"], where)


def validate_env(raw, where):
    text_field(raw, "name", where)
    if not isinstance(raw.get("value"), str):
        raise Fail(f"{where}: value must be a string")
    if raw.get("platform") != "win32":
        raise Fail(f'{where}: an env item is Windows-only and must set platform to "win32"')


def validate_setting(raw, where):
    text_field(raw, "key", where)
    if "value" not in raw:
        raise Fail(f"{where}: a setting item needs a value")
    if raw.get("store", "settings") not in STORES:
        raise Fail(f"{where}: store must be one of {', '.join(STORES)}")


def validate_local(raw, where):
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise Fail(f"{where}: entries must be a non-empty list")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "reason"} \
                or not all(isinstance(entry[key], str) for key in entry):
            raise Fail(f"{where}: each entry must be an object with string name and reason")


def validate_item(raw, source, scope, home):
    if not isinstance(raw, dict):
        raise Fail(f"{source}: an item must be a JSON object")
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise Fail(f"{source}: an item needs a non-empty string id")
    where = f"{source} item {item_id!r}"
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in KIND_FIELDS:
        raise Fail(f"{where}: kind must be one of {', '.join(KIND_FIELDS)} (got {kind!r})")
    unknown = set(raw) - KIND_FIELDS[kind] - COMMON_FIELDS
    if unknown:
        raise Fail(f"{where}: unknown field(s) for kind {kind}: {', '.join(sorted(unknown))}")
    if "note" in raw and not isinstance(raw["note"], str):
        raise Fail(f"{where}: note must be a string")
    if kind == "file":
        validate_file(raw, where, scope, home)
    elif kind == "env":
        validate_env(raw, where)
    elif kind == "setting":
        validate_setting(raw, where)
    else:
        validate_local(raw, where)
    return raw


def load_items(scope, home):
    items, origin = [], {}
    for name in MANIFESTS:
        path = scope / name
        if not path.exists():
            if name == MANIFESTS[0]:
                raise Fail(f"{name}: not found in {scope}")
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise Fail(f"{name}: not valid JSON: {exc}")
        if not isinstance(doc, dict) or set(doc) != {"version", "items"} \
                or type(doc["version"]) is not int or doc["version"] != 1 or not isinstance(doc["items"], list):
            raise Fail(f'{name}: top level must be {{"version": 1, "items": [...]}}')
        for raw in doc["items"]:
            item = validate_item(raw, name, scope, home)
            if item["id"] in origin:
                raise Fail(f"{name} item {item['id']!r}: id is also in {origin[item['id']]}")
            origin[item["id"]] = name
            items.append(item)
    return items


def fold(data):
    return data.replace(b"\r\n", b"\n")


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        _replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def skipped(item):
    return "platform" in item and item["platform"] != PLATFORM


def settings_file(home):
    return home / ".claude" / "settings.json"


def load_settings(home):
    path = settings_file(home)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise Fail(f"settings.json: not valid JSON: {exc}")
    if not isinstance(doc, dict):
        raise Fail("settings.json: top level must be an object")
    hooks = doc.get("hooks", {})
    if not isinstance(hooks, dict):
        raise Fail("settings.json: hooks must be an object")
    for event, groups in hooks.items():
        if not isinstance(groups, list) or not all(
                isinstance(group, dict) and isinstance(group.get("hooks", []), list) for group in groups):
            raise Fail(f"settings.json: hooks.{event} must be a list of groups, each with a hooks list")
    return doc


def store_path(store, home, repo):
    return {"settings": settings_file(home), "local": repo / ".claude" / "settings.local.json",
            "claude-json": home / ".claude.json"}[store]


def store_doc(store, home, repo, settings):
    """The store's top-level object, None when its file is absent, UNREADABLE for a file that is not a JSON object.
    ~/.claude.json holds credentials and is rewritten by running sessions: it is only ever read, one key at a time."""
    if store == "settings":
        return settings
    path = store_path(store, home, repo)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        doc = None
    if not isinstance(doc, dict):
        if store == "local":
            raise Fail(f"{path.name}: not a readable JSON object; fix it before install")
        return UNREADABLE
    return doc


def setting_value(item, repo):
    """The item's value, with `{repo}` replaced by the checkout path and the result normalized as a native path."""
    value = item["value"]
    if isinstance(value, str) and "{repo}" in value:
        return str(Path(value.replace("{repo}", repo.as_posix())))
    return value


def hook_commands(settings, event):
    if settings is None:
        return []
    groups = settings.get("hooks", {}).get(event, [])
    return [hook.get("command") for group in groups for hook in group.get("hooks", []) if isinstance(hook, dict)]


def substitute_dest(text, dest):
    path = dest.as_posix()
    return text.replace("{dest}", f'"{path}"' if " " in path else path)


def resolve_value(value, dest):
    if isinstance(value, str):
        return substitute_dest(value, dest)
    if isinstance(value, dict):
        return {key: resolve_value(part, dest) for key, part in value.items()}
    if isinstance(value, list):
        return [resolve_value(part, dest) for part in value]
    return value


def register_status(item, home, settings):
    reg, dest = item["register"], dest_path(item, home)
    if "hook" in reg:
        command = substitute_dest(reg["hook"]["command"], dest)
        return "registered" if command in hook_commands(settings, reg["hook"]["event"]) else "unregistered"
    if settings is None or reg["setting"] not in settings:
        return "unregistered"
    return "registered" if settings[reg["setting"]] == resolve_value(reg["value"], dest) else "register-differs"


def add_registration(doc, item, dest):
    reg = item["register"]
    if "hook" in reg:
        hook = reg["hook"]
        group = {"hooks": [{"type": "command", "command": substitute_dest(hook["command"], dest)}]}
        if "matcher" in hook:
            group = {"matcher": hook["matcher"], **group}
        doc.setdefault("hooks", {}).setdefault(hook["event"], []).append(group)
    else:
        doc[reg["setting"]] = resolve_value(reg["value"], dest)


def registration_line(item, dest):
    reg = item["register"]
    if "hook" in reg:
        hook = reg["hook"]
        matcher = f" matcher {hook['matcher']}" if "matcher" in hook else ""
        return f"registers: hook {hook['event']}{matcher} -> {substitute_dest(hook['command'], dest)}"
    return f"registers: settings {reg['setting']} = {json.dumps(resolve_value(reg['value'], dest))}"


def inspect_setting(item, home, repo, settings, row):
    store = item.get("store", "settings")
    key, wanted = item["key"], json.dumps(setting_value(item, repo), sort_keys=True)
    path = store_path(store, home, repo)
    doc = store_doc(store, home, repo, settings)
    if doc is UNREADABLE:
        row["status"] = "unreadable"
        row["detail"] = [f"{path.name} is not a readable JSON object right now; nothing is changed"]
    elif doc is None or key not in doc:
        if store == "claude-json":
            row["status"] = "manual"
            row["detail"] = [f"set {key} = {wanted} in the app; this tool never writes {path.name}"]
        else:
            row["status"] = "unset"
            row["detail"] = [f"sets {path.name} {key} = {wanted}"]
    elif json.dumps(doc[key], sort_keys=True) == wanted:
        row["status"] = "set"
    else:
        row["status"] = "differs"
        row["detail"] = [f"the home value stays; the manifest wants {key} = {wanted}"]


def inspect(items, home, scope, settings, repo=REPO):
    rows = []
    for item in items:
        row = {"id": item["id"], "kind": item["kind"], "note": item.get("note"),
               "status": None, "register": None, "detail": []}
        if item["kind"] == "local":
            row["status"] = "local"
        elif skipped(item):
            row["status"] = "skipped-platform"
        elif item["kind"] == "setting":
            inspect_setting(item, home, repo, settings, row)
        elif item["kind"] == "env":
            row["status"] = "set" if get_user_env(item["name"]) else "unset"
            if row["status"] == "unset":
                row["detail"] = [f"sets user env {item['name']}={item['value']}"]
        elif item["kind"] == "file":
            template, dest = scope / item["src"], dest_path(item, home)
            copy = f'copy: cp "{template.as_posix()}" "{dest.as_posix()}"'
            if not dest.exists():
                row["status"] = "missing"
                row["detail"] = [copy]
            elif fold(dest.read_bytes()) == fold(template.read_bytes()):
                row["status"] = "same"
            else:
                row["status"] = "drift"
                row["detail"] = [f"adopt: python3 .claude/tools/user_scope.py adopt {item['id']}",
                                 copy + " (overwrites the home edit)"]
            if "register" in item:
                row["register"] = register_status(item, home, settings)
                if row["register"] == "unregistered":
                    row["detail"].append(registration_line(item, dest))
        rows.append(row)
    return rows


def is_red(row):
    return row["status"] in RED or row["register"] in RED


def render(rows):
    lines = []
    for row in rows:
        register = f"  (register: {row['register']})" if row["register"] else ""
        lines.append(f"{row['status']:<18} {row['id']}{register}")
        lines.extend(f"    {line}" for line in row["detail"])
    return lines


def serialize_settings(doc):
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_local_keys(path, additions):
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    backup = path.with_name(path.name + ".pre-user-scope")
    if path.exists() and not backup.exists():
        write_atomic(backup, path.read_bytes())
    doc.update(additions)
    write_atomic(path, serialize_settings(doc))


def install(items, home, scope, repo=REPO):
    settings = load_settings(home)
    rows = inspect(items, home, scope, settings, repo)
    actions = []
    for item, row in zip(items, rows):
        if row["status"] == "missing":
            write_atomic(dest_path(item, home), (scope / item["src"]).read_bytes())
            actions.append({"id": row["id"], "action": "copied"})
        elif row["status"] == "unset" and item["kind"] == "env":
            set_user_env(item["name"], item["value"])
            actions.append({"id": row["id"], "action": "env-set"})
    local = [item for item, row in zip(items, rows)
             if item["kind"] == "setting" and row["status"] == "unset" and item.get("store") == "local"]
    if local:
        write_local_keys(store_path("local", home, repo), {item["key"]: setting_value(item, repo) for item in local})
        actions += [{"id": item["id"], "action": "registered"} for item in local]
    pending = [item for item, row in zip(items, rows)
               if row["register"] == "unregistered"
               or (item["kind"] == "setting" and row["status"] == "unset" and item.get("store", "settings") == "settings")]
    if pending:
        path = settings_file(home)
        doc = settings if settings is not None else {}
        backup = path.with_name("settings.json.pre-user-scope")
        if settings is not None and not backup.exists():
            write_atomic(backup, path.read_bytes())
        for item in pending:
            if item["kind"] == "setting":
                doc[item["key"]] = setting_value(item, repo)
            else:
                add_registration(doc, item, dest_path(item, home))
            actions.append({"id": item["id"], "action": "registered"})
        write_atomic(path, serialize_settings(doc))
    return actions


def powershell(script):
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
    except subprocess.TimeoutExpired:
        raise Fail("powershell did not finish within 60 seconds (timed out)")
    if done.returncode != 0:
        raise Fail(f"powershell exited {done.returncode}: {done.stderr.strip()[:200]}")
    return done.stdout.rstrip("\r\n")


def ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


def get_user_env(name):
    return powershell(f"[Environment]::GetEnvironmentVariable({ps_quote(name)}, 'User')") or None


def set_user_env(name, value):
    powershell(f"[Environment]::SetEnvironmentVariable({ps_quote(name)}, {ps_quote(value)}, 'User')")


SECRET_SHAPES = [
    ("sk- key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}")),
    ("AWS access key", re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])")),
    ("GitHub token", re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{20,}")),
    ("Slack token", re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("PEM block", re.compile(r"-----BEGIN")),
]
ASSIGNMENT = re.compile(r"(?<![A-Za-z0-9_])([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)[\"']?\s*[=:]\s*(.*)$")
PLACEHOLDER = "<placeholder>"


def identity_checks(home):
    forms = dict.fromkeys([str(home), str(home).replace("\\", "\\\\"), home.as_posix()])
    checks = [("machine home path", re.compile("|".join(re.escape(form) for form in forms), re.IGNORECASE))]
    if home.name:
        checks.append(("user name", re.compile(r"(?<![A-Za-z0-9])" + re.escape(home.name) + r"(?![A-Za-z0-9])",
                                               re.IGNORECASE)))
    return checks


def value_of(text):
    return text.strip().rstrip(",").strip().strip("\"'").strip()


def scan_lines(data, home):
    checks = identity_checks(home) + SECRET_SHAPES
    findings = []
    for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        findings.extend(f"line {number}: {family}" for family, pattern in checks if pattern.search(line))
        assigned = ASSIGNMENT.search(line)
        if assigned and value_of(assigned.group(2)) not in ("", PLACEHOLDER):
            findings.append(f"line {number}: {assigned.group(1)} is assigned a value other than {PLACEHOLDER}")
    return findings


def lint(items, home, scope):
    findings = []
    for name in MANIFESTS:
        if (scope / name).is_file():
            findings.extend(f"{name}: {finding}" for finding in scan_lines((scope / name).read_bytes(), home))
    for src in sorted({item["src"] for item in items if item["kind"] == "file"}):
        findings.extend(f"{src}: {finding}" for finding in scan_lines((scope / src).read_bytes(), home))
    return findings


def adopt(items, home, scope, item_id):
    if item_id is None:
        raise Fail("adopt needs an item id")
    item = next((candidate for candidate in items if candidate["id"] == item_id), None)
    if item is None:
        raise Fail(f"no item {item_id!r}")
    if item["kind"] != "file":
        raise Fail(f"item {item_id!r} is kind {item['kind']} and has no template to adopt into")
    template, dest = scope / item["src"], dest_path(item, home)
    if not dest.exists():
        raise Fail(f"item {item_id!r}: no home file at {dest.as_posix()}")
    data = dest.read_bytes()
    if fold(data) == fold(template.read_bytes()):
        raise Fail(f"item {item_id!r}: home file already matches the template")
    findings = scan_lines(data, home)
    if findings:
        print("\n".join(f"home file {dest.as_posix()}: {finding}" for finding in findings))
        print(f"adopt refused for {item_id!r}: the home file has findings; the template is unchanged",
              file=sys.stderr)
        return 1
    write_atomic(template, fold(data))
    print(f"adopted {item_id}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "install", "adopt", "lint"])
    parser.add_argument("item_id", nargs="?")
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--scope-dir", default=str(SCOPE_DEFAULT))
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_intermixed_args(argv)  # `adopt --home H item` must bind item_id on every Python
    home = Path(os.path.abspath(args.home))
    scope = Path(os.path.abspath(args.scope_dir))
    repo = Path(os.path.abspath(args.repo))
    try:
        items = load_items(scope, home)
        if args.command == "adopt":
            return adopt(items, home, scope, args.item_id)
        if args.command == "lint":
            findings = lint(items, home, scope)
            print("\n".join(findings) if findings else "lint ok")
            return 1 if findings else 0
        if args.command == "install":
            actions = install(items, home, scope, repo)
            if args.json:
                print(json.dumps({"actions": actions}, indent=2))
            else:
                print("\n".join(f"{a['action']} {a['id']}" for a in actions) or "nothing to install")
            return 0
        rows = inspect(items, home, scope, load_settings(home), repo)
        if args.json:
            print(json.dumps({"items": rows}, indent=2))
        else:
            print("\n".join(render(rows)))
        return 1 if any(is_red(row) for row in rows) else 0
    except (Fail, OSError) as exc:
        print(f"user_scope: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
