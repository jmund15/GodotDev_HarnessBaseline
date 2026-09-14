"""Re-runnable proof for hooks/budget_posture.py's [rating-debt] clause (S5).

`HARNESS_PENDING_COUNT` short-circuits the orchestration_metrics import so this proof can drive N
without a real session dir. The clause prints once per N, reprints only when N changes, and
never prints for -1 (unknown). Cases feed real UserPromptSubmit payloads and assert on stdout.

    python3 .claude/tests/test_budget_posture_rating_debt.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

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
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"session_id": session, "prompt": "next slice"}),
                       capture_output=True, text=True, timeout=60, env=env,
                       encoding="utf-8")
    return (r.stdout or "").strip()


def main():
    state = tempfile.mkdtemp(prefix="bprdstate_")
    tmp = tempfile.mkdtemp(prefix="bprdtmp_")
    base_env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp,
                    PYTHONIOENCODING="utf-8")
    base_env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    write_capture(tmp, SID)
    cases = []

    env = dict(base_env, HARNESS_PENDING_COUNT="5")
    out = prompt(env)
    cases.append(("clause prints once for N=5", CLAUSE in out and "5" in out))

    out = prompt(env)
    cases.append(("same N does not reprint the clause", CLAUSE not in out))

    env = dict(base_env, HARNESS_PENDING_COUNT="5")
    out = prompt(env)
    cases.append(("still no reprint on a fresh process with the same N",
                  CLAUSE not in out))

    env = dict(base_env, HARNESS_PENDING_COUNT="9")
    out = prompt(env)
    cases.append(("a changed N reprints the clause", CLAUSE in out and "9" in out))

    write_capture(tmp, "bprd0002")
    env = dict(base_env, HARNESS_PENDING_COUNT="-1")
    out = prompt(env, session="bprd0002")
    cases.append(("-1 (unknown) never prints the clause", CLAUSE not in out))

    write_capture(tmp, "bprd0003")
    env = dict(base_env, HARNESS_PENDING_COUNT="0")
    out = prompt(env, session="bprd0003")
    cases.append(("0 (no debt) never prints the clause", CLAUSE not in out))

    # 7. No seam: the real import path. A fake ~/.claude/projects/<p>/<sid>/workflows run with
    #    two labels and no verdicts must produce N=2 through orchestration_metrics itself.
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
    cases.append(("real import path counts two unrated labels from a fake session dir",
                  CLAUSE in out and "unrated dispatches: 2" in out))

    # 8. The payload's session_id is authoritative: a session that dispatched nothing shows no
    #    debt even though a peer session's workflows dir is the newest on disk.
    lone_sid = "bprd0005"
    write_capture(tmp, lone_sid)
    env = dict(base_env, HOME=home, USERPROFILE=home, CLAUDE_PROJECT_DIR=project)
    env.pop("HARNESS_PENDING_COUNT", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    out = prompt(env, session=lone_sid)
    cases.append(("a session with no workflows dir never inherits a peer's unrated count",
                  CLAUSE not in out))

    # 9. A posture re-emission (band change) must not wipe the debt dedupe.
    write_capture(tmp, "bprd0006", used_pct=40.0)
    env = dict(base_env, HARNESS_PENDING_COUNT="5")
    out = prompt(env, session="bprd0006")
    cases.append(("setup: clause prints once for a fresh session", CLAUSE in out))
    write_capture(tmp, "bprd0006", used_pct=99.0)   # band moves -> posture line re-emits
    out = prompt(env, session="bprd0006")
    cases.append(("a band change re-emits posture without reprinting an unchanged N",
                  CLAUSE not in out))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
