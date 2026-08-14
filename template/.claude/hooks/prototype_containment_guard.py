#!/usr/bin/env python3
"""Keep throwaway prototype files off every non-`prototype/*` branch.

`prototypes/` (lowercase, plural, repo root) is the throwaway home owned by the
`prototype` skill -- code written to answer one question, never merged. The only
containment mechanism that exists today is `{{PROJECT_NAME}}.csproj:47-48`
(`<Compile Remove="prototypes\\**\\*.cs" />` + `<None Include="prototypes\\**\\*.cs" />`),
which covers `.cs` ONLY. Godot does not compile scenes, so a `prototypes/**/*.tscn`
or `.tres` that lands on `main` is fully live: it loads, and a registry whose scan
root covers it would register it. This guard is the second mechanical layer --
commit-time, extension-agnostic.

The rule's documented home is `.claude/skills/prototype/SKILL.md` -> `## Containment`.
Hooks ENFORCE, they never LEGISLATE: this file is unreadable to a human following
doctrine, invisible to `/rule_consistency`, and silent whenever the `Bash` matcher
misses (a PowerShell, IDE, or Godot-editor commit). Read the skill for the rule.

ENFORCEMENT, NOT INSURANCE. Staging a `prototypes/` path is git-legal today --
`prototypes` appears in NO `.gitignore` and NO `project.godot`, only in the csproj
lines above -- so this guards a surface that is genuinely reachable and currently
empty, not one the toolchain already makes impossible.

FAIL-OPEN. Every path that is not a fully-resolved three-way match ALLOWS: detached
HEAD, a `git` call exiting non-zero, an empty staged list, a payload that does not
parse. The matcher is `Bash`, so a fail-closed crash would wedge every commit in
every future session; a fail-open crash loses one catch on a rare authoring mistake.
A swallowed exception writes one line to stderr so the miss is never silent.

Coverage gaps, stated rather than implied:
  - A commit made outside the `Bash` tool (PowerShell, an IDE, the Godot editor)
    is not seen at all.
  - RUNTIME registration is a separate hole this guard cannot close. Registration
    in this project is by directory placement, so a registry whose scan root
    covered the repo root would pick up `prototypes/**/*.tres` on `main` with no
    commit involved. Verified 2026-08-12: every production `ResourceCollection`
    scan root is a specific subtree (`res://Global/Traits`, `res://Spells`,
    `res://Ingredients`, `res://Synergies`, `res://Global/Categories/`,
    `res://Global/Attributes/`, `res://Global/InputActions/`) and
    `EncounterContentIndex` defaults to `res://Dungeon` -- none is repo-root
    recursive, so commit-time containment is sufficient TODAY. The test-side
    `Tests/Framework/TresFileCollector` DOES default to `res://` recursively;
    it excludes `.godot/.git/.claude/harness-baseline/obj/bin` but not
    `prototypes/`. Adding a repo-root-recursive RUNTIME scan root would reopen
    this hole and needs a load-time lever, not this guard.

Mode:
    prototype_containment_guard.py --hook   # PreToolUse: deny a `git commit` that
                                            # stages `prototypes/` off a prototype branch

There is deliberately no `--worktree` mode: it would report on files that already
exist, and `prototypes/` does not exist on `main` -- a SessionStart sweep would
cost a subprocess every session to report nothing. If one is ever added it gets
registered at SessionStart in the same commit (an unregistered mode is an orphan).
"""
import json
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

PROTOTYPE_BRANCH_PREFIX = "prototype/"
PROTOTYPES_DIR = "prototypes/"

# A `git commit` invocation at a token boundary. A PreToolUse decision applies to
# the ENTIRE command string, so a compound `git add … && git commit …` chain must
# match, while the literal text "git commit" inside a quoted message body must not.
GIT_COMMIT = re.compile(r"(^\s*|[;&|]\s*)git\s+(-C\s+\S+\s+)?commit\b")


def _git(args, repo=None):
    """git stdout on success; None on ANY failure (fail-open signal).

    `repo` mirrors the command's own `git -C <path>` so branch and index are read
    from the repo the commit actually targets -- a `.claude/worktrees/*` checkout
    has its own branch and its own index, and querying this one instead would
    silently answer about the wrong tree.
    """
    cmd = ["git"] + (["-C", repo] if repo else []) + args
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def current_branch(repo=None):
    """Branch name, or None when git failed. Detached HEAD reports the literal 'HEAD'."""
    out = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    return None if out is None else out.strip()


def staged_prototype_paths(repo=None):
    """Staged paths under `prototypes/`. None when git failed; [] when none staged."""
    out = _git(["diff", "--cached", "--name-only"], repo)
    if out is None:
        return None
    paths = (line.strip().replace("\\", "/") for line in out.splitlines())
    return [p for p in paths if p.startswith(PROTOTYPES_DIR)]


def violations(repo=None):
    """Staged `prototypes/` paths that must not land on this branch; [] to allow.

    Every non-match ALLOWS -- git failure, detached HEAD, a prototype branch, or
    nothing staged under `prototypes/`.
    """
    branch = current_branch(repo)
    if not branch or branch == "HEAD":
        return []
    if branch.startswith(PROTOTYPE_BRANCH_PREFIX):
        return []
    return staged_prototype_paths(repo) or []


def allow():
    print("{}")
    return 0


def deny(branch, paths):
    listing = "".join(f"\n  {p}" for p in paths)
    reason = (
        f"Blocked: {len(paths)} staged path(s) under `prototypes/` on branch `{branch}`."
        f"{listing}\n\n"
        "`prototypes/` is throwaway prototype code and belongs only on a never-merged "
        "`prototype/<slug>` branch. The csproj exclusion covers `.cs` ONLY -- a `.tscn` "
        "or `.tres` that lands here is fully live, and registration in this project is "
        "by directory placement. Unstage them (`git rm --cached <path>`), or move the "
        "work onto a `prototype/<slug>` branch. Rule: .claude/skills/prototype/SKILL.md "
        "-> ## Containment."
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
        paths = violations(repo)
        if not paths:
            return allow()
        return deny(current_branch(repo), paths)
    except Exception as exc:  # fail-open: a wedged Bash matcher is worse than a missed catch
        print(f"[prototype-containment-guard] fail-open, allowing: {exc!r}", file=sys.stderr)
        return allow()


def main():
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
    print("Usage: prototype_containment_guard.py --hook  (reads a PreToolUse payload on stdin)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
