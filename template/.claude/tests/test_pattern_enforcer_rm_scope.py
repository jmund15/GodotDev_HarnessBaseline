#!/usr/bin/env python3
"""The rm-recursive matcher must not scan past a command separator.

`rm -f a.txt; tool -R out.json` is a SAFE delete followed by an unrelated flag, but a matcher
whose token scan runs to end-of-line reads the later `-R` as the delete's own flag and denies.
Six such denials in one session (2026-09-03), every one a false positive; the real recursive
deletes on those lines were absent.

Arms: the six recorded false positives must PASS, and a battery of genuine recursive deletes
must still be BLOCKED. Without the second arm a matcher that never fires also passes.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import pattern_enforcer as pe  # noqa: E402


def blocked(cmd):
    """The recursive-delete denial message the enforcer emits, or None. Uses the real entry
    point so the test cannot pass against internals the hook no longer consults."""
    is_blocked, msg = pe.check_bash_command(cmd)   # returns (blocked, message)
    if not is_blocked:
        return None
    return msg if "recursive delete" in msg else None


# --- arm 1: the six recorded false positives -------------------------------------------------
fixtures = os.path.join(ROOT, "scratch", "compact_proof", "rm_denials.json")
_RECOVERED = json.load(open(fixtures, encoding="utf-8")) if os.path.exists(fixtures) else []


def load_false_positives(cmds):
    """Recovered denials minus the ones that are genuine recursive deletes.

    The fixture is harvested from the raw transcript, so it holds every denial -- including the
    true positives. Keeping one here would assert the matcher must stop blocking a real delete.
    A recursion flag inside the delete's OWN segment is the discriminator.
    """
    keep = []
    for c in cmds:
        segments = re.split(r"[;|&\n]", c)
        genuine = any(re.search(r"(?<!git\s)\brm\s+(?:[^\s]+\s+)*-[a-zA-Z]*[rR]", s) for s in segments)
        if not genuine:
            keep.append(c)
    return keep


FALSE_POSITIVES = load_false_positives(_RECOVERED)

# --- arm 2: genuine recursive deletes that MUST stay blocked ---------------------------------
MUST_BLOCK = [
    "rm -rf /some/dir",
    "rm -r build/",
    "rm -R build/",
    "rm --recursive build/",
    "rm -f -r dir/",
    "rm foo -r",
    "cd /tmp && rm -rf junk",
    "echo hi; rm -rf /important",
    # A quoted flag is still a flag — the shell sees `rm -r build/`.
    'rm "-r" build/',
    "rm '-rf' build/",
    # Recovered denial #8: a genuine recursive delete of a mktemp dir, correctly blocked. The
    # workaround is a named scratch dir, never a looser matcher.
    'TMP=$(mktemp -d); awk "/x/" f > "$TMP/o"; rm -r' 'f "$TMP"',
]

# Fixtures that are genuine recursive deletes are dropped from the false-positive arm by
# `load_false_positives`; leaving one there would demand the matcher stop blocking a real delete.

# --- arm 3: safe deletes that must never have been blocked -----------------------------------
MUST_PASS = [
    # A heredoc body is data, not execution: authoring a script or a lens brief that MENTIONS a
    # recursive delete blocked the write repeatedly while no delete could run.
    "cat > brief.md <<'EOF'\ntry: find . -exec rm -r {} +\nEOF\necho done",
    "rm -f a.txt",
    "rm -f a.txt; grep -r pattern .",
    "rm -f out.json; bash launcher.sh -R record.json -P prog.log",
    "rm -f x && tool --recursive-check",
    "git rm -r --cached path/",
    # Quote-blanking must still exempt a quoted MENTION of a delete — unwrapping is
    # flag-tokens-only, so this stays a search string, not a command.
    'grep "rm -rf" notes.md',
]

fails = 0
print("pattern_enforcer — rm recursion matcher scope")

for i, cmd in enumerate(FALSE_POSITIVES, 1):
    got = blocked(cmd)
    if got:
        print(f"  FAIL false-positive {i}: still blocked -> {got}")
        print(f"       {cmd[:120]}")
        fails += 1
    else:
        print(f"  PASS false-positive {i}: no longer blocked")

for cmd in MUST_BLOCK:
    if not blocked(cmd):
        print(f"  FAIL must-block NOT blocked: {cmd}")
        fails += 1
if not fails:
    print(f"  PASS all {len(MUST_BLOCK)} genuine recursive deletes still blocked")

for cmd in MUST_PASS:
    if blocked(cmd):
        print(f"  FAIL must-pass blocked: {cmd}")
        fails += 1
if not fails:
    print(f"  PASS all {len(MUST_PASS)} safe deletes allowed")

print()
print("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)")
sys.exit(1 if fails else 0)
