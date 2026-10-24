#!/usr/bin/env python3
"""The one resolver of a dispatched job's tool profile, and the one walk of the MCP registrations.

`guards/tool_profiles.json` is the sole defaults table: a shape maps to its built-ins and its
logical MCP server names. Every consumer reads it here — `scripts/lib/sidecar_common.sh` for all
four launchers, `tools/sidecar_fanout.py` for forwarded deltas, `hooks/workflow_provider_guard.py`
for Workflow jobs — and none keeps a second copy of the defaults.

Logical names (`semantic-search`, `ai-worker`, `godot`) are resolved to the keys the active user,
project and plugin config registers (`ai-worker`, `plugin:semantic-search:semantic-search`),
because that is what `--mcp-config` and a launcher's strict config must name. An unresolvable
server is an error, not a warning: a strict config that quietly drops a server runs the job on an
inventory nobody chose.

CLI:
  tool_profiles.py resolve --shape S [--tools CSV] [--add a,b] [--remove c,d]
                           [--base-builtins CSV] [--config-dir D] [--workdir W]
                           [--field profile|builtins|mcpSpec|mcpConfig|spec] [--brief FILE]...
  tool_profiles.py mcp-config <CSV or name list> [config-dir]   # --mcp-config's own builder
  tool_profiles.py table
  tool_profiles.py agents [--write] [--check] [--table P] [--agents-dir D]
  tool_profiles.py command-coverage [<command.md>...] [--include <path>...]

`--brief` (repeatable) checks a profile against the built-ins a command or file write the brief or a
file it points to names, or the `allowed-tools` grants of a slash command it tells the child to run
(`brief_requirements`). A launcher passes `--widen`: the profile then gains each missing built-in and
says so on stderr. Without `--widen` (the lint path) every gap refuses, exit 2. With or without it,
exit 2 also refuses what no delta can fix: a removed tool
the brief needs, or a nested fan-out. A command granting Workflow or Agent is a nested fan-out, and its
refusal points at orchestration §0: the parent dispatches those lenses. Quoted data is never
scanned: a command inside a `Workflow({...})` or `args: {...}` object, a blockquote or a task-list
item is text the child judges, not an instruction it runs.

`command-coverage` audits each command's body against its own `allowed-tools` line
(`command_coverage`) and exits 1 when any invoked tool is not granted. `allowed-tools`
pre-approves tools; it never bounds the actions inside them.

`agents` renders one `.claude/agents/<agentType>.md` per row that declares `agentType`: a generated
agent type carrying exactly that row's tools, so a Workflow engine can launch a delegate on the
shape's inventory. The files are generated from the table and never hand-edited. Without `--write`
the command exits 1 when a rendered file differs from disk; `--check` only suppresses the preview.
"""
import argparse
import fnmatch
import json
import os
import re
import sys

import plugin_registry

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(_ROOT)
TABLE_PATH = os.path.join(_ROOT, "guards", "tool_profiles.json")
AGENTS_DIR = os.path.join(_ROOT, "agents")

MCP_PREFIX = "mcp:"
LEGACY_MCP_PREFIX = "mcp__"
MODES = ("shape-default", "delta", "legacy-replace", "task-base")
ENFORCEMENT = "strict"

# Orchestration built-ins an ordinary dispatch never gets (owner rule 2026-09-24). The CLI can grant
# them: `claude -p --tools Workflow,Agent,Read` lists Workflow and Task in its init event (2.1.281).
# A shape row that lists one carries it (the benchmark score driver's one Workflow call); no -t,
# delta or arm task row adds one.
RESERVED_BUILTINS = ("Workflow", "Agent")


def norm(name):
    """The registration key comparison form, shared with the config walk."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def load_table(path=None):
    """The parsed, validated defaults table. Raises ValueError on any structural defect."""
    with open(path or TABLE_PATH, encoding="utf-8") as handle:
        table = json.load(handle)
    shapes = table.get("shapes")
    if not isinstance(shapes, dict) or not shapes:
        raise ValueError("table has no shapes")
    for name, row in shapes.items():
        if not isinstance(row, dict):
            raise ValueError("shape %r is not an object" % name)
        builtins = row.get("builtins")
        if builtins is not None and not (isinstance(builtins, list) and builtins):
            raise ValueError("shape %r: builtins must be a non-empty list or null" % name)
        servers = row.get("mcpServers")
        if not isinstance(servers, list):
            raise ValueError("shape %r: mcpServers must be a list" % name)
        agent_type = row.get("agentType")
        if agent_type is not None and not (isinstance(agent_type, str)
                                           and re.fullmatch(r"[a-z][a-z0-9-]*", agent_type)):
            raise ValueError("shape %r: agentType must be a lowercase-hyphenated name" % name)
        if agent_type is not None and builtins is None:
            raise ValueError("shape %r: an agentType row needs its own built-ins" % name)
    for key in ("requiredReadTools", "canonicalOrder"):
        if not isinstance(table.get(key), list) or not table[key]:
            raise ValueError("table has no %s" % key)
    if table.get("defaultShape") not in shapes:
        raise ValueError("defaultShape %r is not a shape" % table.get("defaultShape"))
    return table


def split_csv(value):
    """The non-empty, trimmed entries of a CSV. `""` gives []."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def parse_tools_csv(csv):
    """(mode, add, remove) for a `-t` value.

    `""` is `shape-default`: the caller asserted nothing, so the shape's own row applies. A CSV
    where every entry carries a sign is a `delta`. A CSV with no signs is the old absolute
    `--allowedTools` list, kept for old callers as `legacy-replace`. Mixing the two grammars in
    one CSV is the ambiguous input the caller has to resolve, so it raises.
    """
    entries = split_csv(csv)
    if not entries:
        return "shape-default", [], []
    signed = [e.startswith("+") or e.startswith("-") for e in entries]
    if all(signed):
        add = [e[1:].strip() for e in entries if e.startswith("+")]
        remove = [e[1:].strip() for e in entries if e.startswith("-")]
        return "delta", add, remove
    if any(signed):
        raise ValueError("mixed signed and unsigned -t: %s. Use all-signed for a delta "
                         "(+Bash,-Grep,+mcp:godot) or all-unsigned for a legacy replacement." % csv)
    return "legacy-replace", [], []


def tools_delta(raw):
    """Signed string/list or structured add/remove for a Workflow or fan-out job.

    Only launcher `-t` has the legacy absolute-list grammar. An explicit job `tools` value that
    cannot be read as a delta is an error, never an empty profile.
    """
    if raw is None:
        return [], []
    if isinstance(raw, (str, list)):
        if isinstance(raw, list) and not all(isinstance(item, str) for item in raw):
            raise ValueError("tools list needs signed strings such as ['+Bash', '-Grep']")
        entries = split_csv(raw if isinstance(raw, str) else ",".join(raw))
        if not entries or any(item[0] not in "+-" or not item[1:].strip() for item in entries):
            raise ValueError("tools needs a signed delta such as '+Bash,-Grep'; an unsigned list is not a Workflow grant")
        return ([item[1:].strip() for item in entries if item[0] == "+"],
                [item[1:].strip() for item in entries if item[0] == "-"])
    if not isinstance(raw, dict) or not set(raw) <= {"shape", "add", "remove", "base"}:
        raise ValueError("tools needs a signed string/list or structured {shape?: name, add: [...], remove: [...], base?: [...]} delta")
    out = []
    for key in ("add", "remove"):
        value = raw.get(key, [])
        if isinstance(value, str):
            value = split_csv(value)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("tools.%s needs a list or CSV of built-in names" % key)
        entries = [item.strip() for item in value]
        if any(not item or item.startswith(("+", "-")) for item in entries):
            raise ValueError("tools.%s needs bare names; use signs only in a string/list delta" % key)
        out.append(entries)
    return out[0], out[1]


