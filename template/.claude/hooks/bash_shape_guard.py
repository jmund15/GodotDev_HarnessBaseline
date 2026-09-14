#!/usr/bin/env python3
"""
Hook: PreToolUse (Bash|Monitor) - DISABLED 2026-09-01, all shape denials commented out.

WHY DISABLED: the premise was falsified by direct test. The hook denied heredocs,
$(...), backticks, `&` and cd-compound-writes on the claim that the auto-mode
classifier "cannot statically analyze this shape and would prompt the user
manually". Measured with the hook's settings.json matcher temporarily neutered:
`cat <<EOF`, `python3 - <<EOF` reading a file, and `python3 - <<EOF` WRITING a
file all ran silently with zero prompts. The hook was therefore converting a
working one-call shape into a mandatory two-call write-file-then-run detour.

Counter-evidence the classifier is still live and still judges intent, not
prefix: `sed -i` against this very file was DENIED while `Bash(sed *)` sat in
permissions.allow. So auto mode does inspect allowlisted commands - it just does
not object to these shapes.

CONFOUND, unresolved: permissions.allow currently carries broad `Bash(cat *)`,
`Bash(python3 *)`, `Bash(bash *)`, `Bash(sed *)`. The clean runs may owe to
those rather than to classifier tolerance. `/auto-mode-setup` proposes removing
the interpreter entries. If they go, re-run the test below before trusting this
file's verdict.

TO REIMPLEMENT: uncomment the wanted block(s) in main(). Regexes, _WRITE_OPS and
_deny() are all left live and unmodified, so restoring one rule is uncommenting
its `if` block. Re-verify first - set the settings.json matcher to a
non-matching string, run the shape bare, and see whether a prompt appears.

ROOT CAUSE (from gotcha_auto_mode_classifier_fail_closed.md): Claude Code 2.1.232
(2026-08-13) made input redirections permission-checked; 2.1.233 reverted it the
next day as a regression. This hook was authored against 2.1.232 and was correct
then. The platform moved; the hook did not.

Sibling: compound_cd_approver.py (approve side; allow-only, never denies).
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

# cd-compounds with a write operation. Mirrors compound_cd_approver's segment
# split; git verbs are excluded (the approver's safe list covers cd + git).
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

    # --- DISABLED shape denials (see module docstring). Uncomment to restore. ---
    #
    # if _HEREDOC.search(scan):
    #     _deny(
    #         "Blocked by bash_shape_guard: heredoc/here-string redirect (<<) - the "
    #         "auto-mode classifier cannot statically analyze this shape and would "
    #         "prompt the user manually. Rewrite statically: Write the payload to a "
    #         "temp file, then run the command against it (git commit -F <msgfile>, "
    #         "python3 <probe.py>, bash <script>). Canon: CLAUDE.md §Shell Discipline."
    #     )
    #     sys.exit(0)
    # if _SUBST.search(command):
    #     _deny(
    #         "Blocked by bash_shape_guard: command substitution ($(...)) - the "
    #         "auto-mode classifier cannot statically analyze this shape and would "
    #         "prompt the user manually. Rewrite statically: Write the probe to a "
    #         "file and run python3 <probe.py> / bash <script> / git -C <path> with "
    #         "literal args. Canon: CLAUDE.md §Shell Discipline."
    #     )
    #     sys.exit(0)
    # if _BACKTICK.search(command):
    #     _deny(
    #         "Blocked by bash_shape_guard: backtick command substitution (`...`) - "
    #         "same fail-closed class as $(...). Rewrite statically: Write the probe "
    #         "to a file and run python3 <probe.py> / bash <script>. "
    #         "Canon: CLAUDE.md §Shell Discipline."
    #     )
    #     sys.exit(0)
    # if _BACKGROUND.search(scan):
    #     _deny(
    #         "Blocked by bash_shape_guard: background operator (&) - the auto-mode "
    #         "classifier cannot statically analyze backgrounded processes and would "
    #         "prompt the user manually. Rewrite statically: use the Bash tool's "
    #         "run_in_background parameter instead of shell '&', or restructure "
    #         "without backgrounding (Write-tool probe scripts, timeout-wrapped "
    #         "foreground runs). Canon: CLAUDE.md §Shell Discipline."
    #     )
    #     sys.exit(0)
    # if _CD_START.match(command) and any(
    #     seg.split()[0].lower() in _WRITE_OPS
    #     for seg in _SEGMENT_SPLIT.split(scan)[1:]
    #     if seg.split()
    # ):
    #     _deny(
    #         "Blocked by bash_shape_guard: cd-compound with a write operation - "
    #         "the platform cannot statically determine the compound's final working "
    #         "directory, so relative write targets can't be checked for Cygwin-"
    #         "emulated symlinks, and it prompts manually. Rewrite statically: drop "
    #         "the cd - use git -C <abs-path> for git, and standalone commands with "
    #         "absolute targets for writes (rm C:/abs/path). "
    #         "Canon: CLAUDE.md §Shell Discipline."
    #     )
    #     sys.exit(0)

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
