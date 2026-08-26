#!/usr/bin/env python3
"""Keep throwaway prototype files off every non-`prototype/*` branch.

`prototypes/` (lowercase, plural, repo root) is the throwaway home owned by the
`prototype` skill -- code written to answer one question, usually never merged. The
csproj carries a blanket `<Compile Remove="prototypes\\**\\*.cs" />` plus a per-slug
`<Compile Include>` allowlist, which covers `.cs` ONLY. Godot does not compile scenes,
so a `prototypes/**/*.tscn` or `.tres` that lands on `main` is fully live: it loads,
and a registry whose scan root covers it would register it. This guard is the second
mechanical layer -- commit-time, extension-agnostic.

DEFAULT-DENY WITH A NAMED ALLOWLIST. A slug carrying a non-`absorbed` row in
`.claude/prototype_registry.md` may land on any branch; every other slug is denied off
a `prototype/*` branch exactly as before. The registry row is the point: its Condition
column must name what retires the surface, so shipping provisional code is a deliberate
act that records its own exit. Keeping the code at `prototypes/<slug>/` rather than
relocating it on promotion is intentional -- the path is what advertises that the code
is still provisional, and moving it would hide precisely that.

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
    prototype_containment_guard.py --hook   # PreToolUse: on a `git commit` --
                                            #   deny `prototypes/` paths off a
                                            #   `prototype/*` branch;
                                            #   ADVISORY (allow) naming non-prototype
                                            #   paths staged ON a `prototype/*` branch --
                                            #   they land only there and die at parking
                                            #   (SKILL.md -> ## Extraction)

There is deliberately no `--worktree` mode: it would report on files that already
exist, and `prototypes/` does not exist on `main` -- a SessionStart sweep would
cost a subprocess every session to report nothing. If one is ever added it gets
registered at SessionStart in the same commit (an unregistered mode is an orphan).
"""
import json
import pathlib
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

PROTOTYPE_BRANCH_PREFIX = "prototype/"
PROTOTYPES_DIR = "prototypes/"
REGISTRY_PATH = ".claude/prototype_registry.md"

# A registry table row: `| <slug> | ... | <status> |`. Only the first and last cells are
# read -- everything between is prose for humans.
REGISTRY_ROW = re.compile(r"^\|\s*([a-z0-9-]+)\s*\|.*\|\s*([a-z]+)\s*\|\s*$")
# Statuses that still ship. An ALLOWLIST, not a blacklist of the retired one: the registry's
# column contract defines exactly active -> absorbing -> absorbed, so any value outside this
# set -- including a misspelled `absorbed` that still matches REGISTRY_ROW -- must deny.
SHIPPING_STATUSES = ("active", "absorbing")

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


def staged_paths(repo=None):
    """All staged paths, posix-normalized. None when git failed; [] when none staged."""
    out = _git(["diff", "--cached", "--name-only"], repo)
    if out is None:
        return None
    return [line.strip().replace("\\", "/") for line in out.splitlines()]


def staged_prototype_paths(repo=None):
    """Staged paths under `prototypes/`. None when git failed; [] when none staged."""
    paths = staged_paths(repo)
    if paths is None:
        return None
    return [p for p in paths if p.startswith(PROTOTYPES_DIR)]


def registered_slugs(repo=None):
    """Slugs the registry currently ships, i.e. every non-`absorbed` row.

    An unreadable or absent registry returns the empty set, which denies every
    prototype path -- the pre-registry behaviour. That is the safe direction: a
    missing allowlist must not read as "allow everything".
    """
    root = pathlib.Path(repo) if repo else pathlib.Path.cwd()
    try:
        text = (root / REGISTRY_PATH).read_text(encoding="utf-8")
    except Exception:
        return set()
    slugs = set()
    for line in text.splitlines():
        match = REGISTRY_ROW.match(line.strip())
        if match and match.group(2) in SHIPPING_STATUSES:
            slugs.add(match.group(1))
    return slugs


def path_slug(path):
    """`prototypes/<slug>/...` -> `<slug>`; None for a bare file directly under prototypes/."""
    parts = path.split("/")
    return parts[1] if len(parts) > 2 else None


def allow():
    print("{}")
    return 0


def deny(branch, paths):
    listing = "".join(f"\n  {p}" for p in paths)
    reason = (
        f"Blocked: {len(paths)} staged path(s) under `prototypes/` on branch `{branch}` "
        f"whose slug is not a shipped entry in {REGISTRY_PATH}."
        f"{listing}\n\n"
        "`prototypes/` is throwaway by default and belongs only on a never-merged "
        "`prototype/<slug>` branch. The csproj exclusion covers `.cs` ONLY -- a `.tscn` "
        "or `.tres` that lands here is fully live, and registration in this project is "
        "by directory placement.\n\n"
        "Three ways forward. Unstage them (`git rm --cached <path>`); or move the work "
        f"onto a `prototype/<slug>` branch; or, if this surface is genuinely shipping "
        f"provisionally, add its row to {REGISTRY_PATH} -- which requires naming the "
        "condition that retires it -- and opt the slug into compilation in "
        "{{PROJECT_NAME}}.csproj. Rule: .claude/skills/prototype/SKILL.md -> ## Containment."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def warn(paths, branch):
    """Advisory on a prototype branch: non-prototype paths staged there die at parking."""
    listing = "".join(f"\n  {p}" for p in paths)
    message = (
        f"On prototype branch `{branch}`: staged non-prototype paths land only on this "
        f"branch and die when it is parked:{listing}\n\n"
        "If any are main-worthy (a production fix, harness/memory write, incidental "
        "fix), extract them to main per .claude/skills/prototype/SKILL.md -> "
        "## Extraction. The branch itself is never merged."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": message,
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
        branch = current_branch(repo)
        if not branch or branch == "HEAD":
            return allow()
        paths = staged_paths(repo)
        if paths is None:
            return allow()
        if branch.startswith(PROTOTYPE_BRANCH_PREFIX):
            non_proto = [p for p in paths if not p.startswith(PROTOTYPES_DIR)]
            if non_proto:
                return warn(non_proto, branch)
            return allow()
        proto_paths = [p for p in paths if p.startswith(PROTOTYPES_DIR)]
        if proto_paths:
            shipped = registered_slugs(repo)
            unregistered = [p for p in proto_paths if path_slug(p) not in shipped]
            if unregistered:
                return deny(branch, unregistered)
        return allow()
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