def signed_csv(add, remove):
    """The `-t` a delta becomes on the command line: `+Bash,-mcp:ai-worker`."""
    return ",".join(["+%s" % a for a in (add or [])] + ["-%s" % r for r in (remove or [])])


def _builtin_entries(values):
    return [v for v in values if not v.startswith(MCP_PREFIX)]


def _server_entries(values):
    return [v[len(MCP_PREFIX):] for v in values if v.startswith(MCP_PREFIX)]


def _canonical(items, order):
    """`items` de-duplicated, in the table's canonical order, then any unknown name in input order."""
    seen, known, unknown = set(), [], []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        (known if item in order else unknown).append(item)
    return sorted(known, key=order.index) + unknown


def _legacy_spec(csv):
    """(builtins, servers) for a legacy absolute CSV: the old `-t` grammar, unchanged.

    Built-in names keep their written order and a rule specifier reduces to its tool, exactly as
    `sc_builtin_tools` reduces it; `mcp__<server>` entries name a server.
    """
    builtins, servers = [], []
    for entry in split_csv(csv):
        token = entry.split("(", 1)[0].strip()
        if not token:
            continue
        if token.startswith(LEGACY_MCP_PREFIX) and len(token) > len(LEGACY_MCP_PREFIX):
            name = token[len(LEGACY_MCP_PREFIX):].split("__", 1)[0]
            if name not in servers:
                servers.append(name)
        elif token not in builtins:
            builtins.append(token)
    return builtins, servers


# ------------------------------------------------------------------- MCP registrations

