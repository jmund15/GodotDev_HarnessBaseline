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
import subprocess
import sys
import tempfile

PLAIN_TEMP = tempfile.TemporaryDirectory(prefix='pe_plain_temp_')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOKS = os.path.join(ROOT, "hooks")
HOOK = os.path.join(HOOKS, "pattern_enforcer.py")
sys.path.insert(0, HOOKS)

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
    # A command substitution inside double quotes still runs.
    'echo "$(rm -rf src)"',
    'echo "x `rm -rf src` y"',
    # ANSI-C quoting holds an escaped quote; a case arm's `)` does not close its substitution.
    "echo $'\\'' ; rm -rf src",
    'echo "$(case x in a) rm -rf src;; esac)"',
    "rm -rf /some/dir",
    "rm -r build/",
    "rm -R build/",
    "rm --recursive build/",
    "rm -f -r dir/",
    "rm foo -r",
    "echo hi; rm -rf /important",
    # Resolved-target allow: roots themselves, escapes and unresolvable targets stay blocked.
    "rm -rf .claude/scratch",
    "rm -rf .claude/scratch/*",
    "rm -rf /tmp",
    "rm -rf /tmp/*",
    "rm -rf $UNSET/x",
    "rm -rf ~",
    "rm -rf .claude/scratch/../../src",
    "rm -rf .claude/scratch/x src",
    "cd /tmp && rm -rf ../Users",
    "rm -rf src/.godot",
    "mkdir -p src && rm -rf src",
    # KFM #50: a second delete hidden beside an allowed one disqualifies the whole command.
    "rm -rf .claude/scratch/x; echo $(rm -rf src)",
    "rm -rf .claude/scratch/x; echo `rm -rf src`",
    "rm -rf .claude/scratch/x; sudo rm -rf ~",
    "rm -rf .claude/scratch/x; ls | xargs rm -rf",
    "P=.claude/scratch/x; read P; rm -rf $P",
    "P=.claude/scratch/x; P=src; rm -rf $P",
    "P=.claude/scratch/x; for P in src; do rm -rf $P; done",
    # A quoted flag is still a flag — the shell sees `rm -r build/`.
    'rm "-r" build/',
    "rm '-rf' build/",
    # Scratch evidence is not recoverable merely because this session made it (S15).
    "rm -rf .claude/scratch/ladder_audit/fanout && echo ok",
    "P=.claude/scratch/recompact_probe; rm -rf $P/out",
    'R="."; rm -rf "$R/.claude/scratch/commitmsg"',
    "rm -rf .claude/scratch/fmt_test/*",
    'Remove-Item -Recurse -Force ".claude/scratch/old_probe" -Confirm:$false',
]

# Recovered denial #8 used to sit in MUST_BLOCK above as "correctly blocked". A4 (2026-09-14)
# authorizes exactly this shape — a recursive delete whose only target is a variable the same
# command assigns, once, from `$(mktemp -d)` — as ownership evidence a literal path never carries.
# `test_pattern_enforcer_owned_temp_cleanup.py` owns this case now, plus the block shapes that
# still deny a look-alike with no real ownership evidence.

# Fixtures that are genuine recursive deletes are dropped from the false-positive arm by
# `load_false_positives`; leaving one there would demand the matcher stop blocking a real delete.

# --- arm 3: safe deletes that must never have been blocked -----------------------------------
MUST_PASS = [
    # Quoting restarts inside $(...): a single-quoted pattern there is data, even inside "...".
    'echo "a $(grep -c \'"rm -rf src\' f) b"',
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
    # Plain temp paths and regenerable caches remain recoverable cleanup targets.
    'cd "' + PLAIN_TEMP.name.replace('\\', '/') + '" && rm -rf gt && mkdir gt && cd gt && git init -q',
    # The runner points TMP at a private directory inside /tmp, so /tmp itself is only a temp path off the runner.
    'cd "' + PLAIN_TEMP.name.replace('\\', '/') + '" && rm -rf junk',
    "rm -rf " + PLAIN_TEMP.name.replace('\\', '/') + "/red_check",
    "rm -rf .claude/worktrees/uid-header-backfill/.godot",
    "rm -rf .godot && echo reimport",
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

# The deny hint never advertises a shape the resolver refuses: a retired worktree goes alone in its
# command (`_is_resolved_safe_cleanup` never admits it chained), so the hint offers no chaining and
# points to `orchestration` reference `worktrees.md`, which states the alone-then-prune recipe.
hint = pe._CACHE_HINT
if "chained" in hint or "variable" in hint or "retired worktrees (`orchestration` reference `worktrees.md`)" not in hint:
    print("  FAIL deny hint offers a retired-worktree delete chained or through a variable")
    fails += 1
if len(hint.encode("utf-8")) > 600:
    print(f"  FAIL deny hint is {len(hint.encode('utf-8'))} B; keep it under 600")
    fails += 1

DANGEROUS_COMMANDS = [
    ("del /s /q C:\\temp\\x", True),
    ("rd /s /q C:\\temp\\x", True),
    ("rmdir /s /q C:\\temp\\x", True),
    ("format C:", False),
    ("Remove-Item -Recurse -Force C:\\temp\\x", True),
    ("rm -rf /important", True),
    ("rm --recursive /important", True),
]
for command, has_cache_hint in DANGEROUS_COMMANDS:
    result = subprocess.run([sys.executable, HOOK],
                            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
                            capture_output=True, text=True, timeout=60)
    ok = (result.returncode == 2 and not result.stdout.strip()
          and "CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first" in result.stderr
          and (not has_cache_hint or ("Allowed: caches, temp dirs" in result.stderr
                                       and "Never in $(...)" not in result.stderr)))
    if not ok:
        print(f"  FAIL dangerous command deny cite/cache hint: {command} -> {result.stderr[:180]!r}")
        fails += 1
    else:
        print(f"  PASS dangerous command deny cites go-ahead: {command}")

# A `cd` into a glob lands in a directory the guard cannot resolve, so the delete after it must be blocked
# even when its own target is an owned scratch path; the lone owned delete stays allowed.
_SID = "12345678-1234-1234-1234-123456789abc"
_TRANSCRIPT = os.path.join(PLAIN_TEMP.name, _SID + ".jsonl")
for command, want_blocked in (
        (f"rm -rf .claude/scratch/{_SID}/x", False),
        (f"cd .claude/scratch/{_SID}/* && rm -rf .claude/logs/x", True)):
    got = pe.check_bash_command(command, None, _TRANSCRIPT)[0]
    if got != want_blocked:
        print(f"  FAIL resolved-delete walker blocked={got}, want {want_blocked}: {command}")
        fails += 1
    else:
        print(f"  PASS resolved-delete walker blocked={got}: {command}")

print()
print("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)")
sys.exit(1 if fails else 0)
