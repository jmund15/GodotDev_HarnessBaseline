#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — nudge unbounded recursive scans toward a bound.

Why:
- A recursive `grep -r` / `rg` / `find` whose output is neither capped nor reduced
  to filenames returns its FULL match set into context. Measured 2026-08-04: two
  such greps returned ~70KB each and spilled to tool-results files, costing more
  context than every instruction file in the harness combined.
- Output size is unknowable before the call, so the rule is structural: a
  recursive scan must either cap its output (`| head -N`), reduce it to names or
  counts (`-l` / `-c`), or stop at the first match per file (`-m1`).
- Canonical home: CLAUDE.md §9 (Tool Routing) — "bound every recursive scan".
  This hook only enforces and cites it.

Second axis — SCOPE, independent of volume:
- `grep -r` / `find` are blind to .gitignore and sweep ignored trees that every
  git-aware search excludes — chiefly .claude/worktrees/, whole extra checkouts
  of this repo. Measured 2026-08-09: one `grep -r` returned 261 worktree hits on
  a query where the Grep tool returned 0.
- Bounding does not fix scope: `grep -r ... | head -50` passes the volume check
  and still returns worktree hits, which a capped result makes read as
  authoritative. The two checks therefore fire independently.
- `rg`, `git grep`, and the Grep tool honour .gitignore — never flagged here.
- Canonical home: CLAUDE.md §9 (Tool Routing). This hook only enforces and cites.

What it does:
- Inspects the command string for a recursive-scan verb with no bounding token,
  and separately for a gitignore-blind scan verb with no scoping token.
- Emits a hookSpecificOutput.additionalContext advisory. additionalContext is the
  ONLY model-visible advisory channel on PreToolUse; stderr on exit 0 is dead.
- Never blocks: some scans legitimately need the full set, and the caller knows
  which. Exits 0 on every path.

Deliberately NOT flagged (already bounded, already scoped, or inherently small):
- Anything piping to head/tail/wc/sort -u/uniq, or using -l/-c/-m/-q/--files-with-matches.
- The Grep tool itself (defaults to a head_limit) — this only sees raw shell.
- Non-recursive greps against explicit paths, which are bounded by the file.
- Scans naming a narrowing path operand (`grep -rn pat Tests/`, explicit file lists).
  SCOPED matches flags only, so operand scoping needs its own parse; without it a
  fully-scoped scan reads as a whole-tree sweep. Measured 2026-08-17: four such
  misfires in one session, each costing the reader a rebuttal turn.
- Scans whose cwd is inside `.claude/worktrees/`, where the scope advisory's premise
  inverts — it would warn against entering the tree the caller is working in.

An advisory hook holds a credibility budget: every misfire spends it, and once a
reader learns to skim these, the true positives stop working too. A premise this hook
cannot evaluate (cwd-relative, operand-relative) narrows the trigger rather than
firing blind.

