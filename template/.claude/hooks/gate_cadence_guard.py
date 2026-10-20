#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — gate-cadence advice and session-owned post-red verification.

`change_control` §Gate cadence owns the rule. A full gate after this session's recent FAIL/WARN
is denied until this session has invoked narrower verification, or completed suite records from
linked checkouts prove a post-red run on the current content covering every listed failed test.
A peer's red or an old-format artifact advises; it never inherits the peer's verification debt.
`-RetryOnly` is exempt.

Position markers (`final`, `prepush`, `checkpoint`) advise, never authorize a run. Retired width
flags deny and name their replacement. Narrow suites pass; a bare tier filter advises.
Only executable PowerShell `-File` gate invocations count.

Unreadable state supplies no narrow verification. Proof: `.claude/tests/test_gate_cadence_guard.py`.
"""
import io
import os
import re
import sys
import json
import time
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import read_json_salvage, update_json_locked
from _command_text import executable_text

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from sidecar_argv import executable_index, shell_segments_with_comments

POSITIONS = ("final", "prepush", "checkpoint")
STATE_DIR = os.path.join(".claude", "scratch", "gate_positions")
GATE_LAST = os.path.join(".claude", "scratch", "test_runs", "gate_last.md")
# A red older than this is history, not this drive's open failure.
STALE_FAIL_SEC = 12 * 3600

# Inspect the executable token of each shell segment, not a textual prefix: a gate
# inside grouping or a conditional still runs, while an argument mentioning it does not.
SHELL_RE = re.compile(r"(?:pwsh|powershell)(?:\.exe)?", re.I)
MARKER_RE = re.compile(r"#\s*gate:\s*(\w+)", re.I)
EXEMPT_RE = re.compile(r"-(StaticOnly|RetryOnly|RouteOnly)\b", re.I)
# -Watch only streams a detached run's OUT; combined with -Detach the script spawns a gate first.
WATCH_ONLY_RE = re.compile(r"^(?!.*-Detach\b).*-Watch\b", re.I | re.S)

# The width flags were deleted from the gate 2026-08-20, so these no longer bind a
# parameter — a stale invocation copied from an old plan, doc or memory file now dies
# on a PowerShell binding error instead. Catching them here names the replacement.
RETIRED_RE = re.compile(r"-(SmokeImport|Smoke|Targeted|SkipStatic)\b", re.I)

SUITE_RE = re.compile(r"run_test_suite\.ps1|dotnet\s+test|verify\.ps1")
BROAD_FILTER_RE = re.compile(
    r"FullyQualifiedName~Tests\.(Logic|Integration|Sanity)(?![.\w])", re.I)



def sweep_stale_state(directory, keep=None, max_age_sec=7 * 24 * 3600):
    """Drop per-session state files older than a week (§15: bounded state).

    One file per session accumulates forever otherwise, and the directory is gitignored, so
    nothing in the repo ever surfaces the growth. Best-effort and silent because this is
    housekeeping, not an authorization decision.
    """
    try:
        now = time.time()
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if path == keep or not name.endswith(".json"):
                continue
            if now - os.path.getmtime(path) > max_age_sec:
                os.unlink(path)
    except OSError:
        pass


def open_red_at(repo):
    """Return (mtime, session) for a recent red in the gate's artifact, else None."""
    path = os.path.join(repo, GATE_LAST)
    try:
        mtime = os.path.getmtime(path)
        head = io.open(path, encoding="utf-8", errors="replace").read(400)
    except OSError:
        return None
    if time.time() - mtime > STALE_FAIL_SEC:
        return None
    if not re.search(r"^VERDICT=(FAIL|WARN)\b", head, re.M):
        return None
    session = re.search(r'^SESSION=([^\r\n]*)', head, re.M)
    return mtime, session.group(1).strip() or None if session else None


