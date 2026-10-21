"""Re-runnable proof for hooks/dispatch_mechanism_guard.py.

instruction_quality §14: registration proves wiring, not matching. Each case feeds a real
PreToolUse payload and asserts on its channel. A dispatch is denied until the orchestration skill
and the model ladder are in this context (one deny names every unmet one, with its load route); nested
fan-out still denies. Transcript skill text is data, never a permission: only the state skill_load_marker
and model_ladder_gate record counts.

    python3 .claude/tests/test_dispatch_mechanism_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "dispatch_mechanism_guard.py")

ALLOW, DENY = "allow", "deny"
PR_READY_PRESENT = os.path.isfile(os.path.join(HERE, "..", "commands", "pr_ready.md"))
STATE = tempfile.mkdtemp(prefix="dispatchguard_state_")

# Former skill-load markers stay as transcript fixtures; neither authorizes a dispatch.
MARKER_0 = "Dispatch Shape — decide this " + "FIRST"
MARKER_1 = "Every dispatched agent " + "pins both"


def checked_run(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode not in (0, 2) or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]))
    return result


def arm_ladder(session_id):
    path = os.path.join(STATE, (session_id or "default")[:8] + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        state = {}
    state["model_ladder_ready"] = True
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh)


def arm_skill(session_id):
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    import _hook_state
    from unittest.mock import patch
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=STATE):
        _hook_state.mark_skill_in_context(session_id or "", "orchestration")


def run(payload, ladder=True, cwd=None, project_dir=None, skill=True):
    if isinstance(payload, dict) and payload.get("tool_name") in ("Workflow", "Agent"):
        if ladder:
            arm_ladder(payload.get("session_id") or "")
        if skill:
            arm_skill(payload.get("session_id") or "")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=STATE)
    if project_dir:
        env["CLAUDE_PROJECT_DIR"] = project_dir
    if cwd and isinstance(payload, dict):
        payload = dict(payload, cwd=cwd)
    r = checked_run([sys.executable, HOOK], input=json.dumps(payload),
                    capture_output=True, text=True, timeout=60, env=env, cwd=cwd)
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ALLOW, ""
    doc = json.loads(out)
    hook = doc.get("hookSpecificOutput") or {}
    if hook.get("permissionDecision") == "deny":
        return DENY, hook.get("permissionDecisionReason", "")
    # An advisory reaches the model as additionalContext: return its text, decoded, so a case can
    # assert on what the model reads rather than on the JSON escaping of it.
    return ALLOW, hook.get("additionalContext") or out


def transcript(tmp, name, body):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    return p


def dispatch(tool, path):
    return {"tool_name": tool, "session_id": "dmg001", "transcript_path": path,
            "tool_input": {"description": "fan out three review lenses"}}


def delegated_brief(path, prompt):
    return {"tool_name": "Workflow", "session_id": "dmg001", "transcript_path": path,
            "tool_input": {"args": {"agents": [{"key": "seat", "prompt": prompt}]}}}


def workflow_brief(path, args):
    return {"tool_name": "Workflow", "session_id": "dmg001", "transcript_path": path,
            "tool_input": {"args": args}}


NO_CANON = '{"role":"assistant","content":"dispatching three review agents now"}\n'


def test_unread_ladder_denies_every_dispatch_until_read():
    payload = dict(dispatch('Workflow', ''), session_id='ladrepeat')
    for _ in range(2):
        decision, reason = run(payload, ladder=False)
        assert decision == DENY and 'model_ladder_evidence.md' in reason and 'orchestration §5' in reason, reason
        assert 'Skill(skill="orchestration")' not in reason, 'a loaded skill was named as unmet: ' + reason
    assert run(payload)[0] == ALLOW


def test_s13_unreadable_transcript_allows_without_inspection():
    decision, note = run(dict(dispatch("Workflow", os.path.join(STATE, "missing.jsonl")), session_id="s13unrd1"))
    assert decision == ALLOW


def test_s13_oversized_brief_allows_and_names_unscanned_tail():
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    import dispatch_mechanism_guard as guard
    path = transcript(STATE, "s13-cap.md", "x" * (guard.MAX_BRIEF_BYTES + 100)
                      + "\nDispatch via review_fanout.js.")
    loaded = transcript(STATE, "s13-loaded.jsonl", MARKER_1)
    decision, note = run(dict(workflow_brief(loaded, {"jobs": [{"promptPath": path}]}), session_id="s13brief"))
    assert decision == ALLOW and "unscanned tail" in note and path in note


def test_s13_fanout_in_scanned_prefix_still_denies():
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    import dispatch_mechanism_guard as guard
    path = transcript(STATE, "s13-prefix.md", "Dispatch via review_fanout.js.\n"
                      + "x" * (guard.MAX_BRIEF_BYTES + 100))
    loaded = transcript(STATE, "s13-loaded.jsonl", MARKER_1)
    decision, note = run(dict(workflow_brief(loaded, {"jobs": [{"promptPath": path}]}), session_id="s13brief"))
    assert decision == DENY and "dispatch a fan-out" in note


def test_unloaded_orchestration_denies_and_compaction_reasks():
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    import _hook_state
    from unittest.mock import patch
    sid = "s14once1"
    # The skill's text in the transcript is not a load: only the marker's state counts.
    canon = transcript(STATE, "s14-canon.jsonl", NO_CANON + json.dumps({"m": MARKER_0}) + "\n")
    payload = dict(dispatch("Workflow", canon), session_id=sid)
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=STATE):
        _hook_state.clear_compaction_keys(sid)
    decision, reason = run(payload, skill=False)
    assert decision == DENY and 'Skill(skill="orchestration")' in reason and "SKILL.md" not in reason, reason
    assert "CLAUDE.core.md §9" in reason and "re-issue" in reason and "unchanged" not in reason, reason
    assert "model_ladder_evidence" not in reason, 'a read ladder was named as unmet: ' + reason
    assert run(payload)[0] == ALLOW
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=STATE):
        _hook_state.clear_compaction_keys(sid)
        skill_kept = _hook_state.skill_in_context(sid, "orchestration")
    decision, reason = run(payload, ladder=False, skill=False)
    assert decision == DENY and "model_ladder_evidence.md" in reason, \
        'a compaction must re-ask the ladder: ' + reason
    assert ('Skill(skill="orchestration")' in reason) is not skill_kept, \
        'a compaction re-asks the skill exactly when the runtime cannot re-attach it whole: ' + reason


def test_s14_sibling_precondition_is_retired():
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    import dispatch_mechanism_guard as guard
    assert not hasattr(guard, 'precondition_refusal')
    with open(os.path.join(HERE, '..', 'hooks', 'workflow_provider_guard.py'), encoding='utf-8') as fh:
        assert 'precondition_refusal' not in fh.read()


# Cut from a live inline script (run wf_8a49d030-507) whose 76 claims all came back UNCHECKED.
LIVE_OWN_EVIDENCE = (
    "const SCHEMA = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: {\n"
    "      severity: { type: 'string', enum: ['high', 'medium', 'low'] },\n"
    "      claim: { type: 'string' }, evidence: { type: 'string', description: 'file:line plus a short quote' } },"
    " required: ['severity', 'claim', 'evidence'] } } } }\n"
    "const r = await agent('review', { schema: SCHEMA, model: PINS.model, effort: PINS.effort })\n"
)


def test_inline_script_with_its_own_evidence_schema_denies():
    tmp = tempfile.mkdtemp(prefix="dmg_evidence_")
    loaded = transcript(tmp, "t.jsonl", NO_CANON)
    own_path = transcript(tmp, "own.js", LIVE_OWN_EVIDENCE)
    engine = os.path.realpath(os.path.join(HERE, "..", "workflows", "dispatch.js"))
    with open(engine, encoding="utf-8") as fh:
        engine_text = fh.read()

    def call(tool_input):
        return {"tool_name": "Workflow", "session_id": "dmgev001", "transcript_path": loaded,
                "tool_input": tool_input}

    cases = [
        ("an inline script typing its own evidence denies", call({"script": LIVE_OWN_EVIDENCE}), DENY),
        ("a scriptPath file typing its own evidence denies", call({"scriptPath": own_path}), DENY),
        ("a quoted-key evidence schema denies",
         call({"script": 'const S = {"evidence": {"type": "string"}}'}), DENY),
        ("a script embedding the evidence contract is allowed", call({"script": engine_text}), ALLOW),
        ("a committed engine by scriptPath is allowed", call({"scriptPath": engine}), ALLOW),
        ("prompt prose naming evidence is allowed",
         call({"script": "await agent(`Every claim needs evidence: file:line and a quote.`, {model: 'm', effort: 'low'})"}),
         ALLOW),
        ("a named workflow is not read", call({"name": "review-fanout"}), ALLOW),
    ]
    project = os.path.join(tmp, "project")
    scripts = os.path.join(project, ".claude", "scripts")
    os.makedirs(scripts)
    grader = transcript(scripts, "grader.js", LIVE_OWN_EVIDENCE)
    cases.append(("a committed grader whose free-text evidence is a rationale is allowed",
                  call({"scriptPath": grader}), ALLOW, project))
    bad = []
    for label, payload, expected, *project_dir in cases:
        got, reason = run(payload, project_dir=project_dir[0] if project_dir else None)
        if got != expected:
            bad.append("%s: expected %s got %s" % (label, expected, got))
        elif expected == DENY and not ("/delegate" in reason and "workflow_authoring.md" in reason
                                       and "Workflow(name=" not in reason and "{" not in reason):
            bad.append("%s: the deny names neither its rule nor its one next step: %r" % (label, reason[:200]))
    assert not bad, bad


def main():
    tmp = tempfile.mkdtemp(prefix="dispatchguard_")
    failures = []
    s13_proofs = (test_s13_unreadable_transcript_allows_without_inspection,
                  test_s13_oversized_brief_allows_and_names_unscanned_tail,
                  test_s13_fanout_in_scanned_prefix_still_denies,
                  test_unloaded_orchestration_denies_and_compaction_reasks,
                  test_unread_ladder_denies_every_dispatch_until_read,
                  test_s14_sibling_precondition_is_retired,
                  test_inline_script_with_its_own_evidence_schema_denies)
    for proof in s13_proofs:
        try:
            proof()
            print("ok   " + proof.__name__)
        except Exception as exc:
            failures.append(proof.__name__ + ": " + repr(exc))
            print("FAIL " + proof.__name__)

    # ensure_ascii=False: a real transcript stores the em dash as UTF-8, not as —.
    loaded_0 = transcript(tmp, "loaded_0.jsonl",
                          NO_CANON + json.dumps({"m": MARKER_0}, ensure_ascii=False) + "\n")
    loaded_1 = transcript(tmp, "loaded_1.jsonl",
                          NO_CANON + json.dumps({"m": MARKER_1}, ensure_ascii=False) + "\n")
    # Exercise both literal and JSON-escaped skill text; neither is a permission condition.
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

        # --- the doctrine loaded: the route choice is the agent's ---
        ("a first Workflow dispatch with the skill and ladder loaded is allowed",
         dict(dispatch("Workflow", planted), session_id="dmgwfn01"), ALLOW, ""),
        ("JSON-escaped skill text with the doctrine loaded is allowed",
         dispatch("Workflow", escaped_0), ALLOW, ""),

        # --- adjacent tools stay outside this guard -----------------------------
        ("a non-dispatch tool is never touched",
         {"tool_name": "Bash", "transcript_path": planted,
          "tool_input": {"command": "ls"}}, ALLOW, ""),
        ("Edit is never touched",
         {"tool_name": "Edit", "transcript_path": planted, "tool_input": {}}, ALLOW, ""),

        # --- unreadable transcript evidence is no longer inspected ---
        ("S14: no transcript_path allows",
         {"tool_name": "Workflow", "tool_input": {}}, ALLOW, ""),
        ("S14: an empty transcript allows",
         dispatch("Workflow", empty), ALLOW, ""),
        ("S14: a missing transcript file allows",
         dispatch("Workflow", os.path.join(tmp, "does_not_exist.jsonl")), ALLOW, ""),

        # --- nested delegation: /delegate is a fan-out owner ---------------------
        ("a delegate told to run /delegate is denied",
         delegated_brief(loaded_1, "Now run /delegate --jobs .claude/scratch/jobs.json."),
         DENY, "dispatch a fan-out"),
        ("the nested denial names the lens-brief materializer",
         delegated_brief(loaded_1, "Now run /delegate --jobs .claude/scratch/jobs.json."),
         DENY, "tools/lens_briefs.py"),
        ("a delegate told to use /delegate is denied",
         delegated_brief(loaded_1, "Use /delegate for the remaining review lenses."),
         DENY, "dispatch a fan-out"),
        ("S13: an oversized brief allows with an unscanned-tail note",
         workflow_brief(loaded_1, {"agents": [{"promptPath": oversized_prompt}]}),
         ALLOW, "unscanned tail"),
        ("S13: a non-object top-level payload allows with a fault note",
         [], ALLOW, "could not read its input"),
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
        ("an engine file checked by a shell tool is allowed",
         delegated_brief(loaded_1, "Checks you run: `node --check .claude/workflows/review_fanout.js`, "
                                   "`python3 .claude/tests/test_workflow_tool_profiles.py`."),
         ALLOW, ""),
        ("an engine file read by a shell tool is allowed",
         delegated_brief(loaded_1, "Then run `git diff -- .claude/workflows/dispatch.js` and report."),
         ALLOW, ""),
        ("a dispatch naming the engine by path is still denied",
         delegated_brief(loaded_1, "Then run .claude/workflows/review_fanout.js over these lenses."),
         DENY, "dispatch a fan-out"),
        ("an indented blockquote stays data",
         delegated_brief(loaded_1, "  > Now run /delegate --jobs old.json"),
         ALLOW, ""),
        ("a scratch directory named after a fan-out command is a path, not a command",
         delegated_brief(loaded_1, "Do not push. The orchestrator runs the full gate afterwards; you run scoped "
                                   "verification. Same rules as the first pass (`.claude/scratch/pr_ready/fix_brief.md`)."),
         ALLOW, ""),
        ("a slash command after a path-like word is still a command"
         + ("" if PR_READY_PRESENT else " (coding member absent: its name is no command)"),
         delegated_brief(loaded_1, "Then run /pr_ready over the frozen diff."),
         DENY if PR_READY_PRESENT else ALLOW, "dispatch a fan-out" if PR_READY_PRESENT else ""),

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

    ladder_note = run(dict(dispatch("Workflow", loaded_0), session_id="dmgnold1"), ladder=False)
    ladder_cases = [
        ("a Workflow without a fresh ladder Read is denied naming the ladder",
         ladder_note, DENY, "model_ladder_evidence.md"),
        ("the ladder deny says the doctrine is not in context since the last compaction",
         ladder_note, DENY, "since the session started or last compacted"),
        ("the ladder deny cites orchestration §5",
         ladder_note, DENY, "orchestration §5"),
    ]
    # A session working inside a worktree: the gate accepts only the primary checkout's ladder,
    # so the denial must name that file absolutely — a relative path resolves to the worktree copy.
    primary = tempfile.mkdtemp(prefix="dispatchguard_primary_")
    worktree = os.path.join(primary, ".claude", "worktrees", "wt1")
    for base in (primary, worktree):
        ref = os.path.join(base, ".claude", "reference")
        os.makedirs(ref, exist_ok=True)
        with open(os.path.join(ref, "model_ladder_evidence.md"), "w", encoding="utf-8") as fh:
            fh.write("# Model Ladder\n")
    primary_ladder = os.path.realpath(
        os.path.join(primary, ".claude", "reference", "model_ladder_evidence.md"))
    worktree_ladder = os.path.realpath(
        os.path.join(worktree, ".claude", "reference", "model_ladder_evidence.md"))
    wt_session = dict(dispatch("Workflow", loaded_0), session_id="dmgwt001")
    wt_note = run(wt_session, ladder=False, cwd=worktree, project_dir=primary)
    ladder_cases.append(("a worktree-cwd deny names the primary checkout's ladder absolutely",
                         wt_note, DENY, primary_ladder))
    ladder_cases.append(("a worktree-cwd deny never names the worktree's stale copy",
                         (wt_note[0], worktree_ladder not in wt_note[1]), (DENY, True), ""))
    both = run(dict(dispatch("Workflow", planted), session_id="dmgboth1"), ladder=False, skill=False)
    ladder_cases.append(("missing skill and ladder: one deny names the skill retry",
                         both, DENY, 'Skill(skill="orchestration")'))
    ladder_cases.append(("missing skill and ladder: the same deny names the ladder Read",
                         both, DENY, "model_ladder_evidence"))
    nested_both = run(dict(delegated_brief(planted, "Now run /delegate --jobs j.json."), session_id="dmgnest1"),
                      ladder=False, skill=False)
    ladder_cases.append(("a nested fan-out with the doctrine unloaded: one deny names the fan-out",
                         nested_both, DENY, "dispatch a fan-out"))
    ladder_cases.append(("a nested fan-out with the doctrine unloaded: the same deny names the skill",
                         nested_both, DENY, 'Skill(skill="orchestration")'))
    skill_only = run(dict(dispatch("Workflow", planted), session_id="dmgonly1"))
    ladder_cases.append(("a read ladder and loaded skill add no doctrine line",
                         (skill_only[0], "model_ladder_evidence" not in skill_only[1]), (ALLOW, True), ""))
    first = run(dispatch("Workflow", loaded_0))
    second = run(dispatch("Workflow", loaded_0), ladder=False)
    ladder_cases.append(("one full ladder Read authorizes later Workflows without a re-read",
                         (first[0], second[0]), (ALLOW, ALLOW), ""))
    for label, got, expected, needle in ladder_cases:
        reason = got[1] if isinstance(got, tuple) and len(got) == 2 and isinstance(got[1], str) else ""
        actual = got if isinstance(expected, tuple) else got[0]
        ok = actual == expected and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s expected=%r got=%r reason=%r" % (label, expected, actual, reason[:200]))

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

    plumbing_reason = run([])[1]
    plumbing_checks = [
        ("S13: plumbing advisory reports the skipped check",
         "allowed without that check" in plumbing_reason),
        ("S13: plumbing advisory names the repair route",
         "Repair hooks/dispatch_mechanism_guard.py" in plumbing_reason),
        ("E6: plumbing denial does not ask the agent to load a skill",
         "Skill" not in plumbing_reason),
    ]
    for label, ok in plumbing_checks:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    total = len(cases) + len(helper_cases) + len(ladder_cases) + len(plumbing_checks) + len(s13_proofs)
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
