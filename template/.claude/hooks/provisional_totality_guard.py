#!/usr/bin/env python3
"""Keep provisional code totality-tracked: every `// PROVISIONAL(<slug>)` marker
must have a `.claude/prototype_registry.md` entry.

The totality contract (documented home: `.claude/skills/prototype/SKILL.md` ->
`## PROVISIONAL`): a closed design deliberately shipped minimal carries
`// PROVISIONAL(<slug>)` headers on its `.cs` files and a matching registry row.
This guard is the commit-time backstop for the `.cs`-marked half — same shape
and same fail-open doctrine as its sibling `prototype_containment_guard.py`.

Hooks ENFORCE, they never LEGISLATE: this file is invisible to a human following
doctrine and silent whenever the `Bash` matcher misses (a PowerShell, IDE, or
Godot-editor commit, or a `git merge` — merge commits are created without a
`git commit` invocation). Read the skill for the rule.

Coverage gaps, stated rather than implied:
  - The guard sees `.cs` markers only. A scene-only provisional surface
    (`.tscn`/`.tres`) has no marker to key on; its registry obligation is
    author-discipline plus the promotion sweep (SKILL.md -> `## Promotion`).
  - Registry presence is read from the commit's own tree (index first, HEAD
    fallback), so a marker backfill onto an existing slug needs no registry
    restage — the entry may already sit in HEAD.

FAIL-OPEN. Every non-fully-resolved match ALLOWS: detached HEAD, a `git` call
exiting non-zero, an empty staged list, an unparseable payload, an unreadable
registry. A swallowed exception writes one line to stderr so the miss is never
silent.

Mode:
    provisional_totality_guard.py --hook   # PreToolUse: deny a `git commit`
                                           # staging PROVISIONAL markers whose
                                           # slugs are absent from the registry
"""
import json
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

REGISTRY_PATH = ".claude/prototype_registry.md"
MARKER_RE = re.compile(r"PROVISIONAL\(([a-z0-9-]+)\)")
# Same token-boundary commit matcher as prototype_containment_guard.py — a
# compound `git add … && git commit …` chain must match, while the literal text
# "git commit" inside a quoted message body must not.
GIT_COMMIT = re.compile(r"(^\s*|[;&|]\s*)git\s+(-C\s+\S+\s+)?commit\b")


def _git(args, repo=None):
    """git stdout on success; None on ANY failure (fail-open signal)."""
    cmd = ["git"] + (["-C", repo] if repo else []) + args
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _staged_paths(repo=None):
    out = _git(["diff", "--cached", "--name-only"], repo)
    if out is None:
        return None
    return [line.strip().replace("\\", "/") for line in out.splitlines()]


def _index_text(path, repo=None):
    """Content of `path` in the index; None when unstaged or git failed."""
    return _git(["show", ":" + path], repo)


def _head_text(path, repo=None):
    return _git(["show", "HEAD:" + path], repo)


def _registry_text(repo=None):
    """Registry from the commit's own tree: index first, HEAD fallback."""
    text = _index_text(REGISTRY_PATH, repo)
    if text is None:
        text = _head_text(REGISTRY_PATH, repo)
    return text


def _marker_slugs(repo=None):
    """Slugs of PROVISIONAL markers across staged .cs contents; None on git failure."""
    paths = _staged_paths(repo)
    if paths is None:
        return None
    slugs = []
    for path in paths:
        if not path.lower().endswith(".cs"):
            continue
        text = _index_text(path, repo)
        if text is None:
            continue  # deleted .cs or git failure on this file — skip, not deny
        for match in MARKER_RE.finditer(text):
            if match.group(1) not in slugs:
                slugs.append(match.group(1))
    return slugs


def _missing_slugs(slugs, registry):
    if not registry:
        return list(slugs)
    return [s for s in slugs if not re.search(rf"\b{s}\b", registry)]


def allow():
    print("{}")
    return 0


def warn(message):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": message,
    }}))
    return 0


def deny(missing):
    reason = (
        f"Blocked: staged .cs file(s) carry PROVISIONAL marker(s) with no registry "
        f"entry: {', '.join(missing)}.\n\n"
        "Every // PROVISIONAL(<slug>) marker needs a matching row in "
        "`.claude/prototype_registry.md` (read from the commit's own tree — an entry "
        "already in HEAD counts). Add the row to this commit, or reword the marker. "
        "Rule: .claude/skills/prototype/SKILL.md -> ## PROVISIONAL."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def hook():
    try:
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Bash":
            return allow()
        command = data.get("tool_input", {}).get("command", "") or ""
        match = GIT_COMMIT.search(command)
        if not match:
            return allow()
        repo = (match.group(2) or "").strip()[len("-C"):].strip() or None
        paths = _staged_paths(repo)
        if paths is None:
            return allow()
        slugs = _marker_slugs(repo)
        if slugs is None:
            return allow()
        registry = _registry_text(repo)
        if slugs:
            missing = _missing_slugs(slugs, registry)
            if missing:
                return deny(missing)
            return allow()
        if any(p == REGISTRY_PATH for p in paths):
            return warn(
                "Registry staged with no PROVISIONAL markers in this commit — fine "
                "for a scene-only provisional surface; ensure the entry's Surface "
                "column names the files. Rule: .claude/skills/prototype/SKILL.md -> "
                "## PROVISIONAL."
            )
        return allow()
    except Exception as exc:  # fail-open: a wedged Bash matcher is worse than a missed catch
        print(f"[provisional-totality-guard] fail-open, allowing: {exc!r}", file=sys.stderr)
        return allow()


def main():
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
    print("Usage: provisional_totality_guard.py --hook  (reads a PreToolUse payload on stdin)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
