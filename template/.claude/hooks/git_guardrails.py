#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — block git operations that destroy local state.

Why:
- Uncommitted and unpushed work has no remote copy and no undo. A single
  `git reset --hard` / `git clean -f` / force-push discards work that no other
  session, branch, or reflog entry can recover. Concurrent sessions share one
  checkout, so the loss is not even necessarily the caller's own work.
- Every blocked operation has a non-destructive alternative that preserves the
  same intent (stash, --keep, dry-run, unstage), so blocking costs one extra
  step and never a re-do.

Scope: destructive-LOCAL and history-rewrite only. Plain `git push`, `git
status`, `git restore --staged`, `git clean -n`, `git branch -d` stay allowed —
they are recoverable or non-destructive.

Matching:
- Splits the command on shell separators and tests each segment independently,
  so a git subcommand only counts when it is the segment's own command word.
  Quoted text (`git log --grep="reset --hard"`) survives tokenization as a
  single argument and never trips a rule.
- Understands `git -C <path> ...` and global flag prefixes (`git --no-pager`).
- Pathological quoting may over-block; the message names the alternative, so the
  recovery is one line.

Wired in: settings.json hooks.PreToolUse with matcher "Bash|PowerShell".
"""

import json
import re
import shlex
import sys

SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&\n]")

# Global flags that may precede the subcommand; the ones listed here take a value.
GLOBAL_FLAGS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def segments(command: str):
    for raw in SEGMENT_SPLIT.split(command):
        raw = raw.strip()
        if raw:
            yield raw


def git_args(segment: str):
    """Return the arg list after `git` + global flags, or None if not a git invocation."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()
    if not tokens:
        return None

    i = 0
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
        i += 1  # leading env assignments
    if i >= len(tokens):
        return None

    head = tokens[i].replace("\\", "/").rsplit("/", 1)[-1]
    if head not in ("git", "git.exe"):
        return None

    args = tokens[i + 1:]
    j = 0
    while j < len(args) and args[j].startswith("-"):
        if args[j] in GLOBAL_FLAGS_WITH_VALUE:
            j += 2
        else:
            j += 1
    return args[j:]


def short_flag_chars(args):
    """Characters of single-dash bundled flags, e.g. -fdx -> {'f','d','x'}."""
    chars = set()
    for a in args:
        if a.startswith("-") and not a.startswith("--"):
            chars.update(a[1:])
    return chars


def verdict(args):
    """Return a one-line block message, or None if allowed."""
    if not args:
        return None
    sub, rest = args[0], args[1:]

    if sub == "reset" and "--hard" in rest:
        return ("BLOCKED `git reset --hard` — discards every uncommitted change with no undo. "
                "Use `git stash` to keep them, or `git reset --keep` to move HEAD safely.")

    if sub == "clean":
        flags = short_flag_chars(rest)
        dry = "n" in flags or "--dry-run" in rest
        if not dry and ("f" in flags or "--force" in rest):
            return ("BLOCKED `git clean -f` — deletes untracked files permanently (git has no copy). "
                    "Run the same command with `-n` first, then ask the user to confirm.")

    if sub == "checkout" and ("--" in rest or "." in rest):
        return ("BLOCKED `git checkout` worktree discard — overwrites uncommitted edits to those paths. "
                "Use `git stash push <path>` instead.")

    if sub == "restore" and not ({"--staged", "-S"} & set(rest)):
        return ("BLOCKED `git restore` without `--staged` — discards uncommitted worktree edits. "
                "Use `git stash push <path>`; `git restore --staged` alone is allowed.")

    if sub == "push":
        forcing = {"--force", "--force-with-lease", "--force-if-includes"} & set(rest)
        forcing = forcing or any(
            a.startswith(("--force-with-lease=", "--force-if-includes=")) for a in rest
        )
        forcing = forcing or "f" in short_flag_chars(rest)
        if forcing:
            return ("BLOCKED force-push — rewrites remote history and can erase commits other "
                    "checkouts still depend on. Rebase onto the remote and push normally.")

    if sub == "branch" and ("-D" in rest or ("--delete" in rest and "--force" in rest)):
        return ("BLOCKED `git branch -D` — force-deletes a branch whose commits may be unmerged "
                "and unpushed. Use `git branch -d`; if it refuses, the work is not merged.")

    if sub == "stash" and rest and rest[0] in ("drop", "clear"):
        return (f"BLOCKED `git stash {rest[0]}` — stashed work is unreferenced once dropped. "
                "Apply it (`git stash pop`) or leave it; ask the user before discarding.")

    if sub == "reflog" and "expire" in rest:
        return ("BLOCKED `git reflog expire` — the reflog is the last recovery path for lost "
                "commits. Leave expiry to git's own schedule.")

    if sub == "gc" and any(a == "--prune" or a.startswith("--prune=") for a in rest):
        return ("BLOCKED `git gc --prune` — permanently removes unreachable objects that reflog "
                "recovery depends on. Run `git gc` without `--prune`.")

    if sub in ("filter-branch", "filter-repo"):
        return (f"BLOCKED `git {sub}` — rewrites every commit in place, invalidating all existing "
                "clones and hashes. Ask the user; this is never an in-session operation.")

    return None


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    command = (input_data.get("tool_input") or {}).get("command") or ""
    if "git" not in command:
        sys.exit(0)

    for segment in segments(command):
        args = git_args(segment)
        if args is None:
            continue
        message = verdict(args)
        if message:
            print(message, file=sys.stderr)
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A broken guard must not block every command in the session.
        sys.exit(0)
