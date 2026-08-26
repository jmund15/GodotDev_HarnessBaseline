#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — bound how often the regression gate runs.

Enforces `change_control` §Gate cadence. The rule was already documented on three
surfaces and the over-gating recurred across every drive type anyway, so the gap is
enforcement, not documentation.

Two decisions a gate invocation skips silently.

  1. HOW OFTEN. §Gate cadence budgets ONE full gate per drive, at its close. Between drive start and drive close the expected
count is zero — commits batch to the close gate, and slice/Part verification runs
through `verify.ps1`, which is not a gate and is never checked here. `checkpoint`
carries the table's own allowance ("one or two ... orchestrator judgment"). There is
no reason clause: it only collects a plausible sentence, and the failing case always
has one. (Measured 2026-08-20: the mid-chain gate that prompted this hook stated
"protect submodule work across sessions", which has a cheaper answer — push the ref.)

  2. WIDTH AFTER A RED. §Gate cadence: re-verification width follows the fix's
     blast-radius class, never a reflex full re-run. The reflex is invisible to the
     cadence check above, because a post-failure re-run is honestly marked
     `# gate: final` — uncapped by design, since the close gate is mandatory. So the
     most wasteful shape in the whole workflow (red -> fix -> re-run everything, ~15
     min to re-prove one test) was the one shape this guard waved through.

     Enforced on evidence, not on a declaration: gate_last.md records the last
     VERDICT and its mtime, and this hook already sees narrow runs. After a FAIL, a
     full gate is denied until SOME narrower run has happened since that FAIL. That
     cannot pick the correct width — which needs judgment about what the fix touched
     — but it makes the cheap check happen first, in the order that makes the
     expensive one either unnecessary or justified. `-RetryOnly` is exempt: it IS the
     sanctioned post-failure instrument.

Why this cannot live in a skill: `change_control`'s trigger is "when deciding how a
change is gated". A when-deciding trigger cannot fire once a plan file has already
named a tier, because the agent never enters the decision state. The deny text
therefore stands alone; the citation is provenance, not the mechanism.

Scope — TDD and per-slice runs are deliberately untouched:
- A `-Filter` naming a suite or a sub-namespace passes silently. Strict TDD and
  blast-zone slice verification are the prescribed cadence, not the defect.
- Only a BARE tier filter (`Tests.Logic`/`Tests.Integration`/`Tests.Sanity` with
  nothing after it) draws an advisory, never a deny.
- Reading the gate script is not invoking it; see GATE_RE.

Fail posture: marker and cap checks fail CLOSED — they are the mechanism. State that
cannot be read degrades to a zero count (a missed cap), never to a crashed hook.

Proof harness: `.claude/tests/test_gate_cadence_guard.py`.
"""
import io
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import read_json_salvage, write_json_atomic

POSITIONS = ("final", "prepush", "checkpoint")
# §Gate cadence: "Exceptionally long drive | One or two full-gate checkpoints".
CHECKPOINT_CAP = 2
STATE_DIR = os.path.join(".claude", "scratch", "gate_positions")
GATE_LAST = os.path.join(".claude", "scratch", "test_runs", "gate_last.md")
# A red older than this is history, not this drive's open failure.
STALE_FAIL_SEC = 12 * 3600

# An INVOCATION, never a mention: a `pwsh ... -File <path>` shape. Matching the bare path
# denied every read-only inspection (grep, cat, wc -l, git show); matching `pwsh` without
# `-File` still denied `pwsh -Command` one-liners that merely NAME the script, which is how
# a syntax check of the gate reads.
GATE_RE = re.compile(
    r"(?:^|[|;&]|\bnohup\b|\btimeout\b)\s*(?:pwsh|powershell)(?:\.exe)?\b"
    r"[^|;&]*?-File\s+\S*\bregression_gate\.ps1\b", re.I | re.M)
MARKER_RE = re.compile(r"#\s*gate:\s*(\w+)", re.M)
EXEMPT_RE = re.compile(r"-(StaticOnly|QueueStatus|FromQueue|RetryOnly)\b", re.I)

# The width flags were deleted from the gate 2026-08-20, so these no longer bind a
# parameter — a stale invocation copied from an old plan, doc or memory file now dies
# on a PowerShell binding error instead. Catching them here names the replacement.
RETIRED_RE = re.compile(r"-(SmokeImport|Smoke|Targeted|SkipStatic)\b", re.I)

SUITE_RE = re.compile(r"run_test_suite\.ps1|dotnet\s+test|verify\.ps1")
BROAD_FILTER_RE = re.compile(
    r"FullyQualifiedName~Tests\.(Logic|Integration|Sanity)(?![.\w])", re.I)


def gates_taken(repo, session, position, bump=False):
    """Gates taken at `position` this session. Returns the count BEFORE any bump."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session or "unknown")
    path = os.path.join(repo, STATE_DIR, "%s.json" % safe)
    state = read_json_salvage(path)
    try:
        n = int(state.get(position, 0))
    except (TypeError, ValueError):
        n = 0
    if bump:
        state[position] = n + 1
        write_json_atomic(path, state)
        sweep_stale_state(os.path.dirname(path), keep=path)
    return n


