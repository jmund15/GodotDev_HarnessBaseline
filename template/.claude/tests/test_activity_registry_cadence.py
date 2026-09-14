"""Re-runnable proof for hooks/activity_registry.py's emission cadence (B5).

The peer-activity line is emitted when it is NEW to the model: first prompt of the session,
first prompt after a compaction, or a change in the live peer set. The 10-turn heartbeat is
gone; the fingerprint branch stays. Cases feed real UserPromptSubmit payloads and assert on
stdout.

Both temp roots are redirected: HARNESS_HOOK_STATE_DIR for the shared session state, TMP/TEMP for
the machine-wide registry directory and the hook's own dedupe file.

    python3 .claude/tests/test_activity_registry_cadence.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "activity_registry.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")

SID = "arc00001"
PEER_CHECKOUT = "C:/other/checkout"


def write_record(tmpdir, pid, label, kind="gate"):
    """A registry record of the shape regression_gate.ps1 writes."""
    d = os.path.join(tmpdir, "harness-activity")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s-%d.json" % (kind, pid))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pid": pid, "kind": kind, "checkout": PEER_CHECKOUT,
                   "sessionId": None, "label": label,
                   "startedAt": time.time(), "heartbeatAt": time.time(),
                   "expectedSec": 300}, fh)
    return path


def prompt(env, session=SID):
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"session_id": session, "cwd": "C:/repo",
                                         "prompt": "next slice"}),
                       capture_output=True, text=True, timeout=60, env=env,
                       encoding="utf-8")
    return (r.stdout or "").strip()


def precompact(env, session=SID):
    subprocess.run([sys.executable, PRECOMPACT],
                   input=json.dumps({"session_id": session, "trigger": "auto"}),
                   capture_output=True, text=True, timeout=60, env=env)


def main():
    state = tempfile.mkdtemp(prefix="arcstate_")
    tmp = tempfile.mkdtemp(prefix="arctmp_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp,
               PYTHONIOENCODING="utf-8")
    # A real live PID: liveness is PID-verified, so a fabricated one is swept as garbage.
    peer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    cases = []
    try:
        record = write_record(tmp, peer.pid, "gate: final")
        out = prompt(env)
        cases.append(("the first prompt reports the live peer",
                      "[activity]" in out and "peer gate live" in out))

        cases.append(("an unchanged peer set is silent", prompt(env) == ""))
        cases.append(("and stays silent — no turn-count heartbeat", prompt(env) == ""))

        write_record(tmp, os.getpid(), "suite: NPCs", kind="suite")
        out = prompt(env)
        cases.append(("a changed peer set re-emits", "peer suite live" in out))

        precompact(env)
        out = prompt(env)
        cases.append(("a compaction re-emits the unchanged set", "[activity]" in out))
        cases.append(("still deduped after that", prompt(env) == ""))

        os.remove(record)
        out = prompt(env)
        cases.append(("a peer disappearing is a change, so it re-emits",
                      "[activity]" in out and "peer gate live" not in out))
    finally:
        peer.terminate()

    # Negative: an empty registry says nothing at all.
    empty = tempfile.mkdtemp(prefix="arcnone_")
    cases.append(("no records on disk emits nothing",
                  prompt(dict(env, TMP=empty, TEMP=empty), session="arc00002") == ""))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
