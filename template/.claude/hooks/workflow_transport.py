#!/usr/bin/env python3
"""The args keys the kept Workflow engines read, injected on every Workflow call (T4).

`hooks/pre_workflow_dispatch.py`, the one PreToolUse(Workflow) entry, calls `inject`. A Workflow script has no
filesystem, so each key reaches the engine only through this injection:

  __transport     {name, ids, efforts?}: the native pin vocabulary, the `native` block of `seats.json`.
  __toolProfiles  {job label or lens key: {kind, agentType, builtins, mcpServers}}, one row per job from
                  `guards/tool_profiles.json`.
  __kindProfile   the same row for the call's own `args.kind`, which a single-kind engine (doc audit, worklog
                  relevance, idea fanout, test_skill_pressure) runs every child as. Omitted when the call names no
                  kind or the kind has no row.
  __railsText     `guards/rails.md`, whole: one file for every model (rails Q7).
  __repoRoot      the checkout root (git toplevel of the session cwd). Omitted when git cannot answer.

It runs no capacity, tier, five-hour, citer-coverage, non-thinking or effort check. The caller pins every job's model
and effort, and the launch checks are `tools/launch_check.py`'s (both routes call it).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[1]
PROFILE_KEYS = ("agentType", "builtins", "mcpServers")  # the only row fields a kept engine reads


class TransportError(Exception):
    """The transport cannot inject: the message names the file to repair. The entry denies the call on it."""


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TransportError("%s could not be read (%s: %s)" % (path, type(exc).__name__, exc))


def native_vocabulary(claude_dir=CLAUDE_DIR):
    """`{name, ids, efforts?}` from the `native` block of seats.json. Fails closed: the engines refuse a missing one."""
    sys.path.insert(0, str(CLAUDE_DIR / "tools"))
    import launch_check  # noqa: E402  (tools/launch_check.py: the one reader of seats.json)
    try:
        native = launch_check.table(claude_dir).get("native")
    except (OSError, ValueError) as exc:
        raise TransportError("seats.json could not be read (%s: %s)" % (type(exc).__name__, exc))
    if not isinstance(native, dict) or not native.get("ids"):
        raise TransportError("seats.json has no `native` block with `ids`; the Workflow engines have no legal pins")
    vocabulary = {"name": "native", "ids": list(native["ids"])}
    if native.get("efforts"):
        vocabulary["efforts"] = list(native["efforts"])
    return vocabulary


def jobs_of(args):
    """Every job-shaped entry of a call: `jobs`, each `chains[].jobs`, and `lenses` (explore)."""
    jobs = [j for j in args.get("jobs") or [] if isinstance(j, dict)]
    for chain in args.get("chains") or []:
        if isinstance(chain, dict):
            jobs += [j for j in chain.get("jobs") or [] if isinstance(j, dict)]
    return jobs + [l for l in args.get("lenses") or [] if isinstance(l, dict)]


def _table(claude_dir):
    return _json(Path(claude_dir) / "guards" / "tool_profiles.json")


def _kinds(claude_dir):
    return _table(claude_dir).get("kinds") or {}


def parse_delta(raw):
    """`(add, remove)` of a job's `tools` field: a string (comma or space separated) or a list of signed names
    (`+Bash`, `-Grep`). Raises ValueError when it is not one: an unsigned name, a bare sign, an empty value."""
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        raw = ",".join(raw)
    if not isinstance(raw, str):
        raise ValueError("tools must be a signed string or list such as '+Bash' or '+Bash,-Grep'")
    entries = [e for e in raw.replace(",", " ").split() if e]
    if not entries or any(e[0] not in "+-" or not e[1:] or e[1] in "+-" for e in entries):
        raise ValueError("tools %r is not a signed delta such as '+Bash' or '+Bash,-Grep'" % (raw,))
    return [e[1:] for e in entries if e[0] == "+"], [e[1:] for e in entries if e[0] == "-"]


def _has_delta(job):
    return "tools" in job


def _delta_text(add, remove):
    return " ".join(["+%s" % a for a in add] + ["-%s" % r for r in remove])


def _variant(kinds, variants, kind, add, remove):
    """The `variants` row of `kind` whose delta is exactly `add`/`remove`, with its builtins, or None."""
    for row in variants:
        if row.get("kind") == kind and set(row.get("add") or []) == set(add) and set(row.get("remove") or []) == set(remove):
            base = [b for b in kinds[kind].get("builtins") or [] if b not in remove]
            return row, base + [a for a in add if a not in base]
    return None


def _suggestions(kinds, variants, kind, add, remove):
    """What offers the asked tools, computed from the table: whole kinds, then variants of other kinds."""
    found, writers = [], []
    for name, row in kinds.items():
        have = row.get("builtins") or []
        if name != kind and all(a in have for a in add) and not any(r in have for r in remove):
            (writers if row.get("writes") else found).append(
                name if row.get("writes") else "%s (%s)" % (name, ", ".join(have)))
    for row in variants:
        other = row.get("kind")
        if other == kind or other not in kinds:
            continue
        have = [b for b in kinds[other].get("builtins") or [] if b not in (row.get("remove") or [])]
        have += [a for a in row.get("add") or [] if a not in have]
        if all(a in have for a in add) and not any(r in have for r in remove):
            found.append("%s %s" % (other, _delta_text(row.get("add") or [], row.get("remove") or [])))
    return found + writers


def resolve_job(kinds, variants, job):
    """The profile of one job: its kind's row, or the variant row its `tools` delta selects. None when the kind has no
    row. Raises ValueError naming the fix when the delta is malformed or no variant covers it."""
    kind = job.get("kind")
    label = job.get("label") or job.get("key") or "(unlabeled)"
    row = kinds.get(kind) if isinstance(kind, str) else None
    if not row:
        return None
    if not _has_delta(job):
        return _profile(kinds, kind)
    try:
        add, remove = parse_delta(job.get("tools"))
    except ValueError as exc:
        raise ValueError("%s: %s" % (label, exc))
    hit = _variant(kinds, variants, kind, add, remove)
    if hit is None:
        options = _suggestions(kinds, variants, kind, add, remove)
        raise ValueError("%s: %s %s has no profile; use %s" % (
            label, kind, _delta_text(add, remove),
            "kind " + (", ".join(options[:-1]) + ", or " + options[-1] if len(options) > 1 else options[0]) if options
            else "a kind or variant row in guards/tool_profiles.json that offers it"))
    variant, builtins = hit
    profile = _profile(kinds, kind)
    profile["agentType"] = variant["agentType"]
    profile["builtins"] = builtins
    return profile


def _profile(kinds, kind):
    """The profile of one kind, or None when no row names it."""
    row = kinds.get(kind) if isinstance(kind, str) else None
    if not row:
        return None
    profile = {"kind": kind}
    profile.update({key: row.get(key) for key in PROFILE_KEYS})
    return profile


def kind_row(kind, claude_dir=CLAUDE_DIR):
    """The whole `guards/tool_profiles.json` row of `kind`, or {} when it has none (the entry reads `writes` from it)."""
    row = _kinds(claude_dir).get(kind) if isinstance(kind, str) else None
    return row or {}


def tool_profiles(args, claude_dir=CLAUDE_DIR):
    """One profile per job whose `kind` has a row, keyed by the job's label (or a lens's key). A job's `tools` delta
    selects its kind's variant row. A job whose delta is malformed or uncovered gets none: `delta_refusals` names it."""
    table = _table(claude_dir)
    kinds, variants = table.get("kinds") or {}, table.get("variants") or []
    out = {}
    for job in jobs_of(args):
        name = job.get("label") or job.get("key")
        try:
            profile = resolve_job(kinds, variants, job)
        except ValueError:
            continue
        if name and profile:
            out[name] = profile
    return out