def load_registrations(config_dir="", workdir="", plugin_root=None):
    """[(registered key, server config)] in precedence order: project > .mcp.json > user > plugin.

    The one walk of the config scopes. `sc_mcp_config` and the resolver both read it, so a server
    name resolves the same way whether a launcher is building `--mcp-config` or validating a
    profile before spend.
    """
    cfg = config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or ""
    wd = workdir or os.environ.get("SC_WORKDIR") or _REPO
    home = cfg or os.path.expanduser("~")

    def load(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def same(a, b):
        return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    state = load(os.path.join(home, ".claude.json"))
    candidates = []
    for project, values in (state.get("projects") or {}).items():
        if same(project, wd):
            candidates += list((values.get("mcpServers") or {}).items())
    candidates += list((load(os.path.join(wd, ".mcp.json")).get("mcpServers") or {}).items())
    candidates += list((state.get("mcpServers") or {}).items())
    plugins_dir = (os.path.join(cfg, "plugins") if cfg
                   else os.path.join(home, ".claude", "plugins"))
    try:
        installed = plugin_registry.read(os.path.join(plugins_dir, "installed_plugins.json")).get("plugins") or {}
    except (OSError, ValueError):
        installed = {}
    for key, entries in installed.items():
        entry = next((e for e in entries if e.get("scope") in ("project", "local")
                      and same(e.get("projectPath"), wd)), None) \
            or next((e for e in entries if e.get("scope") == "user"), None)
        root = (entry or {}).get("installPath")
        if not root:
            continue
        decl = load(os.path.join(root, ".claude-plugin", "plugin.json")).get("mcpServers")
        if isinstance(decl, str):
            decl = load(os.path.join(root, decl)).get("mcpServers")
        if not isinstance(decl, dict):
            decl = load(os.path.join(root, ".mcp.json")).get("mcpServers") or {}
        root_fwd = root.replace("\\", "/")
        for name, conf in decl.items():
            conf = json.loads(json.dumps(conf).replace(
                "${CLAUDE_PLUGIN_ROOT}", root_fwd.replace('"', '\\"')))
            candidates.append(("plugin:%s:%s" % (key.split("@", 1)[0], name), conf))
    if plugin_root is not None:
        plugin_root.extend(candidates)
    seen, out = set(), []
    for key, conf in candidates:
        if norm(key) in seen:
            continue
        seen.add(norm(key))
        out.append((key, _expand_env(conf, wd)))
    return out


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# Claude Code sets this variable in a SPAWNED server's env, never in its own, so a walk that left
# `${CLAUDE_PROJECT_DIR:-.}` to `os.environ` would hand the child `.` — relative to the CHILD's
# working directory, not the repository.
_PROJECT_DIR_VAR = "CLAUDE_PROJECT_DIR"


def _expand_env(value, base=""):
    """`${VAR}` and `${VAR:-default}` expanded as Claude Code expands `.mcp.json`; an unset VAR
    with no default stays literal, so the child reports the missing variable by name.

    `base` is the directory the config was read from. For a spawned child, `CLAUDE_PROJECT_DIR` is
    a fact about the walk rather than about this process's environment, so `base` supplies it, as
    an absolute path, and wins over a planted `os.environ` value. A caller that passes no base
    keeps the environment's value and the written default; every pre-existing caller is unchanged.
    """
    project = os.path.abspath(base) if base else None

    def expand(item):
        if isinstance(item, dict):
            return {k: expand(v) for k, v in item.items()}
        if isinstance(item, list):
            return [expand(v) for v in item]
        if not isinstance(item, str):
            return item

        def sub(m):
            name, default = m.group(1), m.group(2)
            if project is not None and name == _PROJECT_DIR_VAR:
                return project
            env = os.environ.get(name)
            if env is not None:
                return env
            return default if default is not None else m.group(0)

        return _ENV_REF.sub(sub, item)

    return expand(value)


def registered_key(logical, candidates):
    """The registered key a logical server name resolves to, or None.

    A logical name matches its own registered key (`ai-worker`) or the tail of a plugin-qualified
    one (`semantic-search` -> `plugin:semantic-search:semantic-search`). First match in precedence
    order wins.
    """
    want = norm(logical)
    for key, _conf in candidates:
        seg = norm(key)
        if seg == want or seg.endswith("_" + want):
            return key
    return None


def resolve_servers(logical_names, candidates, registered=None):
    """(registered keys, {logical: registered}) or ValueError naming every unresolvable name."""
    keys, mapping, missing = [], {}, []
    for name in logical_names:
        key = registered_key(name, candidates)
        if key is None:
            missing.append(name)
            continue
        mapping[name] = key
        if key not in keys:
            keys.append(key)
    if missing:
        have = ", ".join(sorted(k for k, _ in candidates)) or "none"
        raise ValueError("MCP server(s) not registered in this config: %s (registered: %s). "
                         "A requested server the config does not define is an error, not a "
                         "server silently dropped." % (", ".join(missing), have))
    if registered is not None:
        registered.update(mapping)
    return keys, mapping


def strict_mcp_config(keys, candidates):
    """`{"mcpServers": {...}}` holding exactly `keys`, in the order given."""
    by_key = dict(candidates)
    return {"mcpServers": {k: by_key[k] for k in keys if k in by_key}}


# ------------------------------------------------------------------- resolution

def resolve(shape, tools_csv="", add=(), remove=(), base_builtins=None,
            candidates=None, table=None, registered=None, walk_mcp=True):
    """The resolved profile of one job.

    `tools_csv` is the `-t` value as written; `add`/`remove` carry a caller's structured delta
    (`tools.add`/`tools.remove` in a jobs file) and are merged with whatever `-t` named. Every
    item is a canonical built-in name or `mcp:<logical-server>`. `walk_mcp=False` resolves the
    built-ins only, leaving `mcpRegistrations` empty, for a planner whose launcher walks the config.
    """
    table = table or load_table()
    shapes = table["shapes"]
    if shape not in shapes:
        raise ValueError("unknown shape %r (legal: %s)"
                         % (shape, ", ".join(sorted(shapes))))
    row = shapes[shape]
    default_builtins = row["builtins"]
    default_servers = list(row["mcpServers"])

    mode, csv_add, csv_remove = parse_tools_csv(tools_csv)
    add = [a.strip() for a in add if a and a.strip()]
    remove = [r.strip() for r in remove if r and r.strip()]

    # `arm`'s row carries no built-ins: its task row supplies them. The unsigned `-t` a benchmark
    # cell passes IS that task row, so it becomes the base rather than a legacy replacement of a
    # base that does not exist.
    task_base = shape == "arm"
    if task_base and mode == "legacy-replace":
        mode = "task-base"
    if task_base and mode == "task-base":
        base_builtins = _legacy_spec(tools_csv)[0]
    if task_base and base_builtins is None:
        raise ValueError("shape 'arm' needs its task's built-in list before resolution: pass "
                         "-t <csv> or --base-builtins")
    add = csv_add + add
    remove = csv_remove + remove
    if mode in ("legacy-replace", "task-base") and (add or remove):
        raise ValueError("a %s -t is absolute: it cannot be combined with a tools.add/tools.remove "
                         "delta. Use the signed grammar (+Bash,-Grep,+mcp:godot)." % mode)

    for item in add + remove:
        if item.startswith(MCP_PREFIX):
            if len(item) == len(MCP_PREFIX):
                raise ValueError("empty MCP server name in %r" % item)
        elif not item:
            raise ValueError("empty tool name")
    both = sorted(set(add) & set(remove))
    if both:
        raise ValueError("named in both add and remove: %s" % ", ".join(both))
    dupes = sorted({i for i in add + remove if (add + remove).count(i) > 1})
    if dupes:
        raise ValueError("repeated item(s): %s" % ", ".join(dupes))

    add_builtins, add_servers = _builtin_entries(add), _server_entries(add)
    rm_builtins, rm_servers = _builtin_entries(remove), _server_entries(remove)

    if mode in ("legacy-replace", "task-base"):
        builtins, servers = _legacy_spec(tools_csv)
        if mode == "task-base":
            servers = list(default_servers)   # the row still owns MCP; the task row owns built-ins
    elif default_builtins is not None:
        builtins, servers = list(default_builtins), list(default_servers)
    else:
        builtins, servers = list(base_builtins), list(default_servers)

    # A removal ADDS nothing to a profile: it is a correction to that shape's own row, so it must
    # name something the row already carries (the task row, for an arm's built-ins).
    row_builtins = builtins if mode == "task-base" else (default_builtins or [])
    missing = [b for b in rm_builtins if b not in row_builtins]
    missing += ["mcp:" + s for s in rm_servers if s not in default_servers]
    if missing:
        raise ValueError("cannot remove what the shape does not carry: %s (shape row: %s)"
                         % (", ".join(sorted(missing)),
                            ", ".join(list(row_builtins) + ["mcp:" + s for s in default_servers])
                            or "nothing"))
    if mode != "task-base":
        builtins = [b for b in builtins if b not in rm_builtins]
    builtins = _canonical(builtins + add_builtins, table["canonicalOrder"])
    row_owned = default_builtins if mode in ("shape-default", "delta") else []
    ungrantable = [b for b in builtins if b in RESERVED_BUILTINS and b not in (row_owned or [])]
    if ungrantable:
        raise ValueError(
            "cannot grant %s to a dispatch: orchestration built-ins (%s) stay with the calling "
            "session. Only a shape row that lists one carries it (bench-score). Drop it, or "
            "dispatch that job natively."
            % (", ".join(ungrantable), ", ".join(RESERVED_BUILTINS)))
    servers = [s for s in servers if s not in rm_servers]
    for s in add_servers:
        if s not in servers:
            servers.append(s)

    if not builtins:
        raise ValueError("resolved built-ins are empty: the child could not read its brief")
    required = [b for b in table["requiredReadTools"] if b in builtins]
    if not required:
        raise ValueError("resolved built-ins keep none of %s: the child could not read its brief"
                         % ", ".join(table["requiredReadTools"]))

    if not walk_mcp:
        keys, mapping = [], {}
    else:
        if candidates is None:
            candidates = load_registrations()
        keys, mapping = resolve_servers(servers, candidates, registered=registered)

    profile = {
        "shape": shape,
        "builtins": builtins,
        "mcpServers": servers,
        "add": list(add),
        "remove": list(remove),
        "overrideMode": mode,
        "enforcement": ENFORCEMENT,
        "mcpRegistrations": mapping,
    }
    if row.get("agentType"):
        profile["agentType"] = row["agentType"]
    if row.get("tier"):
        profile["tier"] = row["tier"]
    return profile, tool_spec(profile, keys), keys


def tool_spec(profile, keys):
    """The `-t`-grammar spec for a resolved profile: built-ins, then `mcp__<server>` per key.

    Keys are normalized (`plugin:semantic-search:semantic-search` ->
    `plugin_semantic-search_semantic-search`), which is both the form `--allowedTools` takes as a
    server-level entry and the form `sc_mcp_config` resolves back to the registration.
    """
    return ",".join(list(profile["builtins"]) + [LEGACY_MCP_PREFIX + norm(k) for k in keys])


# ------------------------------------------------------------------- brief coverage
# A profile must cover every command and file write its brief tells the child to perform: a review
# lens told to run `git diff` on Read/Glob/Grep spends its whole run and returns blocked coverage.
# Only caller-authored text is scanned (the brief, its context files and the repository files it
# points to, one hop); engine and rail text is not.
#
# Two regions of a brief are DATA the child judges, never an instruction to it, and are dropped
# before the scan: a quoted line (`without_quoted_data`) and a `Workflow({...})`/`args: {...}`
# object (`_drop_data_regions`). The scan keeps its own fence rule — a bare fence is still
# instructions, only a ```json fence is data.

_COMMAND = (r"(?:git(?:\s+-C\s+\S+)?\s+(?:diff|show|log|blame|grep|status|ls-files|rev-parse)"
            r"|gh|python3?|node|pytest|dotnet|bash|pwsh|powershell)(?=\s|$)")
# A command stands at a code segment's start, after a shell connector, or after a `$ ` prompt.
_CODE_COMMAND = re.compile(r"(?:^|[|;&(]|\$ )\s*(?:\$\s+)?(?P<cmd>" + _COMMAND + ")")
# In prose it stands only at the line's start or after a `$ ` prompt.
_PROSE_COMMAND = re.compile(r"(?:^\s*|\$ )(?:\$\s+)?(?P<cmd>" + _COMMAND + ")")
_IMPERATIVE_COMMAND = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:run|execute|invoke)\s*:?[ \t]+`?(?P<cmd>" + _COMMAND + ")", re.I)
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_SCRIPT = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.(?:sh|ps1|py)(?=\s+\S)")
_WRITE = re.compile(r"\b(?:write|spill|save|dump)\b(?:[^.;\n]|\.(?!\s|$)){0,80}?\b(?:to|into|under)\s+"
                    r"(?P<path>`[^`\s]+`|<[^>\s]+>\S*|\S+)", re.I)
_NEGATION = re.compile(r"(?<!\bor )\b(?:not|never)\b|n't\b", re.I)  # "present or not" negates nothing
_PROHIBITION_LIST = re.compile(r"^\W*no\b", re.I)  # "No commits, no stash, no X": a clause that opens with "No"
_CLAUSE_END = re.compile(r"[.;:!?](?=\s|$)|—| -- ")
_POINTER = re.compile(r"(?:\b(?:mandate|brief) is at:?|\bread)\s+`?(?P<path>[^\s`'\"<>]+)", re.I)


