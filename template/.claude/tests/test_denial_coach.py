"""Proof for hooks/denial_coach.py: an auto-mode denial teaches the next move instead of ending the turn.

`--record` (PermissionDenied) files the denial in session state; `--coach` (Stop) blocks the turn's
end once with that tag's guidance, then stays quiet. Real hook payloads on stdin, planted state dir.

    python3 .claude/tests/test_denial_coach.py
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "denial_coach.py")
FAILURES = []


def run(mode, payload, state_dir):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir)
    r = subprocess.run([sys.executable, HOOK, mode], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30, env=env)
    return r.returncode, r.stdout, r.stderr


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


def denial(sid, tag):
    return {"session_id": sid, "hook_event_name": "PermissionDenied", "permission_mode": "auto",
            "tool_name": "Edit", "tool_input": {"file_path": "notes.md"}, "tool_use_id": "t1", "reason": tag}


def stop(sid):
    return {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": False}


state = tempfile.mkdtemp(prefix="coach_proof_")

rc, out, err = run("--record", denial("proof-1", "[Self-Modification]"), state)
check("--record files a denial silently", rc == 0 and out.strip() == "", out + err)

rc, out, err = run("--coach", stop("proof-1"), state)
decision = json.loads(out) if out.strip() else {}
check("--coach blocks the stop once with the tag's move",
      rc == 0 and decision.get("decision") == "block" and "[Self-Modification]" in decision.get("reason", ""), out + err)

rc, out, err = run("--coach", stop("proof-1"), state)
check("--coach stays quiet once the denial is coached", rc == 0 and out.strip() == "", out + err)

rc, out, err = run("--coach", stop("proof-2"), state)
check("a session with no denials passes silently", rc == 0 and out.strip() == "", out + err)

sys.path.insert(0, os.path.dirname(HOOK))
os.environ["HARNESS_HOOK_STATE_DIR"] = state
import denial_coach  # noqa: E402
check("the coach returns nothing for a session with no denials (no swallowed crash)",
      denial_coach._coach(stop("proof-3")) is None)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