Wired in: settings.json hooks.PreToolUse with matcher "Bash|PowerShell".
"""

import json
import os
import re
import shlex
import sys

# A scan that walks a tree and prints matching LINES.
RECURSIVE_SCAN = re.compile(
    r"(?:^|[|;&]\s*|\s)(?:grep\s+[^|;&]*-[a-zA-Z]*r|rg\s|find\s|ls\s+-[a-zA-Z]*R|dir\s+/s)",
    re.IGNORECASE,
)

# Any of these means the caller already bounded or reduced the output.
BOUNDED = re.compile(
    r"(?:\|\s*(?:head|tail|wc|uniq|sort\s+-u|Select-Object|measure))"
    r"|(?:\s-[a-zA-Z]*(?:l|c|q)\b)"
    r"|(?:--files-with-matches|--count|--quiet|-m\s*\d|-m\d)"
    r"|(?:\s-print0)"
    r"|(?:head_limit)",
    re.IGNORECASE,
)

# Walks a tree while blind to .gitignore. `rg` and `git grep` honour it, so both
# are absent here by construction (neither matches these alternatives).
GITIGNORE_BLIND = re.compile(
    r"(?:^|[|;&]\s*|\s)(?:grep\s+[^|;&]*-[a-zA-Z]*r|find\s)",
    re.IGNORECASE,
)

# The caller already scoped the walk, or handed it to a gitignore-aware tool.
# `--include` is deliberately ABSENT: it filters by filename pattern, not by tree,
# so `grep -r pat . --include=*.cs` still walks every ignored checkout.
SCOPED = re.compile(
    r"--exclude-dir|--exclude|-prune|\s-path\s|git\s+grep|git\s+ls-files",
    re.IGNORECASE,
)

# Operands that name the whole tree rather than narrowing it.
BROAD_OPERANDS = {".", "./", "/", "~", "~/", "$HOME", "$PWD", "*"}

# Flags whose VALUE is a separate token, so the value is not a path operand.
VALUE_FLAGS = {
    "-e", "-f", "-m", "-d", "-A", "-B", "-C", "--include", "--exclude",
    "--exclude-dir", "--max-count", "-name", "-iname", "-type", "-path", "-regex",
}

SCAN_VERBS = ("grep", "rg", "find", "ls", "dir")
PATH_FIRST_VERBS = ("find", "ls", "dir")

# Bare operator tokens end the scan's own operand list. A quoted `\|` inside a
# pattern is one token and never matches these.
PIPELINE_OPERATORS = {"|", "||", "&&", "&", ";"}


def has_narrowing_path(command: str) -> bool:
    """True when the scan names a path operand that bounds the walk to a subtree or
    to explicit files. This is the OPERAND axis; SCOPED sees only flags, so without
    this a fully-scoped `grep -rn pat Tests/` reads as a whole-tree sweep."""
    # Tokenize BEFORE splitting on pipeline operators: `grep -rn "a\|b" Tests/` carries
    # a `|` inside the quoted pattern, and a regex split there truncates the command
    # mid-quote, losing the path operand entirely.
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False

    verb_index = None
    for index, token in enumerate(tokens):
        if os.path.basename(token) in SCAN_VERBS:
            verb_index = index
            break
    if verb_index is None:
        return False

    operands, skip_next = [], False
    for token in tokens[verb_index + 1:]:
        if token in PIPELINE_OPERATORS:
            break
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            skip_next = token in VALUE_FLAGS
            continue
        operands.append(token)
    if not operands:
        return False

    # `find`/`ls` take paths first; `grep`/`rg` spend the first operand on the pattern.
    verb = os.path.basename(tokens[verb_index])
    paths = operands if verb in PATH_FIRST_VERBS else operands[1:]
    return any(path not in BROAD_OPERANDS for path in paths)


def in_worktree(cwd: str) -> bool:
    """A worktree checkout is where the scope advisory's premise inverts: the tree it
    warns about entering is the tree the caller is deliberately working in."""
    return ".claude/worktrees" in (cwd or "").replace("\\", "/")

SCOPE_ADVICE = (
    "⚠ GITIGNORE-BLIND SCAN — `grep -r` and `find` walk ignored trees that every "
    "git-aware search excludes, chiefly `.claude/worktrees/`: whole extra checkouts "
    "of this repo whose hits are indistinguishable from real ones. Measured: 261 "
    "worktree hits on a query where the Grep tool returned 0.\n"
    "Capping output does NOT fix this — a bounded, worktree-polluted result reads "
    "as authoritative.\n"
    "Use a tool that honours .gitignore:\n"
    "  • the Grep tool — ripgrep-backed, respects .gitignore, defaults to a head_limit\n"
    "  • `git grep <pat>` — searches tracked files only\n"
    "  • `git ls-files` — when you want the path list rather than matches\n"
    "  • keep `grep -r` only with `--exclude-dir=.claude` (or a narrower root path), "
    "and say why the git-aware tools don't fit\n"
    "Canon: CLAUDE.md §9 Tool Routing."
)

ADVICE = (
    "⚠ UNBOUNDED RECURSIVE SCAN — this command walks a tree and prints matching "
    "LINES with no cap. Output size is unknown before the call; a wide match set "
    "lands in context permanently and can spill to a tool-results file.\n"
    "Bound it before running:\n"
    "  • `| head -50` — cap the lines you actually need\n"
    "  • `-l` (files only) or `-c` (counts) — reduce, then read the few that matter\n"
    "  • `-m1` — first match per file\n"
    "  • narrow the path/glob instead of filtering a wide scan through a second grep\n"
    "Canon: CLAUDE.md §9 Tool Routing. Prefer the Grep tool (defaults to a "
    "head_limit) over raw shell grep when you just need matches."
)


def needs_bound(command: str) -> bool:
    if not command:
        return False
    return bool(RECURSIVE_SCAN.search(command)) and not BOUNDED.search(command)


def needs_scope(command: str, cwd: str = "") -> bool:
    if not command:
        return False
    if not GITIGNORE_BLIND.search(command):
        return False
    if SCOPED.search(command) or has_narrowing_path(command) or in_worktree(cwd):
        return False
    return True


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    tool_input = input_data.get("tool_input") or {}
    command = tool_input.get("command") or ""

    advisories = []
    if needs_bound(command):
        advisories.append(ADVICE)
    if needs_scope(command, input_data.get("cwd") or ""):
        advisories.append(SCOPE_ADVICE)
    if not advisories:
        sys.exit(0)

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n\n".join(advisories),
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Advisory hook: never block a command because the guard broke.
        sys.exit(0)