# Bytes of one brief or pointed-to file the scan reads. The Workflow guard scans every job's brief
# on each call, so a brief that names a log or transcript must not set that hook's cost.
BRIEF_READ_CAP = 256 * 1024


def read_brief(path):
    """The first BRIEF_READ_CAP bytes of `path`, decoded; raises OSError when it cannot be read."""
    with open(path, "rb") as handle:
        return handle.read(BRIEF_READ_CAP).decode("utf-8", errors="replace")


def resolve_brief_path(token, root):
    """The file a brief's path token names: relative tokens, and a repository path written with one
    leading slash (`/.claude/skills/x/SKILL.md`), resolve against `root`."""
    if token.startswith(("/", "\\")) and not token.startswith(("//", "\\\\")):
        rooted = os.path.join(root, token.lstrip("/\\"))
        if os.path.isfile(rooted) or not os.path.isfile(token):
            return rooted
    return token if os.path.isabs(token) else os.path.join(root, token)


def _is_path(token):
    return ("/" in token or "\\" in token) and bool(re.search(r"\.[A-Za-z0-9]{1,6}$", token))


def _negated(line, start):
    clause = _CLAUSE_END.split(line[:start])[-1]
    return bool(_NEGATION.search(clause) or _PROHIBITION_LIST.match(clause))


# A Workflow call's args object, or any `args: {...}` literal, carries data for a script (a judged
# deliverable, a quoted diff), never an instruction to the child; so does a ```json fence.
_DATA_OPEN = re.compile(r"(?:\bWorkflow\s*\(\s*|[\"']?\bargs[\"']?\s*:\s*)\{")


def _brace_delta(fragment):
    """Net `{` minus `}` outside quoted strings."""
    depth, quote, escaped = 0, None, False
    for ch in fragment:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth


# Quoted DATA the child judges, never an instruction to it: a blockquote, a task-list item
# (`- [ ] …`) and that item's indented sub-bullets — every worklog item quoted into a brief. The
# mechanism guard's nested-fan-out check reads the same rule, which lives here because a tool never
# imports a hook.
QUOTED_DATA_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:>|[-*+]\s+\[[ xX]\]|\d+[.)]\s+\[[ xX]\])"
)


def _indent_width(line):
    return len(line) - len(line.lstrip(" \t"))


def without_quoted_data(text):
    """Blank blockquotes, task-list items and their sub-bullets, and serialized JSON records.

    A whole line parsed as an object or an array of objects is data, including its string values.
    Line structure is kept (the line is blanked, not removed), so a caller's line numbers still
    point at the source line.
    """
    out, item_indent = [], None
    for line in text.split("\n"):
        match = QUOTED_DATA_RE.match(line)
        if match:
            item_indent = _indent_width(match.group("indent"))
            out.append("")
            continue
        if item_indent is not None:
            if not line.strip() or _indent_width(line) > item_indent:
                out.append("")
                continue
            item_indent = None
        if line.lstrip().startswith(("{", "[")):
            try:
                value = json.loads(line)
            except ValueError:
                pass
            else:
                if isinstance(value, dict) or (isinstance(value, list) and all(isinstance(row, dict) for row in value)):
                    out.append("")
                    continue
        out.append(line)
    return "\n".join(out)


def _drop_data_regions(text):
    """`text` with every `Workflow({...})` / `args: {...}` brace region blanked to its closing brace.

    Line structure is kept, so the scan's line numbers still point at the source line.
    """
    out, depth = [], 0
    for line in text.split("\n"):
        if depth > 0:
            depth = max(0, depth + _brace_delta(line))
            out.append("")
            continue
        opener = _DATA_OPEN.search(line)
        if opener:
            depth = max(0, _brace_delta(line[opener.end() - 1:]))
            out.append(line[:opener.start()])
            continue
        out.append(line)
    return "\n".join(out)


# ------------------------------------------------------------------- slash commands and fan-out
# A command's `allowed-tools` frontmatter line is the one home of what running it needs, and the
# fan-out set derives from it. A brief that tells its child to run `/<name>` needs that command's
# grants; a grant of Workflow or Agent (`Task` is Agent's older name) makes it a nested fan-out,
# which no delegate profile carries (orchestration §0). Both dispatch routes read the grammar
# below: the sidecar launcher through `brief_requirements`, the native route through
# `hooks/dispatch_mechanism_guard.py`, which imports it because a tool never imports a hook.
_GRANT_ALIASES = {"Task": "Agent"}


def _profile_builtins():
    """The built-ins a profile can carry or refuse. A grant outside them (Skill, Monitor,
    SlashCommand) is nothing a shape or delta could supply, so it is never a brief requirement."""
    return set(load_table()["canonicalOrder"]) | set(RESERVED_BUILTINS)


def _commands_dir(root=None):
    """`<root>/.claude/commands`, or this checkout's when `root` has none: a sidecar workdir
    outside a checkout still runs this harness's commands."""
    if root:
        path = os.path.join(root, ".claude", "commands")
        if os.path.isdir(path):
            return path
    return os.path.join(_REPO, ".claude", "commands")


def _split_grants(value):
    """`Bash(git diff:*), Read` -> ["Bash(git diff:*)", "Read"]: commas inside parentheses stay."""
    out, depth, current = [], 0, ""
    for ch in value:
        if ch == "," and depth == 0:
            out.append(current.strip())
            current = ""
            continue
        depth += (ch == "(") - (ch == ")")
        current += ch
    out.append(current.strip())
    return [e for e in out if e]


