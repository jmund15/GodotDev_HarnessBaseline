"""Re-runnable proof for the nested-fan-out check in hooks/dispatch_mechanism_guard.py.

instruction_quality §14: registration proves wiring, not matching. Each case feeds a real PreToolUse
payload and asserts on the emitted channel. The transcript carries the orchestration markers so the
first check passes and only the nested-fan-out check is under test. Negative cases include a brief
that merely NAMES an engine (rules/harness_tooling.md: match the action, never the noun) and a real
lens brief produced by tools/lens_briefs.py.

    python3 .claude/tests/test_nested_fanout_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HOOK = os.path.join(HERE, "..", "hooks", "dispatch_mechanism_guard.py")
BRIEFS = os.path.join(HERE, "..", "tools", "lens_briefs.py")

ALLOW, DENY = "allow", "deny"
MARKERS = ("Dispatch Shape — decide this " + "FIRST") + "\n" + ("Every dispatched agent " + "pins both") + "\n"


def checked_run(args, allowed=(0, 2), **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode not in allowed or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]))
    return result


def run(payload):
    r = checked_run([sys.executable, HOOK], input=json.dumps(payload, ensure_ascii=False),
                    capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ALLOW, ""
    hook = (json.loads(out).get("hookSpecificOutput") or {})
    return (DENY, hook.get("permissionDecisionReason", "")) if hook.get("permissionDecision") == "deny" else (ALLOW, out)


def main():
    tmp = tempfile.mkdtemp(prefix="nestedfanout_")
    transcript = os.path.join(tmp, "t.jsonl")
    with open(transcript, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "user", "content": MARKERS}, ensure_ascii=False) + "\n")

    def agent(prompt):
        return {"tool_name": "Agent", "transcript_path": transcript, "tool_input": {"prompt": prompt, "subagent_type": "general-purpose"}}

    def workflow(jobs):
        return {"tool_name": "Workflow", "transcript_path": transcript,
                "tool_input": {"scriptPath": ".claude/workflows/dispatch.js", "args": {"jobs": jobs}}}

    def brief(name, text):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return {"label": name, "promptPath": p, "model": "sonnet", "effort": "low", "agentType": "general-purpose"}

    cases = [
        ("agent told to execute /review_pr", DENY, agent(
            "You are reviewing PR #7. Read `.claude/commands/review_pr.md` for the complete review procedure, then execute it step by step.")),
        ("agent told to use /delegate", DENY, agent(
            "Use /delegate to run the remaining review lenses.")),
        ("agent told to fan out through review_fanout.js", DENY, agent(
            "Phase 2c requires you to dispatch 4-7 lenses through `.claude/workflows/review_fanout.js`.")),
        ("agent told to call Workflow", DENY, agent(
            "Run the lenses: invoke Workflow({ scriptPath: '.claude/workflows/dispatch.js', args: {...} }) and consolidate.")),
        ("agent doing one bounded review", ALLOW, agent(
            "You are error-hunter for PR #7. Read the changed files in the worktree and return a JSON findings array.")),
        ("agent brief that only NAMES the engine", ALLOW, agent(
            "Context: these findings were produced by review_fanout.js earlier. Verify each FIX anchor against the file and report.")),
        ("dispatch.js brief telling the delegate to dispatch", DENY, workflow([brief(
            "bad.md", "Read .claude/commands/plan_check.md and execute it step by step for this plan.")])),
        ("dispatch.js brief doing bounded work", ALLOW, workflow([brief(
            "good.md", "Diff OLD vs NEW per changed method. Flag every dropped branch as a BLOCKER. Return JSON.")])),
    ]

    # A real generated lens brief must pass: lens_briefs.py strips the caller-facing spawn rules.
    ctx = os.path.join(tmp, "ctx.md")
    with open(ctx, "w", encoding="utf-8") as fh:
        fh.write("# CONTEXT\nnothing\n")
    try:
        gen = checked_run(
            [sys.executable, BRIEFS, "--keys", "code-reviewer", "error-hunter",
             "--out", tmp, "--prefix", "t1", "--pr-num", "1", "--branch", "x",
             "--context-path", ctx, "--model", "sonnet"],
            allowed=(0,), capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
            timeout=60,
        )
    except RuntimeError as exc:
        print("FAIL lens_briefs.py did not run:", exc)
        return 1
    jobs = json.load(open(os.path.join(tmp, "jobs_t1.json"), encoding="utf-8"))
    cases.append(("real lens briefs from lens_briefs.py", ALLOW, workflow(jobs)))

    helper_cases = [
        ("the proof helper rejects an abnormal exit", "raise SystemExit(7)"),
        ("the proof helper rejects a traceback even with exit zero",
         "print('Traceback (most recent call last):')"),
    ]
    failures = []
    for name, source in helper_cases:
        try:
            checked_run([sys.executable, "-c", source], capture_output=True, text=True, timeout=60)
            ok = False
        except RuntimeError:
            ok = True
        print(("ok  " if ok else "FAIL") + " " + name)
        if not ok:
            failures.append(name)

    for name, want, payload in cases:
        try:
            got, reason = run(payload)
        except RuntimeError as exc:
            got, reason = "crash", str(exc)
        mark = "ok  " if got == want else "FAIL"
        print(f"{mark} {name}: want {want}, got {got}" + (f" — {reason[:90]}" if got == DENY else ""))
        if got != want:
            failures.append(name)
    total = len(cases) + len(helper_cases)
    print(f"\n{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
