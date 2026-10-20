#!/usr/bin/env python3
"""SessionStart hook: probe Ollama and report models.yaml aliases that do not resolve.

Warn-and-continue semantics. Never blocks session start. Output goes to stdout in
additional-context format; Claude Code surfaces it via the SessionStart hook channel. Skips
silently on cloud sessions (CLAUDE_CODE_REMOTE=true).

Never hardcode a model name here. The previous version did, and both halves of the constant went
stale: it demanded a `ollama pull` for a model no alias referenced, while the aliases that were
actually dead went unreported. models.yaml is the registry; this reads it.

It checks EVERY local alias, not the default one. A dead alias fails at call time with a 404 that
reads as a broken tool, and the alias most likely to be dead is the one nobody has exercised
recently -- exactly the one a single-name check misses.

A machine with no local GPU tier gets a different line when Ollama is unreachable. The repo's
tools/workstation_profile.py records this machine's tier, and `none` means no local arms are
configured at all -- there, an unreachable Ollama is the expected state, not a fault with a fix,
and the "run `ollama serve`" instruction sends the reader after something that changes nothing.
The tier is read from the profile's own `local_tier` field, the interface that file documents.
The probe still runs either way, so a machine whose Ollama IS up keeps its full alias report.

Wire-up: register globally in ~/.claude/settings.json under hooks.SessionStart.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

# 127.0.0.1, not localhost: Windows tries ::1 first, where a Docker `ollama` container can answer instead.
OLLAMA_BASE = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434")
MODELS_YAML = pathlib.Path.home() / ".config" / "ai-worker" / "models.yaml"

# The recorded-tier profile and its path override, both as tools/workstation_profile.py documents
# them (PROFILE_ENV, DEFAULT_PROFILE). This file is user scope and outside the repo, so it reads the
# field by name rather than importing the module that owns it.
PROFILE_ENV = "WORKSTATION_PROFILE_PATH"
PROFILE_DEFAULT = pathlib.Path.home() / ".claude" / "workstation_profile.json"

# Deliberately regex and not PyYAML: this runs on every session start and must not be able to fail
# on a missing dependency. The two shapes it needs are unambiguous one-liners in a flat block.
_ALIAS = re.compile(r"^\s{2,}([A-Za-z][\w-]*):\s+(ollama_chat/\S+)\s*(?:#.*)?$", re.M)
_DEFAULT = re.compile(r"^\s{2,}(reader|writer|doc_writer):\s+([\w-]+)\s*(?:#.*)?$", re.M)


def workstation_tier():
    """This machine's recorded `local_tier`, or None when it has no readable profile.

    None is not `none`: an absent, unreadable or malformed profile means the tier is unknown, and
    an unknown tier must keep the ordinary warning.
    """
    override = os.environ.get(PROFILE_ENV)
    path = pathlib.Path(override) if override else PROFILE_DEFAULT
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tier = profile.get("local_tier") if isinstance(profile, dict) else None
    return tier if isinstance(tier, str) else None


def read_registry():
    """-> ({alias: ollama model tag}, {role: alias}). Empty on any read failure."""
    try:
        text = MODELS_YAML.read_text(encoding="utf-8")
    except OSError:
        return {}, {}
    aliases = {a: m.split("/", 1)[1] for a, m in _ALIAS.findall(text)}
    return aliases, dict(_DEFAULT.findall(text))


def main() -> int:
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true":
        return 0

    # SessionStart provides a payload we do not use, but leaving it on stdin can wedge the caller.
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:
        pass

    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError:
        if workstation_tier() == "none":
            print("[ai-worker] no local tier on this machine (workstation profile)")
            return 0
        print(f"[ai-worker] Ollama not reachable at {OLLAMA_BASE} -- local delegation is "
              f"unavailable this session. Run `ollama serve`.")
        return 0
    except Exception as e:
        print(f"[ai-worker] Ollama probe failed ({type(e).__name__}: {e})")
        return 0

    installed = {m.get("name", "") for m in data.get("models", [])}
    aliases, defaults = read_registry()

    if not aliases:
        print(f"[ai-worker] Ollama OK ({len(installed)} models). Could not read local aliases from "
              f"{MODELS_YAML} -- alias resolution UNCHECKED this session.")
        return 0

    dead = sorted(a for a, tag in aliases.items() if tag not in installed)
    live = len(aliases) - len(dead)

    # Name the role bindings: a default pointing at a cloud alias is not a fault, but it is the
    # thing a reader of this line most often wants to know and cannot see from the alias list.
    roles = ", ".join(f"{r}={a}{'' if a in aliases else ' (cloud)'}"
                      for r, a in sorted(defaults.items())) or "none declared"

    if dead:
        detail = "; ".join(f"{a} -> {aliases[a]}" for a in dead)
        print(f"[ai-worker] Ollama OK ({len(installed)} installed). "
              f"{len(dead)} of {len(aliases)} local alias(es) DEAD: {detail}. "
              f"Pull the tag or repoint the alias in {MODELS_YAML.name}. Roles: {roles}")
    else:
        print(f"[ai-worker] Ollama OK -- all {live} local alias(es) resolve. Roles: {roles}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