def sweep_stale_state(directory, keep=None, max_age_sec=7 * 24 * 3600):
    """Drop per-session state files older than a week (§15: bounded state).

    One file per session accumulates forever otherwise, and the directory is gitignored, so
    nothing in the repo ever surfaces the growth. Best-effort and silent: losing a stale
    counter costs at most one un-capped gate, while raising here would block a tool call.
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
    """When the last recorded gate verdict was a red, return when it landed; else None.

    Reads the gate's own artifact rather than tracking verdicts here: the gate is the only
    thing that knows how a run ended, and a PreToolUse hook never sees an exit code.
    """
    path = os.path.join(repo, GATE_LAST)
    try:
        mtime = os.path.getmtime(path)
        head = io.open(path, encoding="utf-8", errors="replace").read(400)
    except OSError:
        return None
    if time.time() - mtime > STALE_FAIL_SEC:
        return None
    return mtime if re.search(r"^VERDICT=(FAIL|WARN)\b", head, re.M) else None


def emit(payload):
    print(json.dumps({"hookSpecificOutput": dict(payload, hookEventName="PreToolUse")}))
    sys.exit(0)


def deny(reason):
    emit({"permissionDecision": "deny", "permissionDecisionReason": reason})


def allow():
    print("{}")
    sys.exit(0)


NO_MARKER = """Declare what this gate backs. Append one:

  # gate: final        drive close, before commits
  # gate: prepush      before push/PR
  # gate: checkpoint   exceptionally long drive (cap %d/session)

Between drive start and drive close the expected gate count is ZERO — slice and
Part-close verification is `verify.ps1 -Scope <domains>`.
Canon: change_control §Gate cadence.""" % CHECKPOINT_CAP

CAP_SPENT = """Gate cap reached — %d `%s` gate(s) already run this session (cap %d).

  verifying a slice or Part  -> verify.ps1 -Scope <domains>
  work is complete           -> # gate: final
  about to push/PR           -> # gate: prepush

Canon: change_control §Gate cadence."""

RETIRED = """`%s` was deleted from the gate on 2026-08-20; the gate has no width knob.

  narrow test run  -> pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope <domains>
  guards + build   -> the gate's -StaticOnly (no test lock, no marker needed)

Canon: change_control §Gate cadence."""

POST_FAIL = """The last gate ended %s and nothing narrower has run since.

Re-verify the fix at its blast-radius width first, then re-run the close gate:

  integration-local  -> regression_gate.ps1 -RetryOnly        (only non-green batches)
  domain-spanning    -> verify.ps1 -Scope <domains>
  shared-state       -> whole-tier: base classes, autoloads, teardown patterns, factory defaults

Say which of the three the fix is and why — an unnamed width is indistinguishable from a
reflex full re-run. Canon: change_control §Gate cadence."""

BROAD_FILTER = (
    "Whole-tier test filter. Per-slice verification is blast-zone sized — narrow to "
    "the sub-namespace the slice touched (e.g. `Tests.Integration.NPCs`), or use "
    "`verify.ps1 -Scope <domain>`, unless you need the tier. A bare tier filter is the "
    "close gate's job and takes the machine-global GdUnit4 pipe. "
    "Canon: change_control §Gate cadence.")


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        allow()

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        allow()

    command = (data.get("tool_input") or {}).get("command") or ""
    # CLAUDE_PROJECT_DIR first: the payload's `cwd` is the SHELL's working directory, which
    # drifts into subdirectories and silently forks this hook's state. Measured 2026-08-25 --
    # a session that ran the gate from `.claude/` and `.claude/scripts/` grew a
    # `.claude/.claude/scratch/gate_positions/` and a `.claude/scripts/.claude/...`, so the
    # checkpoint cap counted from zero in each and the post-FAIL check read an absent
    # gate_last.md and failed open. Both state paths below are repo-relative.
    repo = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or "."
    session = data.get("session_id")

    if GATE_RE.search(command):
        retired = RETIRED_RE.search(command)
        if retired:
            deny(RETIRED % retired.group(0))

        if EXEMPT_RE.search(command):
            allow()

        marker = MARKER_RE.search(command)
        position = marker.group(1).lower() if marker else None
        if position not in POSITIONS:
            deny(NO_MARKER)

        # A red is still open until something narrower has run against the fix.
        red_at = open_red_at(repo)
        if red_at is not None:
            state = read_json_salvage(os.path.join(
                repo, STATE_DIR, "%s.json" % re.sub(r"[^A-Za-z0-9_-]", "_", session or "unknown")))
            try:
                narrow_at = float(state.get("narrow_verify_at", 0))
            except (TypeError, ValueError):
                narrow_at = 0.0
            if narrow_at <= red_at:
                deny(POST_FAIL % "in a red")

        # final/prepush are the mandatory stops: uncapped.
        if position in ("final", "prepush"):
            allow()

        taken = gates_taken(repo, session, position)
        if taken >= CHECKPOINT_CAP:
            deny(CAP_SPENT % (taken, position, CHECKPOINT_CAP))

        gates_taken(repo, session, position, bump=True)
        allow()

    if SUITE_RE.search(command):
        # Record the narrow run itself, so the post-red check above keys on evidence that a
        # cheaper verification actually happened rather than on anyone saying it did.
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session or "unknown")
        path = os.path.join(repo, STATE_DIR, "%s.json" % safe)
        state = read_json_salvage(path)
        state["narrow_verify_at"] = time.time()
        write_json_atomic(path, state)

        if BROAD_FILTER_RE.search(command):
            emit({"additionalContext": BROAD_FILTER})

    allow()


if __name__ == "__main__":
    main()