def completed_narrow_covers_red(repo, red_at):
    """Read runner-owned .green records from linked checkouts; bind coverage to content and TRX bytes."""
    try:
        detail = Path(repo, GATE_LAST).read_text(encoding='utf-8-sig')
        failures = set()
        for section in re.findall(r'### [^\n]+ failing tests\r?\n((?:- [^\n]+\r?\n?)+)', detail):
            failures.update(line[2:].split(' :: ', 1)[0].strip() for line in section.splitlines())
        counts = [int(n) for n in re.findall(r'\bfailed=(\d+)', detail)]
        if not failures or sum(counts) != len(failures):
            return False  # A truncated failure listing or count-only WARN cannot establish coverage.
        listing = subprocess.run(['git', '-C', repo, 'worktree', 'list', '--porcelain'],
                                 capture_output=True, text=True, timeout=5)
        if listing.returncode:
            return False
        roots = [line[9:] for line in listing.stdout.splitlines() if line.startswith('worktree ')]
        candidates = []
        ns = {'t': 'http://microsoft.com/schemas/VisualStudio/TeamTest/2010'}
        for root in roots:
            for path in Path(root, '.claude/scratch/test_runs').glob('*.green'):
                try:
                    rec = json.loads(path.read_text(encoding='utf-8-sig'))
                    start = datetime.fromisoformat(rec['startedAt'].replace('Z', '+00:00')).timestamp()
                    finish = datetime.fromisoformat(rec['finishedAt'].replace('Z', '+00:00')).timestamp()
                    if not (red_at < start <= finish <= time.time() and rec['filter'] and rec['treeDigest']):
                        continue
                    trx = Path(rec['trxPath']).read_bytes()
                    if hashlib.sha256(trx).hexdigest().lower() != rec['trxSha256'].lower():
                        continue
                    doc = ET.fromstring(trx)
                    counters = doc.find('t:ResultSummary/t:Counters', ns)
                    results = doc.findall('t:Results/t:UnitTestResult', ns)
                    total = int(counters.get('total'))
                    if not (total > 0 and total == int(counters.get('executed')) == int(counters.get('passed'))
                            == len(results) and int(counters.get('failed')) == 0
                            and all(row.get('outcome') == 'Passed' for row in results)):
                        continue
                    covered = failures.intersection(row.get('testName') for row in results)
                    if covered:
                        candidates.append((rec['treeDigest'], covered))
                except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
                    continue
        if not candidates:
            return False
        script = Path(__file__).resolve().parents[1] / 'scripts/TreeDigest.ps1'
        command = ". '%s'; (Get-TreeDigest -Repo '%s').digest" % (
            str(script).replace("'", "''"), str(repo).replace("'", "''"))
        # Only on an owned open red with a covering record; leave room in the hook deadline.
        digest_run = subprocess.run(['pwsh', '-NoProfile', '-Command', command],
                                    capture_output=True, text=True, timeout=20)
        digest = digest_run.stdout.strip() if digest_run.returncode == 0 else ''
        covered = set().union(*(names for tree, names in candidates if tree == digest and digest))
        return failures <= covered
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def emit(payload):
    print(json.dumps({"hookSpecificOutput": dict(payload, hookEventName="PreToolUse")}))
    sys.exit(0)


def deny(reason):
    emit({"permissionDecision": "deny", "permissionDecisionReason": reason})


def allow():
    print("{}")
    sys.exit(0)


NO_MARKER = ("This full gate run declares no position (`# gate: final|prepush|checkpoint`). Between drive start "
             "and drive close the expected gate count is zero: slice and Part-close verification is "
             "`verify.ps1 -Scope <domains>`. Canon: change_control §Gate cadence.")

RETIRED = """`%s` was deleted from the gate on 2026-08-20; the gate has no width knob.

  narrow test run  -> pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope <domains>
  guards + build   -> the gate's -StaticOnly (the checkout lease only around its build, no marker needed)

Canon: regression_gate §Invocation."""

POST_FAIL = """The last gate ended %s and nothing narrower has run since on this tree covering its failures.

Rerun the failing `-Filter` through `.claude/scripts/run_test_suite.ps1`; read TRX counts
and account for every failed test. Unchanged partitions carry over from the failed run.
Canon: change_control §Post-failure re-verification."""

BROAD_FILTER = (
    "Whole-tier test filter. Per-slice verification is blast-zone sized — narrow to "
    "the sub-namespace the slice touched (e.g. `Tests.Integration.NPCs`), or use "
    "`verify.ps1 -Scope <domain>`, unless you need the tier. A bare tier filter is the "
    "close gate's job and holds this checkout's test lock and a runtime slot. "
    "Canon: change_control §Gate cadence.")