def _frontmatter_grants(path):
    """The raw `allowed-tools` entries of a command file, or None when it has no such line."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return None
            for line in fh:
                if line.strip() == "---":
                    return None
                if line.startswith("allowed-tools:"):
                    return _split_grants(line.split(":", 1)[1])
    except OSError:
        return None
    return None


def _grant_builtin(entry):
    name = entry.split("(", 1)[0].strip()
    return _GRANT_ALIASES.get(name, name)


def command_grants(name, root=None):
    """The built-ins command `name`'s `allowed-tools` line grants, in written order.

    `Bash(<prefix>:*)` reads as Bash and `Task` as Agent; an MCP tool is not a built-in and is
    dropped. [] when the command has no line or does not exist. `root` is a repository root; its
    `.claude/commands` is read, else this checkout's.
    """
    out = []
    for entry in _frontmatter_grants(os.path.join(_commands_dir(root), name + ".md")) or []:
        builtin = _grant_builtin(entry)
        if builtin and not builtin.startswith(LEGACY_MCP_PREFIX) and builtin not in out:
            out.append(builtin)
    return out


def _command_names(root=None):
    try:
        return sorted(f[:-3] for f in os.listdir(_commands_dir(root)) if f.endswith(".md"))
    except OSError:
        return []


# The dispatch grammar. A verb is a whole word outside any path or identifier (`part_execute.md`,
# `sidecar-dispatch`, `sidecar/dispatch work` name things) in imperative position: line/sentence
# start, after an addressing word (`you need to`, `you must`), or after a step lead (`next,`,
# `then`). A word glued to `:<id>` is a key (`run:<id>` heads a ledger unit row), never a verb.
# Descriptive proximity never matches: the target alone is a noun a brief may cite, and a
# verb in a negated clause (`do not run it`) is no instruction.
_ENGINE_TARGET = r"review_fanout\.js|dispatch\.js|dispatch_chains\.js|\bWorkflow\(\s*\{"
_CLAUSE_LEAD = (r"[:)]\s+|\b(?:you\s+(?:need\s+to\s+|to\s+)?|"
                r"(?:then|please|now|must|should|will|otherwise)\s*,?\s+)")
VERB_LEAD = r"(?:^[ \t]*(?:[-*+]\s+)?(?:next\s*,\s*)?|[.;!?]\s+|" + _CLAUSE_LEAD + r")"
VERB_WORD = r"(?<![\w/\-])(?:dispatch|fan[- ]?out|spawn|launch|invoke|execute|run|use)\b(?!:\S)"
DISPATCH_VERB = VERB_LEAD + VERB_WORD
# A verb and its target share one sentence: the gap between them crosses no sentence end, `;` or
# dash, and a verb after its target stands in imperative position inside that sentence ("then
# execute it"), never as a noun ("a /x run records it").
_SAME_SENTENCE = r"(?:(?![.;!?](?:\s|$)|—| -- )[^\n])"
_MENTION = re.compile(r"(?<![\w.\-/])/([A-Za-z0-9_]+)\b(?!/)|commands/([A-Za-z0-9_]+)\.md")
# DATA the reader judges, never an instruction to it: fenced blocks (a diff of a command file, a
# quoted brief) and the quoted lines `without_quoted_data` blanks. A lens over harness hunks
# legitimately carries "run X" lines.
FENCED_RE = re.compile(r"```.*?```", re.S)
# An engine file inside a code span that a shell tool opens (`node --check …/review_fanout.js`,
# `git diff -- …/dispatch.js`) is a file being checked or read, never a dispatch.
SHELL_SPAN_RE = re.compile(r"`\s*(?:node|python3?|git|cat|grep|rg|sed|head|tail|wc|ls|diff)\b[^`\n]*$")
# A label of two or three words whose line holds only paths after its colon (`- Dispatch
# verification: tools/x.py, workflows/dispatch.js`) lists files; its first word is a noun.
PATH_LIST_LABEL_RE = re.compile(r"^[ \t]*(?:[-*+]\s+)?\w+(?:[ \t]+\w+){1,2}:[ \t]+"
                                r"`?[\w./\-]+`?(?:[ \t]*,[ \t]*`?[\w./\-]+`?)*[ \t]*$")


def instruction_text(text):
    """The instruction half of a brief: fenced blocks blanked, quoted data blanked, lines kept."""
    return without_quoted_data(FENCED_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text))


def _grammar(names, engines):
    """(the dispatch regex, the target regex) recognising the commands `names`, and the engines
    when `engines` is set."""
    alternation = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True)) or r"(?!)"
    commands = "(?:" + alternation + ")"
    engine = (_ENGINE_TARGET + "|") if engines else ""
    target = (r"(?:" + engine + r"commands/" + commands + r"\.md|(?<![\w.\-/])/"
              + commands + r"\b(?!/))")
    nested = re.compile(
        r"(?im)(?:" + DISPATCH_VERB + _SAME_SENTENCE + r"{0,160}?" + target
        + r"|" + target + _SAME_SENTENCE + r"{0,200}?(?:" + _CLAUSE_LEAD
        + r"|\band\s+)(?<![\w/\-])(?:execute|run|follow)\b[^\n]{0,40}\b"
        r"(?:it|them|step by step|the procedure|this)\b)")
    named = re.compile(r"(?:" + engine + r"commands/(?P<md>" + commands + r")\.md"
                       r"|(?<![\w.\-/])/(?P<slash>" + commands + r")\b(?!/))", re.I)
    return nested, named


def _shell_argument(text, match, target_re):
    last = None
    for t in target_re.finditer(match.group(0)):
        last = t
    if last is None:
        return False
    line_start = text.rfind("\n", 0, match.start()) + 1
    before = text[line_start:match.start() + last.start()]
    return before.count("`") % 2 == 1 and bool(SHELL_SPAN_RE.search(before))


def _path_list_label(text, match):
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.start())
    return bool(PATH_LIST_LABEL_RE.match(text[line_start:line_end if line_end >= 0 else len(text)]))


_VERB_ANY = re.compile(r"(?<![\w/\-])(?:dispatch|fan[- ]?out|spawn|launch|invoke|execute|run|use|follow)\b", re.I)


def _negated_dispatch(text, match, target_re):
    """True when the match's dispatch verb sits in a negated clause ("do not run it")."""
    body = match.group(0)
    first = target_re.search(body)
    if first is None:
        return False
    verbs = list(_VERB_ANY.finditer(body))
    before = [v for v in verbs if v.end() <= first.start()]
    after = [v for v in verbs if v.start() >= first.end()]
    verb = before[0] if before else (after[0] if after else None)
    if verb is None:
        return False
    at = match.start() + verb.start()
    line_start = text.rfind("\n", 0, at) + 1
    return _negated(text[line_start:at], at - line_start)


def nested_fanout_hits(text, root=None, fanout=True):
    """Each place `text`'s instruction half tells its reader to dispatch through an engine or to
    run a command, as `{"tools", "command", "line", "snippet"}`.

    `tools` is what the hit needs: Workflow for an engine target (`command` None), else the
    command's `command_grants` that a profile can carry. With `fanout`, only engines and commands
    granting Workflow or Agent are recognised; without it, only commands granting neither. The two
    sets run as separate passes so a command of one kind never hides a later one of the other on
    the same line. A text that mentions no command reads no command file.
    """
    body = instruction_text(text)
    mentioned = {slash or md for slash, md in _MENTION.findall(body)}
    carried = _profile_builtins() if mentioned else set()
    grants = {}
    for name in mentioned:
        needed = [g for g in command_grants(name, root) if g in carried]
        if needed and any(g in RESERVED_BUILTINS for g in needed) == fanout:
            grants[name] = needed
    if not grants and not fanout:
        return []
    nested, named = _grammar(grants, engines=fanout)
    hits = []
    for m in nested.finditer(body):
        if (_shell_argument(body, m, named) or _negated_dispatch(body, m, named)
                or _path_list_label(body, m)):
            continue
        targets = list(named.finditer(m.group(0)))
        target = targets[-1] if targets[-1].end() == len(m.group(0)) else targets[0]
        command = target.group("md") or target.group("slash")
        hits.append({"tools": grants[command] if command else ["Workflow"], "command": command,
                     "line": body.count("\n", 0, m.start() + target.start()) + 1,
                     "snippet": m.group(0)[:120].replace("\n", " ")})
    return hits


def nested_fanout_hit(text, root=None):
    """The first nested fan-out instruction's snippet in `text`, or None."""
    hits = nested_fanout_hits(text, root)
    return hits[0]["snippet"] if hits else None


def _scan(source, text, out, commands=None):
    """Append one match per tool per line of `text` to `out`. With `commands`, also append
    `(line, command text)` for each Bash hit, so a grant's prefix can be checked against it."""
    fenced = data_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            data_fence = fenced and stripped[3:].strip().lower().startswith("json")
            continue
        if data_fence:
            continue
        hits = []
        if fenced:
            hits += [("Bash", m.start("cmd"), len(line)) for m in _CODE_COMMAND.finditer(line)]
        else:
            hits += [("Bash", m.start("cmd"), len(line)) for m in _PROSE_COMMAND.finditer(line)]
            hits += [("Bash", m.start("cmd"), len(line)) for m in _IMPERATIVE_COMMAND.finditer(line)]
            for m in _WRITE.finditer(line):
                if _is_path(m.group("path").strip("`<>").rstrip(".,;:)")):
                    hits.append(("Write", m.start(), m.end()))
        # A backtick span is scanned the same way fenced or not: a mandate sentence can quote a
        # command inside a fenced example without the fence's own shell text being one.
        for span in _CODE_SPAN.finditer(line):
            hits += [("Bash", span.start(2) + m.start("cmd"), span.end(2))
                     for m in _CODE_COMMAND.finditer(span.group(2))]
            script = _SCRIPT.match(span.group(2))
            if script:
                hits.append(("Bash", span.start(2) + script.start(), span.end(2)))
        if commands is not None:
            commands.extend((number, line[start:end].strip().rstrip("`.,;:"))
                            for tool, start, end in hits if tool == "Bash" and not _negated(line, start))
        seen = set()
        for tool, start, _end in sorted(hits, key=lambda h: h[1]):
            if tool in seen:
                continue
            seen.add(tool)
            out.append({"tool": tool, "source": source, "line": number, "text": line.strip(),
                        "negated": _negated(line, start)})


