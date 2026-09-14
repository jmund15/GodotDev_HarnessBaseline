"""Re-runnable proof for hooks/budget_posture.py's emission cadence (B5).

The posture line is emitted when it is NEW to the model: first prompt of the session, first
prompt after a compaction, a band change, or a ±0.15 pressure move. The 10-turn heartbeat is
gone — it re-sent text the model still had. Cases feed real UserPromptSubmit payloads and
assert on stdout.

Both temp roots are redirected: HARNESS_HOOK_STATE_DIR for the shared session state, TMP/TEMP for
the cc-cachestat capture and the hook's own dedupe file.

    python3 .claude/tests/test_budget_posture_cadence.py
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
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")

SID = "bpc00001"
SEVEN_DAY = 7 * 24 * 3600


def write_capture(tmpdir, session, used_pct):
    """The rate_limits snapshot statusline.py writes every turn."""
    now = time.time()
    path = os.path.join(tmpdir, "cc-cachestat-%s.json" % session)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {
            "captured_at": now,
            # Half the window elapsed, so pressure == used_percentage / 50.
            "seven_day": {"used_percentage": used_pct, "resets_at": now + SEVEN_DAY / 2},
        }}, fh)


def checked_run(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode not in (0, 2) or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]))
    return result


def prompt(env, session=SID):
    r = checked_run([sys.executable, HOOK],
                    input=json.dumps({"session_id": session, "prompt": "next slice"}),
                    capture_output=True, text=True, timeout=60, env=env,
                    encoding="utf-8")   # the hook prints §; locale decode mangles it
    return (r.stdout or "").strip()


def precompact(env, session=SID):
    checked_run([sys.executable, PRECOMPACT],
                input=json.dumps({"session_id": session, "trigger": "auto"}),
                capture_output=True, text=True, timeout=60, env=env)


def main():
    state = tempfile.mkdtemp(prefix="bpcstate_")
    tmp = tempfile.mkdtemp(prefix="bpctmp_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp,
               PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    cases = []
    for label, source in (
        ("the proof helper rejects an abnormal exit", "raise SystemExit(7)"),
        ("the proof helper rejects a traceback even with exit zero",
         "print('Traceback (most recent call last):')"),
    ):
        try:
            checked_run([sys.executable, "-c", source], capture_output=True, text=True, timeout=60)
            rejected = False
        except RuntimeError:
            rejected = True
        cases.append((label, rejected))

    write_capture(tmp, SID, 40.0)                 # pressure 0.80 -> Surplus
    out = prompt(env)
    cases.append(("the first prompt emits the posture",
                  "[budget-posture]" in out and "Surplus" in out))
    cases.append(("the emitted NEVER clause cites its home", "orchestration §5" in out))

    cases.append(("an unchanged posture is silent on the next prompt", prompt(env) == ""))
    cases.append(("and stays silent — no turn-count heartbeat", prompt(env) == ""))

    write_capture(tmp, SID, 60.0)                 # pressure 1.20 -> Ahead
    out = prompt(env)
    cases.append(("a band change re-emits", "Ahead" in out))

    precompact(env)
    out = prompt(env)
    cases.append(("a compaction re-emits the unchanged posture",
                  "[budget-posture]" in out))

    cases.append(("still deduped after that", prompt(env) == ""))

    # Negative: no telemetry to read at all.
    empty = tempfile.mkdtemp(prefix="bpcnone_")
    bare = dict(env, TMP=empty, TEMP=empty)
    cases.append(("no capture on disk emits nothing",
                  prompt(bare, session="bpc00002") == ""))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
