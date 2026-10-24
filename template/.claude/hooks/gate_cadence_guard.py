#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — gate-cadence advice and session-owned post-red verification.

`change_control` §Gate cadence owns the rule. A full gate after this session's recent FAIL/WARN
is denied until this session has invoked narrower verification, or completed suite records from
linked checkouts prove a post-red run on the current content covering every listed failed test.
A peer's red or an old-format artifact advises; it never inherits the peer's verification debt.
`-RetryOnly` is checked like a full gate: a gate-length rerun repairs nothing. The one exception is a red whose
every failed test shows a timeout message: it admits a single `-RetryOnly`, and a retry that fails again denies.
A red is read from the newest
gate_last.md across the checkout and its linked worktrees, since an isolated-worktree gate writes only its own.

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
from sidecar_argv import executable_index, literal_launcher_body, shell_segments_with_comments

POSITIONS = ("final", "prepush", "checkpoint")
STATE_DIR = os.path.join(".claude", "scratch", "gate_positions")
GATE_LAST = os.path.join(".claude", "scratch", "test_runs", "gate_last.md")
# A red older than this is history, not this drive's open failure.
STALE_FAIL_SEC = 12 * 3600

# Inspect the executable token of each shell segment, not a textual prefix: a gate
# inside grouping or a conditional still runs, while an argument mentioning it does not.
SHELL_RE = re.compile(r"(?:pwsh|powershell)(?:\.exe)?", re.I)
MARKER_RE = re.compile(r"#\s*gate:\s*(\w+)", re.I)
EXEMPT_RE = re.compile(r"-(StaticOnly|RouteOnly)\b", re.I)
RETRY_RE = re.compile(r"-RetryOnly\b", re.I)
TIMEOUT_RE = re.compile(r"time[sd]?[ -]?out|wall-clock", re.I)
FAILING_SECTION_RE = re.compile(r"### [^\n]+ failing tests\r?\n((?:- [^\n]+\r?\n?)+)")
# -Watch only streams a detached run's OUT; combined with -Detach the script spawns a gate first.
WATCH_ONLY_RE = re.compile(r"^(?!.*-Detach\b).*-Watch\b", re.I | re.S)

# The width flags were deleted from the gate 2026-08-20, so these no longer bind a
# parameter — a stale invocation copied from an old plan, doc or memory file now dies
# on a PowerShell binding error instead. Catching them here names the replacement.
RETIRED_RE = re.compile(r"-(SmokeImport|Smoke|Targeted|SkipStatic)\b", re.I)

SUITE_SCRIPTS = ("run_test_suite.ps1", "verify.ps1")
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