def _command_matches(source, text, root, out):
    """Append one match per tool per slash-command or engine hit in `text` to `out`."""
    lines = text.split("\n")
    for fanout in (True, False):
        for hit in nested_fanout_hits(text, root, fanout):
            for tool in hit["tools"]:
                row = {"tool": tool, "source": source, "line": hit["line"],
                       "text": lines[hit["line"] - 1].strip(), "negated": False}
                if row not in out:
                    out.append(row)


_UNIFIED_DIFF_RE = re.compile(r"\A\s*(?:diff --git |--- \S[^\n]*\n\+\+\+ )")


def _is_unified_diff(text):
    return bool(_UNIFIED_DIFF_RE.match(text))


def brief_requirements(texts, root=None):
    """`{"matches": [...], "missing": [...]}` for caller-authored brief texts.

    `texts` is a list of `(source, text)` pairs. Every repository path a text tells the agent to
    read (`mandate is at: <path>`, `brief is at: <path>`, `read <path>`) is scanned too, one hop
    deep, relative to `root` (the repository root by default), limited to the `§` sections the
    pointer's clause names when it names any; a path that cannot be read is listed
    in `missing`. Each read stops at BRIEF_READ_CAP bytes. A match is one tool per line: `{tool,
    source, line, text, negated}`. A match in a negated clause is reported with `negated`; quoted
    data is not scanned at all — a `Workflow({...})`/`args: {...}` region and a quoted line (a
    blockquote, a task-list item and its sub-bullets) are text the child judges, not instructions.
    So is a unified diff, given or pointed at: a text that opens with a diff header adds nothing.
    A slash command the text tells the child to run adds that command's grants a profile can carry
    (`nested_fanout_hits`), and an engine dispatch adds Workflow. A pointed-to skill, reference or
    rule file (`_DOCTRINE_PATH`) is reading material: its own command and engine mentions are
    not orders, while a text handed over directly and a brief's own sentences still count.
    """
    direct = {source for source, _text in texts}
    texts, missing = _with_pointed(texts, root)
    matches = []
    for source, text in texts:
        _scan(source, _drop_data_regions(without_quoted_data(text)), matches)
        if source in direct or not _is_doctrine_path(source):
            _command_matches(source, text, root or os.path.dirname(_ROOT), matches)
    return {"matches": matches, "missing": missing}


_DOCTRINE_PATH = re.compile(r"(?:^|/)\.claude/(?:skills|reference|rules)/")


def _is_doctrine_path(source):
    return bool(_DOCTRINE_PATH.search(str(source).replace("\\", "/")))


def _with_pointed(texts, root=None):
    """`(texts plus every file they point to, unreadable pointer tokens)`, unified diffs dropped."""
    root = root or os.path.dirname(_ROOT)
    texts = [(s, t) for s, t in texts if not _is_unified_diff(t)]
    pointed, missing = [], []
    for _source, text in list(texts):
        for m in _POINTER.finditer(_drop_data_regions(without_quoted_data(text))):
            token = m.group("path").rstrip(".,;:)")
            if not _is_path(token):
                continue
            if token in [p for p, _ in pointed] or token in missing:
                continue
            try:
                body = read_brief(resolve_brief_path(token, root))
            except OSError:
                missing.append(token)
                continue
            if not _is_unified_diff(body):
                pointed.append((token, _named_sections(body, _section_refs(text, m.end()))))
    return texts + pointed, missing


