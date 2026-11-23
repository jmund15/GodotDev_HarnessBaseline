#!/usr/bin/env python3
"""The args keys the kept Workflow engines read, injected on every Workflow call (T4).

`hooks/pre_workflow_dispatch.py`, the one PreToolUse(Workflow) entry, calls `inject`. A Workflow script has no
filesystem, so each key reaches the engine only through this injection:

  __transport     {name, ids, efforts?}: the native pin vocabulary, the `native` block of `seats.json`.
  __toolProfiles  {job label or lens key: {kind, agentType, builtins, mcpServers}}, one row per job from
                  `guards/tool_profiles.json`.
  __kindProfile   the same row for the call's own `args.kind`, which a single-kind engine (doc audit, worklog
                  relevance, idea fanout, test_skill_pressure) runs every child as. Omitted when the call names no
                  kind or the kind has no engine row.
  __railsText     `guards/rails.md`, whole: one file for every model (rails Q7).
  __repoRoot      the checkout root (git toplevel of the session cwd). Omitted when git cannot answer.
  __postReturn    true when the project sets `dispatch.post_return` in adaptation.json. The engine then runs
                  `tools/launch_check.py post_return` after each job. Omitted otherwise.

It runs no capacity, tier, five-hour, citer-coverage, non-thinking or effort check. The caller pins every job's model
and effort, and the launch checks are `tools/launch_check.py`'s (both routes call it).
"""

import json
import os
import subprocess
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[1]
PROFILE_KEYS = ("agentType", "builtins", "mcpServers")  # the only row fields a kept engine reads
ENGINE_ROUTE = "engine"


class TransportError(Exception):
    """The transport cannot inject: the message names the file to repair. The entry denies the call on it."""


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TransportError("%s could not be read (%s: %s)" % (path, type(exc).__name__, exc))


def native_vocabulary(claude_dir=CLAUDE_DIR):
    """`{name, ids, efforts?}` from the `native` block of seats.json. Fails closed: the engines refuse a missing one."""
    native = _json(Path(claude_dir) / "seats.json").get("native")
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


def _kinds(claude_dir):
    return _json(Path(claude_dir) / "guards" / "tool_profiles.json").get("kinds") or {}


def _profile(kinds, kind):
    """The profile of one kind, or None when no row serves the engine route."""
    row = kinds.get(kind) if isinstance(kind, str) else None
    if not row or ENGINE_ROUTE not in (row.get("routes") or []):
        return None
    profile = {"kind": kind}
    profile.update({key: row.get(key) for key in PROFILE_KEYS})
    return profile


def tool_profiles(args, claude_dir=CLAUDE_DIR):
    """One profile per job whose `kind` row serves the engine route, keyed by the job's label (or a lens's key)."""
    kinds = _kinds(claude_dir)
    out = {}
    for job in jobs_of(args):
        name = job.get("label") or job.get("key")
        profile = _profile(kinds, job.get("kind"))
        if name and profile:
            out[name] = profile
    return out


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


def post_return_set(claude_dir=CLAUDE_DIR):
    """True when adaptation.json names a `dispatch.post_return` hook."""
    import sys
    tools = str(CLAUDE_DIR / "tools")  # adaptation.py is the one reader; the config comes from claude_dir
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import adaptation
    return bool(adaptation.get(Path(claude_dir), "dispatch.post_return"))


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
    if post_return_set(claude_dir):
        out["__postReturn"] = True
    return out