def gate_roots(repo):
    """The checkout and its linked worktrees; only the checkout when git cannot list them."""
    roots = [repo]
    try:
        listing = subprocess.run(['git', '-C', repo, 'worktree', 'list', '--porcelain'],
                                 capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return roots
    if listing.returncode:
        return roots
    here = os.path.normcase(os.path.abspath(repo))
    for line in listing.stdout.splitlines():
        if line.startswith('worktree ') and os.path.normcase(os.path.abspath(line[9:])) != here:
            roots.append(line[9:])
    return roots


def red_failure_rows(root):
    """(failing-test rows, failed= counts) of root's gate_last.md, or None when it is unreadable."""
    try:
        detail = Path(root, GATE_LAST).read_text(encoding='utf-8-sig')
    except OSError:
        return None
    rows = [line[2:] for section in FAILING_SECTION_RE.findall(detail) for line in section.splitlines()]
    return rows, [int(n) for n in re.findall(r'\bfailed=(\d+)', detail)]


def red_failures(root):
    """The failed test names in root's gate_last.md, or None when its listing is truncated or count-only."""
    parsed = red_failure_rows(root)
    if parsed is None:
        return None
    rows, counts = parsed
    failures = {row.split(' :: ', 1)[0].strip() for row in rows}
    if not failures or sum(counts) != len(failures):
        return None
    return failures


def red_timeout_only(root):
    """True when root's red lists every failed test with a timeout message and the listing is complete."""
    parsed = red_failure_rows(root)
    if parsed is None:
        return False
    rows, counts = parsed
    if not rows or sum(counts) != len(rows):
        return False
    return all(' :: ' in row and TIMEOUT_RE.search(row.split(' :: ', 1)[1]) for row in rows)


def newest_ledger_record(root):
    """The newest gate ledger record of root as a dict, or None."""
    newest = None
    for path in Path(root, '.claude/scratch/gate_ledger').glob('*.result.json'):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or stamp > newest[0]:
            newest = (stamp, path)
    if newest is None:
        return None
    try:
        rec = json.loads(newest[1].read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def first_retry_owed(root, session):
    """True when root's newest gate run is `session`'s red and was not itself a -RetryOnly run."""
    rec = newest_ledger_record(root)
    return (rec is not None and rec.get('status') in ('FAIL', 'WARN') and rec.get('sessionId') == session
            and 'retry-only' not in str(rec.get('mode') or 'retry-only'))


def red_digest(root, session):
    """The tree digest of root's newest gate run when that run is a red of `session`, else None.

    The gate ledger (regression_gate.ps1 Complete-Gate) holds the full digest; gate_last.md does not.
    """
    rec = newest_ledger_record(root)
    if rec is not None and rec.get('status') in ('FAIL', 'WARN') and rec.get('sessionId') == session:
        return str(rec.get('treeDigest') or '').strip().lower() or None
    return None


def open_red_at(repo):
    """Return (mtime, session, root) for a recent red in the newest gate artifact, else None.

    The newest gate_last.md across the checkout and its worktrees decides, so a later green anywhere closes the red.
    A FAIL is red. A WARN is red only when it names a failed test: a clean count drop has none to rerun.
    """
    newest = None
    for root in gate_roots(repo):
        path = os.path.join(root, GATE_LAST)
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            continue
        if newest is None or stamp > newest[0]:
            newest = (stamp, root, path)
    if newest is None:
        return None
    mtime, root, path = newest
    try:
        text = io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    if time.time() - mtime > STALE_FAIL_SEC:
        return None
    if re.search(r"^VERDICT=WARN\b", text, re.M):
        if not (re.search(r"^### [^\n]+ failing tests", text, re.M) or re.search(r"\bfailed=[1-9]", text)):
            return None
    elif not re.search(r"^VERDICT=FAIL\b", text, re.M):
        return None
    session = re.search(r'^SESSION=([^\r\n]*)', text, re.M)
    return mtime, (session.group(1).strip() or None) if session else None, root


UNCOVERED, UNREPAIRED, COVERED = 'uncovered', 'unrepaired', 'covered'


def completed_narrow_covers_red(repo, red_at, red_tree=None, content_repo=None):
    """Read runner-owned .green records from linked checkouts; bind coverage to content and TRX bytes.

    `repo` is the checkout holding the red; `content_repo` (default `repo`) is the checkout the fix is edited in.
    COVERED needs every failed test passing in a completed run on `content_repo`'s current content. When
    `red_tree` is known, that content must also differ from the red's: a pass on the same content shows a flake,
    not a repair.
    """
    try:
        failures = red_failures(repo)
        if failures is None:
            return UNCOVERED  # A truncated failure listing or count-only WARN cannot establish coverage.
        roots = gate_roots(repo)
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
            return UNCOVERED
        script = Path(__file__).resolve().parents[1] / 'scripts/TreeDigest.ps1'
        command = ". '%s'; (Get-TreeDigest -Repo '%s').digest" % (
            str(script).replace("'", "''"), str(content_repo or repo).replace("'", "''"))
        # Only on an owned open red with a covering record; leave room in the hook deadline.
        digest_run = subprocess.run(['pwsh', '-NoProfile', '-Command', command],
                                    capture_output=True, text=True, timeout=20)
        digest = digest_run.stdout.strip() if digest_run.returncode == 0 else ''
        covered = set().union(*(names for tree, names in candidates if tree == digest and digest))
        if not failures <= covered:
            return UNCOVERED
        return UNREPAIRED if red_tree and digest.lower() == red_tree else COVERED
    except (OSError, ValueError, subprocess.SubprocessError):
        return UNCOVERED


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
and account for every failed test. Unchanged partitions carry over from the failed run. A test
that passes again alone failed for a cause (a wall-clock wait, shared state):
repair it before another gate of any length.
Canon: change_control §Post-failure re-verification."""

UNREPAIRED_FAIL = """The last gate's failed tests pass again alone, but the content is unchanged since the red.

A pass on unchanged content shows a flake with a cause (a wall-clock wait, shared state), not a repair.
Find the cause and change the code or test, then gate once. If it cannot be repaired, stop and ask the
owner to accept it as a named known failure: the commit then says `Verified (with known failures: ...)`
and needs no further gate.
Canon: change_control §Post-failure re-verification."""

BROAD_FILTER = (
    "Whole-tier test filter. Per-slice verification is blast-zone sized — narrow to "
    "the sub-namespace the slice touched (e.g. `Tests.Integration.NPCs`), or use "
    "`verify.ps1 -Scope <domain>`, unless you need the tier. A bare tier filter is the "
    "close gate's job and holds this checkout's test lock and a runtime slot. "
    "Canon: change_control §Gate cadence.")


def executed_segments(command):
    """(segment, program index, shell comment) for each command the shell runs, reading literal `bash -c` bodies."""
    for raw, comment in shell_segments_with_comments(command):
        segment, index = executable_index(raw)
        body = literal_launcher_body(segment, index) if index < len(segment) else None
        if body is not None:
            yield from executed_segments(body.body)
        else:
            yield segment, index, comment


def program_name(segment, index):
    return os.path.basename(segment[index].replace('\\', '/')).lower() if index < len(segment) else ''


def file_arg(args, names):
    """True when `-File <name>` binds one of `names`."""
    return any(arg.lower() == "-file" and pos + 1 < len(args)
               and os.path.basename(args[pos + 1].replace('\\', '/')).lower() in names
               for pos, arg in enumerate(args))


def gate_invocations(command):
    """(argv, shell comment) for each executable gate call in the command."""
    for segment, index, comment in executed_segments(command):
        if not SHELL_RE.fullmatch(program_name(segment, index)):
            continue
        args = segment[index + 1:]
        if file_arg(args, ("regression_gate.ps1",)):
            yield args, comment


def suite_run(command):
    """True when the command runs a narrow suite: run_test_suite.ps1, verify.ps1 or `dotnet test`."""
    for segment, index, _ in executed_segments(command):
        name = program_name(segment, index)
        args = segment[index + 1:]
        if SHELL_RE.fullmatch(name) and file_arg(args, SUITE_SCRIPTS):
            return True
        if name in SUITE_SCRIPTS or (name == "dotnet" and args[:1] == ["test"]):
            return True
    return False


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
        retry_only = bool(RETRY_RE.search(invocation))

        marker = MARKER_RE.search(comment)
        if not marker and len(invocations) == 1:
            # A line's comment binds to its last segment, so `<gate> | tail  # gate: final` puts the marker on
            # `tail`. With one gate in the command, any marker in it declares that gate.
            marker = next((m for m in (MARKER_RE.search(c) for _, c in shell_segments_with_comments(command)) if m),
                          None)
        # A nudge, never a deny: the marker is text the caller writes, so it cannot be a pass condition
        # (instruction_quality §3); whether a full gate run is warranted is the caller's judgment.
        position = marker.group(1).lower() if marker else None
        if position not in POSITIONS and not retry_only:
            unmarked = True

        # A red is still open until something narrower has run against the fix.
        red = open_red_at(repo)
        if red is not None:
            red_at, owner, red_root = red
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
            # A failure listing that cannot be parsed names nothing to cover: any narrow run since the red clears it.
            covered = completed_narrow_covers_red(red_root, red_at, red_digest(red_root, owner), repo)
            if covered == UNREPAIRED:
                deny(UNREPAIRED_FAIL)
            if (covered == UNCOVERED and retry_only and red_timeout_only(red_root)
                    and first_retry_owed(red_root, owner)):
                continue
            if covered == UNCOVERED and not (narrow_at > red_at and red_failures(red_root) is None):
                deny(POST_FAIL % "in a red")

    if invocations:
        notes = ([NO_MARKER] if unmarked else []) + list(dict.fromkeys(red_notes))
        if notes:
            emit({"additionalContext": '\n'.join(notes)})
        allow()

    if suite_run(command):
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
