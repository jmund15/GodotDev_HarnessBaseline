#!/usr/bin/env python3
"""
baseline_compose.py — the merge rules for every `composed` lock row (§9).

`baseline_sync.py`'s `compose` operation is the only caller. It holds the lock mutex, reads
each `composed` row's `inputs`, dispatches to one function here per row shape, writes the
result with LF bytes, and records `hash`/`inputs` in one mutex-held lock write. This module
never touches the lock or the mutex, and never does its own I/O beyond what its parameters
hand it — every path argument is read by the caller, so a fixture never needs a real `.claude`
tree on disk except through the `hooks_dir` prune paths documented below.

Two composed shapes exist today (`WATCH_COMPOSED_INPUTS` in `baseline_sync.py`):
  - `.claude/settings.json` from `settings.base.json` (tracked) + `settings.project.json`
    (local) — `compose_settings`.
  - `.claude/reference/memory_domains.md` from `memory_domains.base.md` (tracked) +
    `adaptation.json`'s `memory_domains` rows (local) — `render_memory_domains`.

`settings_equivalent` (owner ruling 2026-09-15) judges two settings.json-shaped dicts by behavior,
not bytes: permission lists as sets, hook entries as a `(matcher, entry)` multiset regardless of
grouping, every other top-level key deep-equal. Used to check a project's real, drifted
settings.json against a `compose_settings` draft when the ordered-union merge rule cannot
reproduce an independently-reordered array byte-for-byte.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

MEMORY_DOMAINS_MARKER = "<!-- memory-domains-table -->"

_GODOT_PERMISSION_PATTERN = re.compile(
    r"dotnet|gdunit4|GODOT_BIN|mcp__godot__|godotengine|godot-gdunit", re.IGNORECASE
)
_HOOK_COMMAND_PATH_PATTERN = re.compile(r"\.claude/hooks/([\w.]+)")
_SKILL_ALLOW_PATTERN = re.compile(r"Skill\(([\w-]+)\)")

_DISABLE_PERMISSION_KEYS = {
    "permissions.allow": "allow",
    "permissions.deny": "deny",
    "permissions.ask": "ask",
}


class ComposeError(RuntimeError):
    """A `$disable` entry does not name exactly one base entry, or an input is unusable."""


def canonical_json(obj) -> bytes:
    """Sorted-key, 2-space-indent, LF-terminated JSON — the form `compose --check` compares."""
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _deep_merge(base, project):
    """Recursive merge for every settings.json key except `permissions` and `hooks`, which have
    their own ordered-union / group-append rules and are merged by their dedicated helpers before
    this function ever sees them. A scalar, list, or type-mismatched pair: the project value wins,
    covering `env` per key since each `env` entry is itself a scalar."""
    if isinstance(base, dict) and isinstance(project, dict):
        merged = dict(base)
        for key, value in project.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    return project


def _ordered_union(base_list, project_list):
    result = list(base_list)
    seen = set(base_list)
    for item in project_list:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _merge_hook_groups(base_groups, project_groups):
    """Base order is kept. A project group whose `matcher` equals a base group's appends its
    hook entries to that group; any other project group appends after every base group."""
    groups = [dict(g, hooks=list(g.get("hooks", []))) for g in base_groups]
    by_matcher = {g.get("matcher"): g for g in groups}
    tail = []
    for project_group in project_groups:
        target = by_matcher.get(project_group.get("matcher"))
        if target is not None:
            target["hooks"] = target["hooks"] + list(project_group.get("hooks", []))
        else:
            appended = dict(project_group, hooks=list(project_group.get("hooks", [])))
            tail.append(appended)
            by_matcher.setdefault(project_group.get("matcher"), appended)
    return groups + tail


def _merge_hooks(base_hooks, project_hooks):
    events = list(base_hooks.keys())
    for event in project_hooks:
        if event not in events:
            events.append(event)
    return {event: _merge_hook_groups(base_hooks.get(event, []), project_hooks.get(event, []))
            for event in events}


def _flatten_hook_commands(hooks: dict) -> list[str]:
    return [
        h.get("command")
        for groups in hooks.values()
        for group in groups
        for h in group.get("hooks", [])
    ]


def _disable_base_hooks(base_hooks: dict, hook_targets: list[str]) -> dict:
    """Remove `$disable.hooks` targets from BASE's own hook groups only, before any merge with
    project groups — so a project group that legitimately re-declares the same command string
    (e.g. a full-redeclaration project file) never loses its own copy to the disable filter."""
    if not hook_targets:
        return base_hooks
    filtered: dict = {}
    for event, groups in base_hooks.items():
        new_groups = []
        for group in groups:
            kept = [h for h in group.get("hooks", []) if h.get("command") not in hook_targets]
            new_groups.append(dict(group, hooks=kept))
        filtered[event] = new_groups
    return filtered


def _disable_base_permissions(base_permissions: dict, disable: dict) -> dict:
    """Remove `$disable.permissions.*` targets from BASE's own lists only, before the ordered
    union with project's lists -- the same rationale as `_disable_base_hooks`."""
    filtered = dict(base_permissions)
    for disable_key, list_key in _DISABLE_PERMISSION_KEYS.items():
        targets = disable.get(disable_key)
        if not targets:
            continue
        current = filtered.get(list_key, [])
        filtered[list_key] = [entry for entry in current if entry not in targets]
    return filtered