def gate_invocations(command):
    """(argv, shell comment) for each executable gate call in the command."""
    for raw, comment in shell_segments_with_comments(command):
        segment, index = executable_index(raw)
        if index >= len(segment) or not SHELL_RE.fullmatch(
                os.path.basename(segment[index].replace('\\', '/'))):
            continue
        args = segment[index + 1:]
        if any(arg.lower() == "-file" and pos + 1 < len(args)
               and os.path.basename(args[pos + 1].replace('\\', '/')).lower() == "regression_gate.ps1"
               for pos, arg in enumerate(args)):
            yield args, comment


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        allow()

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        allow()

    # Heredoc bodies are data, not this command's execution: writing a SCRIPT that mentions a
    # gate is not running one. Judging the raw string denied that three times in one session.
    command = executable_text((data.get("tool_input") or {}).get("command") or "")
    # CLAUDE_PROJECT_DIR first: the payload's `cwd` is the SHELL's working directory, which
    # drifts into subdirectories and silently forks this hook's state. Measured 2026-08-25 --
    # a session that ran the gate from `.claude/` and `.claude/scripts/` grew a
    # `.claude/.claude/scratch/gate_positions/` and a `.claude/scripts/.claude/...`, so
    # the post-FAIL check read an absent gate_last.md and failed open. Both state paths below are repo-relative.
    repo = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or "."
    session = data.get("session_id")

    invocations = list(gate_invocations(command))
    unmarked = False
    red_notes = []
    for args, comment in invocations:
        invocation = " ".join(args)
        retired = RETIRED_RE.search(invocation)
        if retired:
            deny(RETIRED % retired.group(0))

        if EXEMPT_RE.search(invocation) or WATCH_ONLY_RE.search(invocation):
            continue

        marker = MARKER_RE.search(comment)
        if not marker and len(invocations) == 1:
            # A line's comment binds to its last segment, so `<gate> | tail  # gate: final` puts the marker on
            # `tail`. With one gate in the command, any marker in it declares that gate.
            marker = next((m for m in (MARKER_RE.search(c) for _, c in shell_segments_with_comments(command)) if m),
                          None)
        # A nudge, never a deny: the marker is text the caller writes, so it cannot be a pass condition
        # (instruction_quality §3); whether a full gate run is warranted is the caller's judgment.
        position = marker.group(1).lower() if marker else None
        if position not in POSITIONS:
            unmarked = True

        # A red is still open until something narrower has run against the fix.
        red = open_red_at(repo)
        if red is not None:
            red_at, owner = red
            if not session or not owner or owner != session:
                who = 'another session' if owner else 'an unknown owner'
                red_notes.append(f'The last gate red belongs to {who}. Read gate_last.md and choose the '
                                 'fix\'s verification width (change_control §Gate cadence).')
                continue
            state = read_json_salvage(os.path.join(
                repo, STATE_DIR, "%s.json" % re.sub(r"[^A-Za-z0-9_-]", "_", session or "unknown")))
            try:
                narrow_at = float(state.get("narrow_verify_at", 0))
            except (TypeError, ValueError):
                narrow_at = 0.0
            if narrow_at <= red_at and not completed_narrow_covers_red(repo, red_at):
                deny(POST_FAIL % "in a red")

    if invocations:
        notes = ([NO_MARKER] if unmarked else []) + list(dict.fromkeys(red_notes))
        if notes:
            emit({"additionalContext": '\n'.join(notes)})
        allow()

    if SUITE_RE.search(command):
        # Record the narrow run itself, so the post-red check above keys on evidence that a
        # cheaper verification actually happened rather than on anyone saying it did.
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session or "unknown")
        path = os.path.join(repo, STATE_DIR, "%s.json" % safe)
        update_json_locked(path, lambda state: state.__setitem__("narrow_verify_at", time.time()))
        sweep_stale_state(os.path.dirname(path), keep=path)

        if BROAD_FILTER_RE.search(command):
            emit({"additionalContext": BROAD_FILTER})

    allow()


if __name__ == "__main__":
    main()
