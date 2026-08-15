#!/usr/bin/env python3
"""
Hook: PreToolUse (Bash|Monitor) - Deny command shapes the auto-mode classifier cannot analyze

Auto permission mode approves a Bash command only when the platform can statically
verify it. Shapes it cannot analyze - heredocs (<<), command substitution ($(...)),
backtick substitution, backgrounding (&), cd-compounds with write operations -
are refused classifier delegation and force a manual
permission prompt, even when the command itself is safe. This hook denies those
shapes BEFORE the permission system sees them, naming the statically-verifiable
replacement, so the agent self-corrects and no manual prompt appears.

Canon: CLAUDE.md §Shell Discipline ("Auto mode fails closed on unverifiable Bash
shapes"). Sibling: compound_cd_approver.py (approve side of the same family).
Ordered BEFORE compound_cd_approver in settings.json so a denied shape can never
ride a compound approval.

Test affordance: pass a payload file path as argv[1] instead of stdin.
"""

import json
import re
import sys

# Quoted spans are literal text - a commit message or argument containing "<<" is
# not a heredoc. Mirrors pattern_enforcer's quoted-span stripping. Double-quoted
# $(...) and backticks still expand, so the substitution checks run on raw text.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

_HEREDOC = re.compile(r"<<")    # <<EOF / <<'EOF' / <<-EOF / <<< (here-string)
_SUBST = re.compile(r"\$\(")    # $(...) command substitution
_BACKTICK = re.compile(r"`")    # `...` command substitution
_BACKGROUND = re.compile(r"(?<![\d&])&(?![\d&])")  # background & — not &&, not 2>&1, not &>

# cd-compounds with a write operation: the platform cannot statically determine
# the compound's final cwd, so relative write targets can't be symlink-checked
# and it always prompts ("manual approval required to prevent path resolution
# bypass"). Mirrors compound_cd_approver's segment split; git verbs are excluded
# (the approver's safe list already covers cd + git).
_CD_START = re.compile(r"^\s*cd\b")
_WRITE_OPS = frozenset({
    "rm", "del", "rd", "rmdir", "mv", "cp", "mkdir", "touch", "truncate",
    "remove-item", "ri", "move-item", "mi", "copy-item", "ci",
    "set-content", "sc",
})
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\|")


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def main():
    if len(sys.argv) > 1:
        src = open(sys.argv[1], encoding="utf-8")
    else:
        src = sys.stdin
    try:
        input_data = json.load(src)
    except json.JSONDecodeError:
        print("{}")  # Workaround for Claude Code #10463
        sys.exit(0)
    finally:
        if src is not sys.stdin:
            src.close()

    if input_data.get("tool_name") not in ("Bash", "Monitor"):
        print("{}")
        sys.exit(0)

    command = input_data.get("tool_input", {}).get("command", "")
    scan = _QUOTED.sub(" ", command)

    if _HEREDOC.search(scan):
        _deny(
            "Blocked by bash_shape_guard: heredoc/here-string redirect (<<) - the "
            "auto-mode classifier cannot statically analyze this shape and would "
            "prompt the user manually. Rewrite statically: Write the payload to a "
            "temp file, then run the command against it (git commit -F <msgfile>, "
            "python3 <probe.py>, bash <script>). Canon: CLAUDE.md §Shell Discipline."
        )
        sys.exit(0)
    if _SUBST.search(command):
        _deny(
            "Blocked by bash_shape_guard: command substitution ($(...)) - the "
            "auto-mode classifier cannot statically analyze this shape and would "
            "prompt the user manually. Rewrite statically: Write the probe to a "
            "file and run python3 <probe.py> / bash <script> / git -C <path> with "
            "literal args. Canon: CLAUDE.md §Shell Discipline."
        )
        sys.exit(0)
    if _BACKTICK.search(command):
        _deny(
            "Blocked by bash_shape_guard: backtick command substitution (`...`) - "
            "same fail-closed class as $(...). Rewrite statically: Write the probe "
            "to a file and run python3 <probe.py> / bash <script>. "
            "Canon: CLAUDE.md §Shell Discipline."
        )
        sys.exit(0)
    if _BACKGROUND.search(scan):
        _deny(
            "Blocked by bash_shape_guard: background operator (&) - the auto-mode "
            "classifier cannot statically analyze backgrounded processes and would "
            "prompt the user manually. Rewrite statically: use the Bash tool's "
            "run_in_background parameter instead of shell '&', or restructure "
            "without backgrounding (Write-tool probe scripts, timeout-wrapped "
            "foreground runs). Canon: CLAUDE.md §Shell Discipline."
        )
        sys.exit(0)
    if _CD_START.match(command) and any(
        seg.split()[0].lower() in _WRITE_OPS
        for seg in _SEGMENT_SPLIT.split(scan)[1:]
        if seg.split()
    ):
        _deny(
            "Blocked by bash_shape_guard: cd-compound with a write operation - "
            "the platform cannot statically determine the compound's final working "
            "directory, so relative write targets can't be checked for Cygwin-"
            "emulated symlinks, and it prompts manually. Rewrite statically: drop "
            "the cd - use git -C <abs-path> for git, and standalone commands with "
            "absolute targets for writes (rm C:/abs/path). "
            "Canon: CLAUDE.md §Shell Discipline."
        )
        sys.exit(0)

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