def _validate_disable(disable: dict, base_hooks: dict, base_permissions: dict) -> None:
    """Every `$disable` target is counted against the BASE entries only (`removes those base
    entries`) — a target the project itself also introduced does not satisfy the count. A count
    other than 1 is a hard error naming the exact key and entry, never a silent skip."""
    hook_targets = list(disable.get("hooks", []))
    if hook_targets:
        base_commands = _flatten_hook_commands(base_hooks)
        for target in hook_targets:
            count = base_commands.count(target)
            if count != 1:
                raise ComposeError(f"$disable.hooks entry matches {count} base entries: {target!r}")
    for disable_key, list_key in _DISABLE_PERMISSION_KEYS.items():
        targets = disable.get(disable_key)
        if not targets:
            continue
        base_list = base_permissions.get(list_key, [])
        for target in targets:
            count = base_list.count(target)
            if count != 1:
                raise ComposeError(f"$disable.{disable_key} entry matches {count} base entries: {target!r}")


def _prune_hooks(hooks: dict, hooks_dir: Path) -> None:
    """R3: drop a hook entry whose `.claude/hooks/<file>` no longer exists, then drop any group
    or event left with nothing in it."""
    for event, groups in list(hooks.items()):
        kept_groups = []
        for group in groups:
            kept_hooks = []
            for h in group.get("hooks", []):
                match = _HOOK_COMMAND_PATH_PATTERN.search(h.get("command", ""))
                if match and not (hooks_dir / match.group(1)).exists():
                    continue
                kept_hooks.append(h)
            if kept_hooks:
                kept_groups.append(dict(group, hooks=kept_hooks))
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]


def _prune_allow(allow: list[str], claude_dir: Path, layers: list[str]) -> list[str]:
    """R10: drop a `Skill(<name>)` entry whose command/skill file is absent, and drop a
    godot-stack permission entry when the profile has not adopted the `godot` layer."""
    def keep(entry: str) -> bool:
        skill_match = _SKILL_ALLOW_PATTERN.fullmatch(entry)
        if skill_match:
            name = skill_match.group(1)
            if not ((claude_dir / "commands" / f"{name}.md").exists()
                    or (claude_dir / "skills" / name).exists()):
                return False
        if "godot" not in layers and _GODOT_PERMISSION_PATTERN.search(entry):
            return False
        return True

    return [entry for entry in allow if keep(entry)]


def compose_settings(base: dict, project: dict, hooks_dir: Path, layers: list[str]) -> dict:
    """§9 `settings.json`: deep-merge everything except `permissions` and `hooks`, ordered-union
    the three permission lists, append-or-merge hook groups by matcher, apply the project's
    `$disable`, then prune per R3/R10. `layers` is the consumer's adopted-layer list (the lock's
    `profile`, or `bootstrap.sh`'s `--layers` before a lock exists) — not itself a Seams-listed
    parameter, but the godot-permission prune filter has no other source for it (see the S8
    report's deviations)."""
    project = dict(project)
    disable = project.pop("$disable", {})
    base_permissions = base.get("permissions", {})
    project_permissions = project.pop("permissions", {})
    base_hooks = base.get("hooks", {})
    project_hooks = project.pop("hooks", {})

    _validate_disable(disable, base_hooks, base_permissions)
    base_permissions = _disable_base_permissions(base_permissions, disable)
    base_hooks = _disable_base_hooks(base_hooks, list(disable.get("hooks", [])))

    rest_base = {k: v for k, v in base.items() if k not in ("permissions", "hooks")}
    merged = _deep_merge(rest_base, project)
    # Only materialize a permission list key ("allow"/"deny"/"ask") that base or project actually
    # declares -- unconditionally synthesizing all three would add e.g. an empty "ask": [] that
    # neither side ever had, breaking canonical-JSON equality with a hand-authored settings.json
    # that omits an unused key entirely.
    permission_keys = [k for k in ("allow", "deny", "ask")
                        if k in base_permissions or k in project_permissions]
    merged["permissions"] = {
        list_key: _ordered_union(base_permissions.get(list_key, []), project_permissions.get(list_key, []))
        for list_key in permission_keys
    }
    merged["hooks"] = _merge_hooks(base_hooks, project_hooks)

    _prune_hooks(merged["hooks"], hooks_dir)
    if "allow" in merged["permissions"]:
        merged["permissions"]["allow"] = _prune_allow(merged["permissions"]["allow"], hooks_dir.parent, layers)

    return merged