_SECTION_REF = re.compile(r"§\s*([0-9]+(?:[.–-][0-9]+)?|[A-Za-z][\w-]*)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _section_refs(text, end):
    """The `§` sections a pointer names in its own clause: `read <path> §7 and §8` gives 7 and 8."""
    clause = re.split(r"`|\n|\. |;", text[end:end + 120].lstrip("`"), maxsplit=1)[0]
    return _SECTION_REF.findall(clause)


def _named_sections(body, refs):
    """Only the sections `refs` name, each from its heading to the next heading at its level or
    above; the whole body when there are no refs or none matches, so an unknown name scans more."""
    if not refs:
        return body
    lines, keep, level = body.split("\n"), [], None
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            depth, title = len(heading.group(1)), heading.group(2).strip()
            if level is not None and depth <= level:
                level = None
            if level is None and any(_heading_names(title, ref) for ref in refs):
                level = depth
        if level is not None:
            keep.append(line)
    return "\n".join(keep) if keep else body


def _heading_names(title, ref):
    if ref[0].isdigit():
        return re.match(r"%s(?:[.)]|\s|$)" % re.escape(ref), title) is not None
    return title.lower().startswith(ref.lower())


# ------------------------------------------------------------------- command grant audit
_WORKFLOW_CALL = re.compile(r"^\s*Workflow\(\s*\{", re.M)


def _body(text):
    """`text` with its frontmatter blanked, lines kept."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    return "\n" * text.count("\n", 0, end + 4) + text[end + 4:]


def _bash_covered(command, entries):
    for entry in entries:
        if entry == "Bash":
            return True
        if entry.startswith("Bash(") and entry.endswith(")"):
            pattern = entry[5:-1]
            pattern = pattern[:-2] + "*" if pattern.endswith(":*") else pattern
            if fnmatch.fnmatchcase(command, pattern):
                return True
    return False


def command_coverage(path, includes=(), root=None):
    """`{command, grants, gaps, lines}` for one command file: the tools its body invokes against
    its `allowed-tools` grants.

    Invoked tools are `brief_requirements`' matches (no pointer hop; `includes` names each linked
    procedure file to scan with it) plus Workflow for a line-start `Workflow({` call. A slash
    command the body runs adds only its Workflow or Agent grant: running a fan-out command makes
    this command a fan-out too, while its other tools are pre-approved by its own line. `Bash` covers
    every command and `Bash(<prefix>:*)` a command starting with `<prefix>`. A command with no
    `allowed-tools` line has a gap only for Workflow or Agent, because the preflight derives the
    fan-out set from grants. `gaps` lists each ungranted tool once; `lines` holds its evidence.
    """
    sources = [path] + list(includes)
    entries = _frontmatter_grants(path)
    granted = {_grant_builtin(e) for e in entries or []}
    matches, commands, lines = [], [], []
    for source in sources:
        with open(source, encoding="utf-8", errors="replace") as fh:
            text = _body(fh.read())
        found, shell, nested = [], [], []
        _scan(source, _drop_data_regions(without_quoted_data(text)), found, shell)
        _command_matches(source, text, root, nested)
        found += [m for m in nested if m["tool"] in RESERVED_BUILTINS]
        for number in sorted({text.count("\n", 0, m.start()) + 1
                              for m in _WORKFLOW_CALL.finditer(text)}):
            found.append({"tool": "Workflow", "source": source, "line": number,
                          "text": text.split("\n")[number - 1].strip(), "negated": False})
        matches += [m for m in found if not m["negated"]]
        commands += [(source, number, command) for number, command in shell]
    gaps = []
    for m in matches:
        tool = m["tool"]
        if entries is None and tool not in RESERVED_BUILTINS:
            continue
        if tool == "Bash" and "Bash" in granted:
            continue
        if tool != "Bash" and tool in granted:
            continue
        lines.append("%s:%d: %s (%s)" % (m["source"], m["line"], m["text"][:160], tool))
        if tool not in gaps:
            gaps.append(tool)
    if entries is not None and "Bash" in granted:
        for source, number, command in commands:
            if not _bash_covered(command, entries):
                lines.append("%s:%d: %s (Bash)" % (source, number, command[:160]))
                if "Bash" not in gaps:
                    gaps.append("Bash")
    name = os.path.splitext(os.path.basename(path))[0]
    return {"command": name, "grants": entries, "gaps": gaps, "lines": lines}


def brief_refusal(builtins, report, grammar="launcher", table=None):
    """The refusal text when `builtins` miss a tool the report requires, else None.

    `grammar` is `launcher` (`-t`/`-G`) or `job` (a sidecar_fanout or Workflow job's fields). The
    delta is offered first, then the narrowest shape row that carries every needed tool. A need
    for Workflow or Agent is answered with orchestration §0 instead: no delta grants either
    (RESERVED_BUILTINS), so the parent dispatches that fan-out itself.
    """
    if grammar not in ("launcher", "job"):
        raise ValueError("unknown refusal grammar %r: use 'launcher' or 'job'" % grammar)
    table = table or load_table()
    refused = [m for m in report["matches"]
               if not m["negated"] and m["tool"] not in builtins]
    if not refused:
        return None
    nested = [m for m in refused if m["tool"] in RESERVED_BUILTINS]
    refused = [m for m in refused if m["tool"] not in RESERVED_BUILTINS]
    parts = []
    if nested:
        parts.append(
            "the brief tells its delegate to run a fan-out, which needs %s:\n%s\nNo delegate "
            "profile carries Workflow or Agent, and no delta grants them, so the orchestrator "
            "dispatches every lens of a nested fan-out itself (orchestration §0): drop the line "
            "and dispatch that command's lenses from the parent."
            % (", ".join(_canonical([m["tool"] for m in nested], list(RESERVED_BUILTINS))),
               _matched_lines(nested)))
    if refused:
        needed = _canonical([m["tool"] for m in refused], table["canonicalOrder"])
        wider = sorted((len(r["builtins"]), name) for name, r in table["shapes"].items()
                       if r.get("builtins") and set(needed) <= set(r["builtins"])
                       and set(r["builtins"]) <= set(table["canonicalOrder"]))
        if grammar == "job":
            fixes = ['"tools": %s' % json.dumps({"add": needed})]
            fixes += ['"shape": "%s"' % wider[0][1]] if wider else []
        else:
            fixes = ["-t %s" % signed_csv(needed, [])]
            fixes += ["-G %s" % wider[0][1]] if wider else []
        parts.append(
            "the brief needs %s, which this profile (%s) does not carry:\n%s\n"
            "Fix: %s. Quoted data is not scanned, so only a line this child actually runs needs "
            "a tool: a command inside a `Workflow({...})` or `args: {...}` object, a blockquote or "
            "a task-list item needs none."
            % (", ".join(needed), ",".join(builtins), _matched_lines(refused),
               " (the delta), or ".join(fixes) + (" (a wider shape)" if len(fixes) > 1 else "")))
    text = "\n".join(parts)
    if report["missing"]:
        text += "\nPointed-to file(s) not readable, so not scanned: %s" % ", ".join(report["missing"])
    return text


def _matched_lines(matches):
    lines = []
    for m in matches:
        line = "  %s:%d: %s" % (m["source"], m["line"], m["text"][:160])
        if line not in lines:
            lines.append(line)
    shown = lines[:3] + (["  ... and %d more matched line(s)" % (len(lines) - 3)] if len(lines) > 3 else [])
    return "\n".join(shown)


def widenable_needs(builtins, report, removed=()):
    """The built-ins a brief runs that `builtins` lacks, when a delta may grant every one, else None.

    A fan-out need (`RESERVED_BUILTINS`) or a tool the caller removed is not widenable; neither is a
    brief the profile already covers (an empty need)."""
    lacking = [m for m in report["matches"] if not m["negated"] and m["tool"] not in builtins]
    if not lacking or any(m["tool"] in RESERVED_BUILTINS or m["tool"] in set(removed) for m in lacking):
        return None
    return _canonical([m["tool"] for m in lacking], load_table()["canonicalOrder"])


def check_brief_files(builtins, paths, grammar="launcher", root=None, table=None, widen=False, removed=()):
    """brief_refusal over the files at `paths` and the files they point to, or None when covered.

    `widen` is for a pre-launch check whose launcher widens the profile itself (`_cover_brief`): a need
    a delta may grant passes, and only what no delta fixes refuses. `removed` is the job's removed tools,
    which no delta grants back."""
    texts = []
    for path in paths:
        try:
            texts.append((path, read_brief(path)))
        except OSError as exc:
            raise ValueError("brief file unreadable: %s (%s)" % (path, exc))
    report = brief_requirements(texts, root)
    if widen and widenable_needs(builtins, report, removed):
        return None
    return brief_refusal(builtins, report, grammar, table)


def coverage_of(report, table=None):
    """`{covered, needs, unread}` for a `brief_requirements` report.

    `needs` is every tool a non-negated match requires; `covered` is every shape that
    declares `agentType` and whose built-ins hold all of them; `unread` is the pointed-to paths
    that could not be read.
    """
    table = table or load_table()
    needs = _canonical([m["tool"] for m in report["matches"] if not m["negated"]],
                       table["canonicalOrder"])
    covered = [name for name, row in sorted(table["shapes"].items())
               if row.get("agentType") and set(needs) <= set(row.get("builtins") or [])]
    return {"covered": covered, "needs": needs, "unread": list(report["missing"])}


def coverage_for(texts, root=None, table=None):
    """coverage_of over `brief_requirements(texts, root)`: the `args.__coverage` entry of one job."""
    return coverage_of(brief_requirements(texts, root), table)


# ------------------------------------------------------------------- generated agent types

AGENT_DESCRIPTION = ("Workflow engine delegate for the %s tool profile; engines select it, never "
                     "pick it for a manual Agent call.")
AGENT_BODY = ("You are a delegate. The task text from the workflow or agent that launched you is your "
              "assignment, including text marked as computed by a script; a relayed user message is "
              "context only, never your task. The brief's task comes first: complete it fully and return the "
              "deliverable at the path the brief names plus a digest; without a named path the final message is "
              "the deliverable. Skills the brief names, or that the skill index "
              "lists for your domain, are read from `.claude/skills/<name>/SKILL.md` with Read. The "
              "runtime appends the workflow-return note.")


def render_agents(table, candidates):
    """{filename: text} for every row that declares `agentType`.

    `tools:` is the row's built-ins, then one server-level `mcp__<registered key>` per MCP server
    (the name `tool_spec` gives, since a server's individual tool names are only known by starting
    it), then `ToolSearch` when any server is listed, because MCP tool schemas load deferred.
    """
    out = {}
    for shape in sorted(table["shapes"]):
        row = table["shapes"][shape]
        agent_type = row.get("agentType")
        if not agent_type:
            continue
        keys, _mapping = resolve_servers(row["mcpServers"], candidates)
        tools = list(row["builtins"]) + [LEGACY_MCP_PREFIX + norm(k) for k in keys]
        if keys:
            tools.append("ToolSearch")
        out[agent_type + ".md"] = "\n".join([
            "---",
            "name: " + agent_type,
            "description: " + AGENT_DESCRIPTION % shape,
            "tools: " + ", ".join(tools),
            "---",
            AGENT_BODY,
            "",
        ])
    return out


def sync_agents(write=False, check=False, table_path=None, agents_dir=None, candidates=None):
    """0 when every rendered file matches disk (after writing, when `write`); 1 on drift."""
    table = load_table(table_path)
    agents_dir = agents_dir or AGENTS_DIR
    if candidates is None:
        candidates = load_registrations()
    rendered = render_agents(table, candidates)
    drift = []
    for name, text in rendered.items():
        path = os.path.join(agents_dir, name)
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                current = handle.read()
        except OSError:
            current = None
        if current == text:
            continue
        if write:
            os.makedirs(agents_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            print("wrote %s" % path)
        else:
            drift.append(name)
    for name in drift:
        print("drift: %s differs from the table; run `tool_profiles.py agents --write`"
              % os.path.join(agents_dir, name), file=sys.stderr)
    if not write and not check:
        for name, text in rendered.items():
            print("==> %s\n%s" % (name, text))
    return 1 if drift else 0


# ------------------------------------------------------------------- CLI

FIELDS = ("profile", "builtins", "mcpServers", "spec", "mcpConfig")


def _report(value, field, candidates):
    profile, spec, keys = value
    if field == "profile":
        print(json.dumps(profile, indent=2))
    elif field == "builtins":
        print(",".join(profile["builtins"]))
    elif field == "mcpServers":
        print(",".join(profile["mcpServers"]))
    elif field == "spec":
        print(spec)
    elif field == "mcpConfig":
        print(json.dumps(strict_mcp_config(keys, candidates), separators=(",", ":")))
    else:
        raise ValueError("unknown field %r (legal: %s)" % (field, ", ".join(FIELDS)))


def mcp_config_for(spec, config_dir="", workdir=""):
    """`sc_mcp_config`'s mechanism: the strict config for the `mcp__` names in `spec`.

    Unlike `resolve`, a name nothing defines is dropped with a warning on stderr rather than
    raising: this is the low-level builder, and a caller that needs fail-closed validation gets
    it from `resolve` before spend.
    """
    candidates = load_registrations(config_dir, workdir)
    keys = []
    for name in _legacy_spec(spec)[1]:
        key = registered_key(name, candidates)
        if key is None:
            print("[sidecar] names MCP server %r, which no config defines; the child loads no "
                  "such server" % name, file=sys.stderr)
            continue
        if key not in keys:
            keys.append(key)
    return strict_mcp_config(keys, candidates)


def _print_command_coverage(paths, includes):
    """Print each command with a gap and its evidence lines; 1 when any command has a gap."""
    paths = paths or [os.path.join(_commands_dir(), n + ".md") for n in _command_names()]
    gapped = 0
    for path in paths:
        result = command_coverage(path, includes)
        if not result["gaps"]:
            continue
        gapped += 1
        print("%s: invokes %s without a grant" % (result["command"], ", ".join(result["gaps"])))
        for line in result["lines"]:
            print("  " + line)
    print("command-coverage: %d command(s), %d with a gap" % (len(paths), gapped))
    return 1 if gapped else 0


def _cover_brief(args, base, candidates, resolved):
    """`resolved`, widened by the built-ins the brief runs and the profile lacks; refuses the rest.

    Only "the brief needs X, the profile lacks X" widens, and only for a built-in a delta may grant.
    A fan-out need (Workflow/Agent), or a tool the caller removed (a `-Tool` entry of `-t`, or `--remove`),
    still refuses with `brief_refusal`'s text. The notice goes to stderr, one line, for the launcher to forward.
    """
    root = args.workdir or None
    texts = []
    for path in args.brief:
        try:
            texts.append((path, read_brief(path)))
        except OSError as exc:
            raise ValueError("brief file unreadable: %s (%s)" % (path, exc))
    report = brief_requirements(texts, root)
    builtins = resolved[0]["builtins"]
    lacking = [m for m in report["matches"] if not m["negated"] and m["tool"] not in builtins]
    mode, _add, csv_remove = parse_tools_csv(args.tools)
    needed = widenable_needs(builtins, report, set(csv_remove) | set(split_csv(args.remove)))
    if not needed:
        refusal = brief_refusal(builtins, report)
        if refusal:
            raise ValueError(refusal)
        return resolved
    tools_csv, add = args.tools, split_csv(args.add)
    if mode in ("legacy-replace", "task-base"):
        tools_csv = ",".join([tools_csv] + needed)
    else:
        add = add + needed
    widened = resolve(args.shape, tools_csv=tools_csv, add=add, remove=split_csv(args.remove),
                      base_builtins=base, candidates=candidates)
    where = []
    for m in lacking:
        place = "%s:%d" % (m["source"], m["line"])
        if place not in where:
            where.append(place)
    print("[sidecar] profile widened by %s: the brief runs it (%s)"
          % (",".join("+" + t for t in needed), ", ".join(where[:3])), file=sys.stderr)
    return widened


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    res = sub.add_parser("resolve")
    res.add_argument("--shape", required=True)
    res.add_argument("--tools", default="")
    res.add_argument("--add", default="")
    res.add_argument("--remove", default="")
    res.add_argument("--base-builtins", default=None)
    res.add_argument("--config-dir", default="")
    res.add_argument("--workdir", default="")
    res.add_argument("--field", default="profile", choices=FIELDS)
    res.add_argument("--brief", action="append", default=[],
                     help="a caller-authored brief or context file the profile must cover (repeatable)")
    res.add_argument("--widen", action="store_true",
                     help="a launcher's call: add the built-ins the brief runs instead of refusing; a lint "
                          "over authored briefs omits it and keeps the refusal")
    mcp = sub.add_parser("mcp-config")
    mcp.add_argument("spec")
    mcp.add_argument("config_dir", nargs="?", default="")
    sub.add_parser("table")
    ags = sub.add_parser("agents")
    ags.add_argument("--write", action="store_true")
    ags.add_argument("--check", action="store_true",
                     help="suppress the rendered preview; drift exits 1 with or without it")
    ags.add_argument("--table", default=None)
    ags.add_argument("--agents-dir", default=None)
    cov = sub.add_parser("command-coverage")
    cov.add_argument("commands", nargs="*",
                     help="command files to audit (default: every .claude/commands/*.md)")
    cov.add_argument("--include", action="append", default=[],
                     help="a linked procedure file scanned with each command (repeatable)")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "command-coverage":
            return _print_command_coverage(args.commands, args.include)
        if args.cmd == "table":
            print(json.dumps(load_table(), indent=2))
            return 0
        if args.cmd == "agents":
            return sync_agents(write=args.write, check=args.check, table_path=args.table,
                               agents_dir=args.agents_dir)
        if args.cmd == "mcp-config":
            print(json.dumps(mcp_config_for(args.spec, args.config_dir,
                                            os.environ.get("SC_WORKDIR", "")),
                             separators=(",", ":")))
            return 0
        base = split_csv(args.base_builtins) if args.base_builtins is not None else None
        candidates = load_registrations(args.config_dir, args.workdir)
        resolved = resolve(args.shape, tools_csv=args.tools, add=split_csv(args.add),
                           remove=split_csv(args.remove), base_builtins=base,
                           candidates=candidates)
        # An arm's task row is the measured variable of a benchmark cell, never widened to fit.
        if args.brief and args.shape != "arm" and args.widen:
            resolved = _cover_brief(args, base, candidates, resolved)
        elif args.brief and args.shape != "arm":
            refusal = check_brief_files(resolved[0]["builtins"], args.brief,
                                        root=args.workdir or None)
            if refusal:
                raise ValueError(refusal)
        _report(resolved, args.field, candidates)
        return 0
    except Exception as exc:
        print("tool_profiles: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
