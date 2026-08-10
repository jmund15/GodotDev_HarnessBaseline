#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — nudge unbounded recursive scans toward a bound.

Fully
domain-agnostic logic, only the canon citation changed.

Why:
- A recursive `grep -r` / `rg` / `find` whose output is neither capped nor reduced
  to filenames returns its FULL match set into context. Output size is unknowable
  before the call, so the rule is structural: a recursive scan must either cap its
  output (`| head -N`), reduce it to names or counts (`-l` / `-c`), or stop at the
  first match per file (`-m1`).
- Canonical home: CLAUDE.md § Shell & tool discipline. This hook only enforces and
  cites it.

What it does:
- Inspects the command string for a recursive-scan verb with no bounding token.
- Emits a hookSpecificOutput.additionalContext advisory. additionalContext is the
  ONLY model-visible advisory channel on PreToolUse; stderr on exit 0 is dead.
- Never blocks: some scans legitimately need the full set, and the caller knows
  which. Exits 0 on every path.

Deliberately NOT flagged (already bounded or inherently small):
- Anything piping to head/tail/wc/sort -u/uniq, or using -l/-c/-m/-q/--files-with-matches.
- The Grep tool itself (defaults to a head_limit) — this only sees raw shell.
- Non-recursive greps against explicit paths, which are bounded by the file.

Wired in: settings.json hooks.PreToolUse with matcher "Bash|PowerShell".
"""

import json
import re
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

ADVICE = (
    "⚠ UNBOUNDED RECURSIVE SCAN — this command walks a tree and prints matching "
    "LINES with no cap. Output size is unknown before the call; a wide match set "
    "lands in context permanently and can spill to a tool-results file.\n"
    "Bound it before running:\n"
    "  • `| head -50` — cap the lines you actually need\n"
    "  • `-l` (files only) or `-c` (counts) — reduce, then read the few that matter\n"
    "  • `-m1` — first match per file\n"
    "  • narrow the path/glob instead of filtering a wide scan through a second grep\n"
    "Canon: CLAUDE.md § Shell & tool discipline. Prefer the Grep tool (defaults to a "
    "head_limit) over raw shell grep when you just need matches."
)


def needs_bound(command: str) -> bool:
    if not command:
        return False
    return bool(RECURSIVE_SCAN.search(command)) and not BOUNDED.search(command)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    tool_input = input_data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not needs_bound(command):
        sys.exit(0)

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": ADVICE,
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
