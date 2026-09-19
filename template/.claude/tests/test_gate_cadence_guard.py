"""Re-runnable proof for hooks/gate_cadence_guard.py.

instruction_quality §14: registration proves wiring, not matching. Each case below
feeds the hook a real PreToolUse payload and asserts on the channel it emits, so a
regression shows up as a failing case rather than as a surprise denial mid-session.

    python3 .claude/tests/test_gate_cadence_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "hooks", "gate_cadence_guard.py")
GATE = "pwsh -NoProfile -File .claude/scripts/regression_gate.ps1"
VERIFY = "pwsh -NoProfile -File .claude/scripts/verify.ps1"

ALLOW, DENY, NUDGE = "allow", "deny", "nudge"


def run(command, session, repo):
    payload = {"tool_name": "Bash", "session_id": session, "cwd": repo,
               "tool_input": {"command": command}}
    # The hook roots its state on CLAUDE_PROJECT_DIR before the payload cwd. Inherited from a session,
    # it points every case at one real checkout, so gate counts leak across runs. Each case owns its repo.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=90, env=env)
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ALLOW, ""
    try:
        doc = json.loads(out)
    except ValueError:
        return "UNPARSED", out
    hook = doc.get("hookSpecificOutput") or {}
    if hook.get("permissionDecision") == "deny":
        return DENY, hook.get("permissionDecisionReason", "")
    if hook.get("additionalContext"):
        return NUDGE, hook["additionalContext"]
    return ALLOW, out


CASES = [
    # --- reading the gate script is not invoking it -------------------------
    ("read-only grep of the gate script is not an invocation",
     'grep -n "QueueStatus" .claude/scripts/regression_gate.ps1', ALLOW, ""),
    ("read-only wc of the gate script",
     "wc -l .claude/scripts/regression_gate.ps1", ALLOW, ""),
    ("git show of the gate script",
     "git show HEAD:.claude/scripts/regression_gate.ps1 | head -40", ALLOW, ""),
    ("editing the gate's doc is not an invocation",
     'sed -i "s/x/y/" .claude/commands/regression_gate.md', ALLOW, ""),
    ("a pwsh -Command that merely NAMES the script is not an invocation",
     'pwsh -NoProfile -Command "ParseFile(.claude/scripts/regression_gate.ps1)"', ALLOW, ""),

    # --- the marker is mandatory -------------------------------------------
    ("bare invocation with no marker is denied",
     GATE, DENY, "Declare what this gate backs"),
    ("bogus marker is denied",
     GATE + "  # gate: whenever", DENY, "Declare what this gate backs"),
    ("the retired `mid` marker is no longer a position",
     GATE + "  # gate: mid", DENY, "Declare what this gate backs"),
    ("-StaticOnly is exempt from the marker",
     GATE + " -StaticOnly", ALLOW, ""),
    ("-QueueStatus is exempt",
     GATE + " -QueueStatus", ALLOW, ""),

    # --- the mandatory stops ------------------------------------------------
    ("final is allowed uncapped",
     GATE + "  # gate: final", ALLOW, ""),
    ("prepush is allowed",
     GATE + "  # gate: prepush", ALLOW, ""),

    # --- retired width flags name their replacement -------------------------
    ("-Smoke is retired and names verify.ps1",
     GATE + " -Smoke  # gate: final", DENY, "verify.ps1"),
    ("-Targeted is retired",
     GATE + " -Targeted NPCs  # gate: final", DENY, "verify.ps1"),
    ("-SkipStatic is retired",
     GATE + " -SkipStatic  # gate: final", DENY, "verify.ps1"),
    ("a retired flag is caught even with no marker at all",
     GATE + " -SmokeImport", DENY, "verify.ps1"),

    # --- the narrow instrument is never gated -------------------------------
    ("verify.ps1 needs no marker and is never denied",
     VERIFY + " -Scope NPCs,AI", ALLOW, ""),
    ("whole-tier filter is nudged, not denied",
     'pwsh -File .claude/scripts/run_test_suite.ps1 '
     '-Filter "FullyQualifiedName~Tests.Integration" -Label x', NUDGE, "blast-zone"),
    ("blast-zone filter is silent",
     'pwsh -File .claude/scripts/run_test_suite.ps1 '
     '-Filter "FullyQualifiedName~Tests.Integration.NPCs" -Label x', ALLOW, ""),
]

# Cases that depend on accumulated per-session state, run in order on one session.
SEQUENCE = [
    ("first checkpoint allowed", GATE + "  # gate: checkpoint", ALLOW, ""),
    ("second checkpoint allowed", GATE + "  # gate: checkpoint", ALLOW, ""),
    ("third checkpoint denied", GATE + "  # gate: checkpoint", DENY, "Gate cap reached"),
    ("final is never capped", GATE + "  # gate: final", ALLOW, ""),
    ("prepush is never capped", GATE + "  # gate: prepush", ALLOW, ""),
]


def write_gate_last(repo, verdict, age_sec=0):
    """Plant a gate_last.md carrying `verdict`, aged `age_sec` seconds into the past."""
    d = os.path.join(repo, ".claude", "scratch", "test_runs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "gate_last.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# Regression Gate detail\n\nVERDICT=%s (exit 1)\n" % verdict)
    stamp = time.time() - age_sec
    os.utime(p, (stamp, stamp))
    return p


def post_red_cases(run_one):
    """The post-FAIL reflex check. Each case owns its repo — the signal is on disk."""
    out = []

    # 1. an open red, nothing narrower since -> the full gate is denied
    repo = tempfile.mkdtemp(prefix="postred_a_")
    write_gate_last(repo, "FAIL")
    out.append(("open red + no narrow run -> full gate denied",
                run_one(GATE + "  # gate: final", "red001", repo), DENY, "nothing narrower has run"))

    # 2. -RetryOnly is the sanctioned instrument, never blocked
    out.append(("-RetryOnly is exempt while a red is open",
                run_one(GATE + " -RetryOnly  # gate: final", "red002", repo), ALLOW, ""))

    # 3. a narrow run since the red clears the block
    repo2 = tempfile.mkdtemp(prefix="postred_b_")
    write_gate_last(repo2, "FAIL", age_sec=60)
    run_one(VERIFY + " -Scope NPCs", "red003", repo2)          # the narrow run
    out.append(("narrow run since the red -> full gate allowed",
                run_one(GATE + "  # gate: final", "red003", repo2), ALLOW, ""))

    # 4. the narrow run must be THIS session's, not another's
    out.append(("another session's narrow run does not clear it",
                run_one(GATE + "  # gate: final", "red004", repo2), DENY, "nothing narrower has run"))

    # 5. a green last verdict blocks nothing
    repo3 = tempfile.mkdtemp(prefix="postred_c_")
    write_gate_last(repo3, "PASS")
    out.append(("a green last verdict blocks nothing",
                run_one(GATE + "  # gate: final", "red005", repo3), ALLOW, ""))

    # 6. a stale red is history, not this drive's open failure
    repo4 = tempfile.mkdtemp(prefix="postred_d_")
    write_gate_last(repo4, "FAIL", age_sec=13 * 3600)
    out.append(("a red older than the staleness bound is history",
                run_one(GATE + "  # gate: final", "red006", repo4), ALLOW, ""))

    # 7. no gate_last.md at all -> nothing to key on
    repo5 = tempfile.mkdtemp(prefix="postred_e_")
    out.append(("no prior verdict on disk -> allowed",
                run_one(GATE + "  # gate: final", "red007", repo5), ALLOW, ""))

    return out


def main():
    repo = tempfile.mkdtemp(prefix="gatecadence_")
    failures = []

    for i, (label, command, expected, needle) in enumerate(CASES):
        got, reason = run(command, "iso%03d" % i, repo)
        ok = got == expected and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                            % (label, expected, got, reason[:200]))

    print("  -- sequenced (one session) --")
    for label, command, expected, needle in SEQUENCE:
        got, reason = run(command, "seq001", repo)
        ok = got == expected and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                            % (label, expected, got, reason[:200]))

    print("  -- post-red reflex check --")
    post = post_red_cases(lambda cmd, sid, rp: run(cmd, sid, rp))
    for label, (got, reason), expected, needle in post:
        ok = got == expected and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                            % (label, expected, got, reason[:200]))

    total = len(CASES) + len(SEQUENCE) + len(post)
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
