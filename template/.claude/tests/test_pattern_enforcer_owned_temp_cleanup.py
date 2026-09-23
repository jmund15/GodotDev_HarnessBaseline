#!/usr/bin/env python3
"""A4: a recursive delete whose every target is a `NAME=$(mktemp -d)` variable this same command
assigns, exactly once and never reassigns, is allowed regardless of surrounding chaining. A literal
path is never ownership evidence, however mktemp-shaped it looks, so it stays blocked.

Friction F8303: pattern_enforcer denied the verbatim command below (transcript-probe cleanup) even
though its only delete targets `"$TMPD"`, a directory this same command created via `mktemp -d` and
never reassigns. `_is_safe_ephemeral_cleanup` rejects ANY chaining, `$(`, or variable target by
design (`pattern_enforcer.py:198-199`), so it can only over-block a self-owned temp-dir cleanup —
this is a second, narrower allow path alongside it, not a relaxation of it.

Arms: the F8303 command verbatim MUST PASS. Four shapes that look similar but carry no ownership
evidence MUST BLOCK: no mktemp assignment, a reassigned variable, an extra target and a `/..`
suffix. A literal path under the system temp directory is judged by the resolved-target allow,
which `test_pattern_enforcer_rm_scope.py` owns. A chained-but-unrelated second delete riding alongside
an owned one MUST BLOCK (ownership is judged per delete segment, never for the whole command). The
existing `test_pattern_enforcer_cache_cleanup.py` and `test_pattern_enforcer_rm_scope.py` batteries
must stay green, except the one `test_pattern_enforcer_rm_scope.py` MUST_BLOCK case A4 intentionally
flips (a genuine self-owned mktemp-dir delete) — that file's own MUST_BLOCK list no longer carries it.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import pattern_enforcer as pe  # noqa: E402

# The command from the plan's Measured facts A4 block with its machine paths neutralized (its `grep -E`
# argument carries real `\[`/`\]` escapes — losing either backslash would silently change the test).
F8303_COMMAND = (
    'cd /c/Users/dev/Projects/Game && '
    'T=$(ls /c/Users/dev/.claude/projects/C--Users-dev-Projects-Game/9e798553*.jsonl | head -1); '
    'W=$(cygpath -m "$T"); '
    'TMPD=$(mktemp -d); '
    'printf \'{"source":"compact","session_id":"9e798553-probe","transcript_path":"%s"}\' "$W" '
    '| CLAUDE_PROJECT_DIR="$TMPD" python3 .claude/hooks/compact_directive_anchor.py '
    '| grep -E \'^\\[owner-directives\\]|^--- U\' '
    '| cut -c1-300; '
    'rm -rf "$TMPD"'
)

MUST_BLOCK = {
    "no mktemp assignment": 'T=$(ls foo.jsonl | head -1); rm -rf "$T"',
    "reassigned variable": 'TMPD=$(mktemp -d); TMPD="/other/path"; rm -rf "$TMPD"',
    "extra target": 'TMPD=$(mktemp -d); rm -rf "$TMPD" /extra/path',
    "/.. suffix": 'TMPD=$(mktemp -d); rm -rf "$TMPD/.."',
    # Parent review 2026-09-14: a recursive delete that does not HEAD its own segment must still
    # disqualify the command, or one owned delete green-lights a hidden one.
    "delete hidden in command substitution": 'T=$(mktemp -d); echo $(rm -rf /); rm -rf "$T"',
    "delete hidden in backticks": 'T=$(mktemp -d); echo `rm -rf /`; rm -rf "$T"',
    "delete behind sudo": 'T=$(mktemp -d); sudo rm -rf /; rm -rf "$T"',
    "delete behind a background operator": 'T=$(mktemp -d); rm -rf "$T" & rm -rf /',
    # A reassignment that is not `NAME=` still breaks ownership.
    "reassigned by read": 'T=$(mktemp -d); read T < paths.txt; rm -rf "$T"',
    "reassigned by for": 'T=$(mktemp -d); for T in /; do rm -rf "$T"; done',
    # Independent review 2026-09-14 (critical): a suffix on the assignment VALUE moves the target
    # outside the temp directory while the delete operand stays a bare variable.
    "assignment value with a /../.. suffix": 'NAME=$(mktemp -d)/../../important && rm -rf "$NAME"',
    "assignment value with a /.. suffix": 'NAME=$(mktemp -d)/.. ; rm -rf "$NAME"',
    "assignment value with a glob suffix": 'NAME=$(mktemp -d)/* && rm -rf "$NAME"',
}

# An unowned second delete must not ride along beside a legitimate one — ownership is judged per
# delete-shaped segment, never for the whole command.
MUST_BLOCK_CHAINED_UNRELATED = 'TMPD=$(mktemp -d); rm -rf "$TMPD" && rm -rf /important'

# `format` is never relaxed by either allow path, even alongside an owned delete.
MUST_BLOCK_FORMAT_ALONGSIDE = 'TMPD=$(mktemp -d); rm -rf "$TMPD"; format C:'

MUST_PASS = {
    "two independently owned deletes": 'A=$(mktemp -d); B=$(mktemp -d); rm -rf "$A"; rm -rf "$B"',
    "brace form": 'TMPD=$(mktemp -d); rm -rf ${TMPD}',
    "-t template with no slash": 'TMPD=$(mktemp -d -t fooXXXXXX); rm -rf "$TMPD"',
}

MUST_BLOCK_TEMPLATE_SLASH = 'TMPD=$(mktemp -d -t foo/bar); rm -rf "$TMPD"'

# The one case test_pattern_enforcer_rm_scope.py used to carry in its own MUST_BLOCK list — A4
# explicitly authorizes this exact shape (a genuine, self-owned mktemp-dir delete).
FLIPPED_FROM_RM_SCOPE = 'TMP=$(mktemp -d); awk "/x/" f > "$TMP/o"; rm -r' 'f "$TMP"'


def blocked(cmd):
    return pe.check_bash_command(cmd)[0]


def channel(cmd):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "pe-a4-proof",
               "hook_event_name": "PreToolUse", "cwd": ROOT}
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "pre_bash_dispatch.py")],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                          env={**os.environ, "CLAUDE_PROJECT_DIR": os.path.dirname(ROOT)})
    if proc.returncode not in (0, 2) or "Traceback" in proc.stderr:
        return "CRASH", proc
    denied = proc.returncode == 2 or '"deny"' in proc.stdout or "BLOCKED" in (proc.stdout + proc.stderr)
    return ("deny" if denied else "allow"), proc


fails = 0
print("pattern_enforcer — A4 owned-mktemp-dir recursive delete")

if blocked(F8303_COMMAND):
    print("  FAIL F8303 verbatim command blocked")
    fails += 1
else:
    print("  ok   F8303 verbatim command allowed")

for label, cmd in MUST_BLOCK.items():
    if not blocked(cmd):
        print(f"  FAIL must-block allowed ({label}): {cmd}")
        fails += 1
    else:
        print(f"  ok   block ({label}): {cmd}")

if not blocked(MUST_BLOCK_CHAINED_UNRELATED):
    print(f"  FAIL must-block allowed (chained unrelated delete): {MUST_BLOCK_CHAINED_UNRELATED}")
    fails += 1
else:
    print(f"  ok   block (chained unrelated delete): {MUST_BLOCK_CHAINED_UNRELATED}")

if not blocked(MUST_BLOCK_FORMAT_ALONGSIDE):
    print(f"  FAIL must-block allowed (format alongside owned delete): {MUST_BLOCK_FORMAT_ALONGSIDE}")
    fails += 1
else:
    print(f"  ok   block (format alongside owned delete): {MUST_BLOCK_FORMAT_ALONGSIDE}")

if not blocked(MUST_BLOCK_TEMPLATE_SLASH):
    print(f"  FAIL must-block allowed (template with slash): {MUST_BLOCK_TEMPLATE_SLASH}")
    fails += 1
else:
    print(f"  ok   block (template with slash): {MUST_BLOCK_TEMPLATE_SLASH}")

for label, cmd in MUST_PASS.items():
    if blocked(cmd):
        print(f"  FAIL must-pass blocked ({label}): {cmd}")
        fails += 1
    else:
        print(f"  ok   pass ({label}): {cmd}")

if blocked(FLIPPED_FROM_RM_SCOPE):
    print(f"  FAIL flipped case still blocked: {FLIPPED_FROM_RM_SCOPE}")
    fails += 1
else:
    print(f"  ok   pass (flipped from rm_scope MUST_BLOCK): {FLIPPED_FROM_RM_SCOPE}")

for cmd, want in ((F8303_COMMAND, "allow"), (MUST_BLOCK_CHAINED_UNRELATED, "deny")):
    got, proc = channel(cmd)
    if got != want:
        print(f"  FAIL channel {cmd[:60]!r}...: expected {want}, got {got} (exit {proc.returncode}) "
              f"{(proc.stdout + proc.stderr)[:200]!r}")
        fails += 1
    else:
        print(f"  ok   channel {want}: {cmd[:60]}...")

total = 1 + len(MUST_BLOCK) + 1 + 1 + 1 + len(MUST_PASS) + 1 + 2
print()
print(f"{total - fails}/{total} cases pass" if fails else f"ALL PASS ({total} cases)")
sys.exit(1 if fails else 0)