def delta_refusals(args, claude_dir=CLAUDE_DIR):
    """One line per job whose `tools` delta is malformed or no variant covers, each leading with the fix."""
    table = _table(claude_dir)
    kinds, variants = table.get("kinds") or {}, table.get("variants") or []
    lines = []
    for job in jobs_of(args):
        try:
            resolve_job(kinds, variants, job)
        except ValueError as exc:
            lines.append(str(exc))
    return lines


def kind_profile(args, claude_dir=CLAUDE_DIR):
    """The profile of the call's own `args.kind`, or None. A single-kind engine runs every child as that kind."""
    return _profile(_kinds(claude_dir), args.get("kind"))


def rails_text(claude_dir=CLAUDE_DIR):
    try:
        return (Path(claude_dir) / "guards" / "rails.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise TransportError("guards/rails.md could not be read (%s)" % exc)


def repo_root(cwd):
    """The git toplevel of `cwd`, or None when git cannot answer."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        done = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    root = done.stdout.strip() if done.returncode == 0 else ""
    return root.replace("\\", "/") or None


def inject(args, cwd=None, claude_dir=CLAUDE_DIR):
    """`args` (a dict) with the injected keys added. Returns a new dict; the input is not changed.
    Raises TransportError when a file the keys come from cannot be read."""
    out = dict(args)
    out["__transport"] = native_vocabulary(claude_dir)
    out["__toolProfiles"] = tool_profiles(args, claude_dir)
    call_profile = kind_profile(args, claude_dir)
    if call_profile:
        out["__kindProfile"] = call_profile
    out["__railsText"] = rails_text(claude_dir)
    root = repo_root(cwd)
    if root:
        out["__repoRoot"] = root
    return out