def _hook_entry_key(entry: dict) -> str:
    """The hook object with sorted keys, as a comparable string (owner ruling 2026-09-15)."""
    return json.dumps(entry, sort_keys=True, ensure_ascii=False)


def _hook_pair_multiset(hooks: dict) -> dict[str, dict[tuple[object, str], int]]:
    """Per event, a `(matcher, hook entry)` pair -> count multiset, ignoring group shape."""
    result: dict[str, dict[tuple[object, str], int]] = {}
    for event, groups in (hooks or {}).items():
        counts: dict[tuple[object, str], int] = {}
        for group in groups:
            matcher = group.get("matcher")
            for entry in group.get("hooks", []):
                key = (matcher, _hook_entry_key(entry))
                counts[key] = counts.get(key, 0) + 1
        result[event] = counts
    return result


def settings_equivalent(a: dict, b: dict) -> tuple[bool, list[str]]:
    """Owner ruling (2026-09-15): a consumer's settings split is judged by behavior, not bytes --
    order may differ, because matching hooks run in parallel and permission rules apply as sets. Two
    settings are equivalent when `permissions.allow`/`deny`/`ask` are equal as sets, each hook
    event's `(matcher, hook entry)` multiset is equal regardless of grouping, and every other
    top-level key is deep-equal. Returns `(is_equivalent, differences)`."""
    differences: list[str] = []

    a_perm = a.get("permissions", {}) or {}
    b_perm = b.get("permissions", {}) or {}
    for list_key in ("allow", "deny", "ask"):
        a_set = set(a_perm.get(list_key, []))
        b_set = set(b_perm.get(list_key, []))
        if a_set != b_set:
            only_a = sorted(a_set - b_set)
            only_b = sorted(b_set - a_set)
            differences.append(
                f"permissions.{list_key}: only in a {only_a}, only in b {only_b}")

    a_hooks = _hook_pair_multiset(a.get("hooks", {}))
    b_hooks = _hook_pair_multiset(b.get("hooks", {}))
    for event in sorted(set(a_hooks) | set(b_hooks)):
        a_counts = a_hooks.get(event, {})
        b_counts = b_hooks.get(event, {})
        if a_counts != b_counts:
            differences.append(f"hooks.{event}: (matcher, entry) multiset differs")

    other_keys = (set(a) | set(b)) - {"permissions", "hooks"}
    for key in sorted(other_keys):
        if a.get(key) != b.get(key):
            differences.append(f"{key}: not deep-equal")

    return (len(differences) == 0, differences)


def render_memory_domains(base_text: str, domains: list[dict]) -> str:
    """§8/§9 `reference/memory_domains.md`: replace the `<!-- memory-domains-table -->` marker in
    `base_text` with a table rendered from `adaptation.json`'s `memory_domains` rows. Each row is
    `{name, triggers, memory_keywords, skills, rules}`; a row's list fields join with `, `, and an
    absent or empty list renders as `—`. The marker must appear exactly once."""
    count = base_text.count(MEMORY_DOMAINS_MARKER)
    if count != 1:
        raise ComposeError(f"{MEMORY_DOMAINS_MARKER!r} appears {count} time(s); expected exactly 1")

    def cell(values) -> str:
        values = [v for v in (values or []) if v]
        return ", ".join(values) if values else "\N{EM DASH}"

    lines = ["| Domain | Triggers | Memory keywords | Skills | Rules |", "|---|---|---|---|---|"]
    for row in domains:
        lines.append("| {} | {} | {} | {} | {} |".format(
            row.get("name", ""),
            cell(row.get("triggers")),
            cell(row.get("memory_keywords")),
            cell(row.get("skills")),
            cell(row.get("rules")),
        ))
    table = "\n".join(lines)
    return base_text.replace(MEMORY_DOMAINS_MARKER, table, 1)
