"""Re-runnable proof that hooks/budget_posture.py no longer prints a per-prompt [rating-debt] clause.

Rating debt surfaces at phase boundaries instead: the compaction hook (compact_directive_anchor.py),
Part close and /session_end (`orchestration_metrics.py --pending`). A per-prompt reminder pulled the
drive toward bookkeeping on every turn. Cases feed real UserPromptSubmit payloads with a planted
debt, through the seam and the real import path, and assert the clause is absent while the posture
line still prints.

    python3 .claude/tests/test_budget_posture_rating_debt.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

# The hook's plan-usage sampler writes here, never to the real ~/.claude/usage_samples.jsonl.
os.environ["HARNESS_USAGE_SAMPLES"] = os.path.join(tempfile.mkdtemp(prefix="bprd_samples_"), "samples.jsonl")
# A cold capacity cache must not start a live probe from a proof (budget_posture.start_warm).
os.environ["HARNESS_CAPACITY_WARM"] = "0"

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "budget_posture.py")

SID = "bprd0001"
SEVEN_DAY = 7 * 24 * 3600
CLAUSE = "[rating-debt] unrated dispatches:"


def write_capture(tmpdir, session, used_pct=40.0):
    now = time.time()
    path = os.path.join(tmpdir, "cc-cachestat-%s.json" % session)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {
            "captured_at": now,
            "seven_day": {"used_percentage": used_pct, "resets_at": now + SEVEN_DAY / 2},
        }}, fh)


def prompt(env, session=SID):
    transcript = os.path.join(env['HARNESS_HOOK_STATE_DIR'], 'dispatch.jsonl')
    with open(transcript, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps({'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Agent', 'input': {'prompt': 'review'}}]}}) + '\n')
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"session_id": session, "prompt": "next slice", 'transcript_path': transcript}),
                       capture_output=True, text=True, timeout=60, env=env,
                       encoding="utf-8")
    return (r.stdout or "").strip()


def main():
    state = tempfile.mkdtemp(prefix="bprdstate_")
    tmp = tempfile.mkdtemp(prefix="bprdtmp_")
    base_env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
                    PYTHONIOENCODING="utf-8")
    base_env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    write_capture(tmp, SID)
    cases = []

    env = dict(base_env, HARNESS_PENDING_COUNT="5")
    out = prompt(env)
    cases.append(("planted debt N=5 prints no clause", CLAUSE not in out and "[rating-debt]" not in out))
    cases.append(("the posture line still prints", "[budget-posture]" in out))

    env = dict(base_env, HARNESS_PENDING_COUNT="9")
    out = prompt(env)
    cases.append(("a changed N prints no clause", "[rating-debt]" not in out))

    home = tempfile.mkdtemp(prefix="bprdhome_")
    project = tempfile.mkdtemp(prefix="bprdproj_")
    sid = "bprd0004"
    wdir = os.path.join(home, ".claude", "projects", "anyproj", sid, "workflows")
    os.makedirs(wdir)
    with open(os.path.join(wdir, "wf_1.json"), "w", encoding="utf-8") as fh:
        json.dump({"runId": "wf_1", "workflowProgress": [
            {"type": "workflow_agent", "label": "exec:one", "agentId": "a1"},
            {"type": "workflow_agent", "label": "exec:two", "agentId": "a2"},
        ]}, fh)
    write_capture(tmp, sid)
    env = dict(base_env, HOME=home, USERPROFILE=home, CLAUDE_CODE_SESSION_ID=sid,
               CLAUDE_PROJECT_DIR=project)
    env.pop("HARNESS_PENDING_COUNT", None)
    out = prompt(env, session=sid)
    cases.append(("real import path with two unrated labels prints no clause",
                  "[rating-debt]" not in out))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
