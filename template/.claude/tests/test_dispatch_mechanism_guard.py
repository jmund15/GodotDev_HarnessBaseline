"""Re-runnable proof for hooks/dispatch_mechanism_guard.py.

instruction_quality §14: registration proves wiring, not matching. Each case feeds a real
PreToolUse payload and asserts on the emitted channel, and the last group asserts the guard's
markers still exist in skills/orchestration/SKILL.md — a marker that drifted out of the skill
turns the guard into an unconditional denial of every dispatch.

    python3 .claude/tests/test_dispatch_mechanism_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "dispatch_mechanism_guard.py")
SKILL = os.path.join(HERE, "..", "skills", "orchestration", "SKILL.md")

ALLOW, DENY = "allow", "deny"

# Assembled from fragments for the same reason the hook does it: a literal here would land in
# the transcript of any session that reads this file and falsely satisfy the guard.
MARKER_0 = "Dispatch Shape — decide this " + "FIRST"
MARKER_1 = "Every dispatched agent " + "pins both"


def checked_run(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode not in (0, 2) or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]))
    return result


def run(payload):
    r = checked_run([sys.executable, HOOK], input=json.dumps(payload),
                    capture_output=True, text=True, timeout=60)
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ALLOW, ""
    doc = json.loads(out)
    hook = doc.get("hookSpecificOutput") or {}
    if hook.get("permissionDecision") == "deny":
        return DENY, hook.get("permissionDecisionReason", "")
    return ALLOW, out


def transcript(tmp, name, body):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    return p


def dispatch(tool, path):
    return {"tool_name": tool, "session_id": "dmg001", "transcript_path": path,
            "tool_input": {"description": "fan out three review lenses"}}


def delegated_brief(path, prompt):
    return {"tool_name": "Agent", "session_id": "dmg001", "transcript_path": path,
            "tool_input": {"prompt": prompt}}


def workflow_brief(path, args):
    return {"tool_name": "Workflow", "session_id": "dmg001", "transcript_path": path,
            "tool_input": {"args": args}}


NO_CANON = '{"role":"assistant","content":"dispatching three review agents now"}\n'


def main():
    tmp = tempfile.mkdtemp(prefix="dispatchguard_")
    failures = []

    # ensure_ascii=False: a real transcript stores the em dash as UTF-8, not as —.
    loaded_0 = transcript(tmp, "loaded_0.jsonl",
                          NO_CANON + json.dumps({"m": MARKER_0}, ensure_ascii=False) + "\n")
    loaded_1 = transcript(tmp, "loaded_1.jsonl",
                          NO_CANON + json.dumps({"m": MARKER_1}, ensure_ascii=False) + "\n")
    # The same §0 marker with the em dash JSON-escaped — the shape that makes a non-ASCII
    # marker unmatchable. Marker 1 is pure ASCII and is the guard's escape-proof half.
    escaped_0 = transcript(tmp, "escaped_0.jsonl",
                           NO_CANON + json.dumps({"m": MARKER_0}) + "\n")
    planted = transcript(tmp, "planted.jsonl", NO_CANON * 20)
    empty = transcript(tmp, "empty.jsonl", "")
    nested_prompt = transcript(tmp, "nested.md", "Dispatch the lenses via review_fanout.js.")
    oversized_prompt = transcript(
        tmp, "oversized.md", "bounded review context\n" * 30000 + "Use /delegate for the remaining work.\n"
    )

    cases = [
        # --- clean: the skill's canon is in the transcript ----------------------
        ("Workflow dispatch with the §0 marker present is allowed",
         dispatch("Workflow", loaded_0), ALLOW, ""),
        ("Agent dispatch with the §5 pins marker present is allowed",
         dispatch("Agent", loaded_1), ALLOW, ""),

        # --- planted violation: dispatch with no canon loaded -------------------
        ("Workflow dispatch with no marker is denied",
         dispatch("Workflow", planted), DENY, "Skill(orchestration)"),
        ("Agent dispatch with no marker is denied",
         dispatch("Agent", planted), DENY, "Re-issue the dispatch"),
        ("the denial forbids a direct-Agent downgrade when Workflow approval is absent",
         dispatch("Agent", planted), DENY, "ask the user"),

        ("a JSON-escaped em dash defeats the §0 marker — documents why marker 1 is ASCII",
         dispatch("Agent", escaped_0), DENY, "Skill(orchestration)"),

        # --- reverted: the same session after loading the skill -----------------
        ("the denied dispatch is allowed once the marker lands",
         dispatch("Agent", loaded_0), ALLOW, ""),

        # --- negatives: adjacent tools and unreadable inputs fail open ----------
        ("a non-dispatch tool is never touched",
         {"tool_name": "Bash", "transcript_path": planted,
          "tool_input": {"command": "ls"}}, ALLOW, ""),
        ("Edit is never touched",
         {"tool_name": "Edit", "transcript_path": planted, "tool_input": {}}, ALLOW, ""),
        ("no transcript_path fails open",
         {"tool_name": "Agent", "tool_input": {}}, ALLOW, ""),
        ("an empty transcript fails open",
         dispatch("Agent", empty), ALLOW, ""),
        ("a missing transcript file fails open",
         dispatch("Agent", os.path.join(tmp, "does_not_exist.jsonl")), ALLOW, ""),

        # --- nested delegation: /delegate is a fan-out owner ---------------------
        ("a delegate told to run /delegate is denied",
         delegated_brief(loaded_1, "Now run /delegate --jobs .claude/scratch/jobs.json."),
         DENY, "dispatch a fan-out"),
        ("a delegate told to use /delegate is denied",
         delegated_brief(loaded_1, "Use /delegate for the remaining review lenses."),
         DENY, "dispatch a fan-out"),
        ("an oversized brief is denied instead of approving an unchecked suffix",
         workflow_brief(loaded_1, {"agents": [{"promptPath": oversized_prompt}]}),
         DENY, "scan cap"),
        ("a non-object top-level payload fails open",
         [], ALLOW, ""),
        ("malformed jobs never crash the guard",
         workflow_brief(loaded_1, {"jobs": 1}), ALLOW, ""),
        ("malformed chains never crash the guard",
         workflow_brief(loaded_1, {"chains": 1}), ALLOW, ""),
        ("malformed agents never crash the guard",
         workflow_brief(loaded_1, {"agents": 1}), ALLOW, ""),
        ("malformed lenses never crash the guard",
         workflow_brief(loaded_1, {"lenses": 1}), ALLOW, ""),
        ("a review agent promptPath is inspected",
         workflow_brief(loaded_1, {"agents": [{"promptPath": nested_prompt}]}),
         DENY, "dispatch a fan-out"),
        ("an explore lens promptPath is inspected",
         workflow_brief(loaded_1, {"lenses": [{"promptPath": nested_prompt}]}),
         DENY, "dispatch a fan-out"),
        ("an explore lens inline prompt is inspected",
         workflow_brief(loaded_1, {"lenses": [{"prompt": "Dispatch via dispatch.js."}]}),
         DENY, "dispatch a fan-out"),
        ("a comma-led dispatch instruction is denied",
         delegated_brief(loaded_1, "Next, dispatch the lenses via review_fanout.js."),
         DENY, "dispatch a fan-out"),
        ("a need-to dispatch instruction is denied",
         delegated_brief(loaded_1, "You need to spawn the lenses via review_fanout.js."),
         DENY, "dispatch a fan-out"),
        ("a descriptive /delegate mention is allowed",
         delegated_brief(loaded_1, "The caller used /delegate before assigning this bounded task."),
         ALLOW, ""),
        ("an indented nested dispatch instruction is denied",
         delegated_brief(loaded_1, "  Dispatch the lenses via review_fanout.js."),
         DENY, "dispatch a fan-out"),
        ("a plain-bullet dispatch instruction is denied",
         delegated_brief(loaded_1, "- Dispatch the lenses via review_fanout.js."),
         DENY, "dispatch a fan-out"),
        ("an infinitive fan-out description is allowed",
         delegated_brief(loaded_1, "The ability to dispatch lenses via review_fanout.js is tested."),
         ALLOW, ""),
        ("a task-list quote stays data",
         delegated_brief(loaded_1, "- [ ] Run /delegate --jobs old.json\n  - archived note"),
         ALLOW, ""),
        ("an indented alternate task-list quote stays data",
         delegated_brief(loaded_1, "  * [x] Run /delegate --jobs old.json\n    old context"),
         ALLOW, ""),
        ("an indented blockquote stays data",
         delegated_brief(loaded_1, "  > Now run /delegate --jobs old.json"),
         ALLOW, ""),
    ]

    helper_cases = [
        ("the proof helper rejects an abnormal exit", "raise SystemExit(7)"),
        ("the proof helper rejects a traceback even with exit zero",
         "print('Traceback (most recent call last):')"),
    ]
    for label, source in helper_cases:
        try:
            checked_run([sys.executable, "-c", source], capture_output=True, text=True, timeout=60)
            ok = False
        except RuntimeError:
            ok = True
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    for label, payload, expected, needle in cases:
        try:
            got, reason = run(payload)
        except RuntimeError as exc:
            got, reason = "crash", str(exc)
        ok = got == expected and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                            % (label, expected, got, reason[:200]))

    print("  -- markers still exist in the skill they quote --")
    with open(SKILL, "r", encoding="utf-8") as fh:
        skill = fh.read()
    for label, marker in (("§0 heading marker", MARKER_0), ("§5 pins marker", MARKER_1)):
        ok = marker in skill
        print("%-4s %s present in skills/orchestration/SKILL.md" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s absent from SKILL.md — the guard would deny every dispatch" % label)

    total = len(cases) + len(helper_cases) + 2
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
