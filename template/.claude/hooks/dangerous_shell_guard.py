#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — block dangerous shell delete/format commands.

Extracted from the godot layer's `pattern_enforcer.py`, which mixes this fully
domain-agnostic shell-safety half with a Godot/C#-specific code-pattern half
(GD.Print, GetNode-in-_Process, [Export]/[GlobalClass]/[Tool] cascade rules —
none of which apply here). Only the shell half is ported; the code-pattern half
is dropped entirely rather than adapted, since none of it transfers.

Blocks:
- Recursive/forced deletes and directory removal (rm -r/-rf, del /s, rd /s,
  rmdir /s, PowerShell Remove-Item -Recurse and its aliases)
- Drive format commands

Exemptions:
- Quoted spans are blanked before matching, so a dangerous-looking pattern that
  is merely a QUOTED argument (grep "rm -rf", an echo, a commit body) isn't
  mistaken for a real command.
- `git rm` stays allowed (version-controlled, recoverable).
- A single (non-chained) recursive delete confined entirely to this project's
  own ephemeral scratch (`.claude/.cache`, `.claude/logs`) is allowed — that
  scratch is harness-managed and regenerable. Any chained command or any
  non-ephemeral path disqualifies the exemption; it can only over-block, never
  green-light a broad or dangerous delete.

Wired in: settings.json hooks.PreToolUse with matcher "Bash|PowerShell".
"""

import json
import re
import sys

# Dangerous command patterns — applied to BOTH the Bash and PowerShell tools
# (PowerShell text also reaches Bash via `powershell -Command "..."`).
DANGEROUS_BASH_PATTERNS = [
    # Windows dangerous commands
    (r'del\s+/[sq]', 'BLOCKED: Dangerous recursive delete (del /s or /q)'),
    (r'rd\s+/[sq]', 'BLOCKED: Dangerous recursive directory removal (rd /s)'),
    (r'rmdir\s+/[sq]', 'BLOCKED: Dangerous recursive directory removal (rmdir /s)'),
    (r'\bformat\s+[a-zA-Z]:', 'BLOCKED: Format drive command detected'),
    # PowerShell recursive delete — Remove-Item and its delete aliases with
    # -Recurse (PS requires at least -recurse-unambiguous spelling; -r alone
    # errors as ambiguous, so matching the full word keeps false positives low).
    (r'(?i)\b(remove-item|ri|del|erase)\b[^|;\n]*\s-recurse\b', 'BLOCKED: Dangerous recursive delete (Remove-Item -Recurse)'),
    # Block rm with any recursive flag cluster. Plain "rm -f <file>" is a safe
    # single-file delete (force = suppress prompt) and is commonly used for temp
    # cleanup, so it is NOT blocked. Recursion requires -r / -R / --recursive —
    # that is what we actually need to block. `git rm` stays allowed via the
    # lookbehind (version-controlled, recoverable).
    # The intermediate (?:[^\s]+\s+)* span catches separated-flag cases like
    # `rm -f -r dir/` or `rm foo -r` that a stricter prefix match would miss.
    (r'(?<!git\s)\brm\s+(?:[^\s]+\s+)*-[a-zA-Z]*[rR]', 'BLOCKED: Dangerous recursive delete (rm -r / -R / -rf)'),
    (r'(?<!git\s)\brm\s+--recursive\b', 'BLOCKED: Dangerous recursive delete (rm --recursive)'),
]

# Recoverable, harness-managed scratch the tooling itself creates and destroys
# (cache dir, log spool). A recursive delete confined to these is safe.
_EPHEMERAL_RE = re.compile(r'\.claude[\\/](?:\.cache|logs)\b')


def _strip_quoted(command: str) -> str:
    """Blank single/double-quoted spans so a dangerous-looking pattern that is merely a
    QUOTED argument (e.g. `grep "rm -rf"`, an echo, a commit body) isn't mistaken for a
    real command. A genuine `rm -rf "<target>"` still trips the guard — only the quoted
    target blanks; the unquoted `rm -rf` remains to match."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", " ", command)


def _is_safe_ephemeral_cleanup(scan: str) -> bool:
    """True only for a single (non-chained) recursive delete whose every path argument
    sits under an ephemeral prefix. Any control operator (so a chained second delete
    can't ride along) or any non-ephemeral path token disqualifies it — the check can
    never green-light a broad or dangerous delete, only over-block."""
    if re.search(r'&&|\|\||;|\||`|\$\(', scan):
        return False
    paths = [t for t in scan.split()
             if not t.startswith('-')
             and t.lower() not in ('rm', 'del', 'erase', 'ri', 'remove-item')]
    return bool(paths) and all(_EPHEMERAL_RE.search(p) for p in paths)


def check_bash_command(command: str) -> tuple[bool, str]:
    """Check bash command for dangerous patterns. Returns (blocked, message)."""
    scan = _strip_quoted(command)
    for pattern, message in DANGEROUS_BASH_PATTERNS:
        if re.search(pattern, scan, re.IGNORECASE):
            # Allow recursive deletes confined to regenerable harness scratch; never
            # relax a drive-format block regardless of target.
            if 'format' not in message.lower() and _is_safe_ephemeral_cleanup(scan):
                continue
            return True, message
    return False, ""


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    if tool_name in ("Bash", "PowerShell"):
        command = tool_input.get("command", "")
        blocked, message = check_bash_command(command)
        if blocked:
            print(message, file=sys.stderr)
            sys.exit(2)

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
