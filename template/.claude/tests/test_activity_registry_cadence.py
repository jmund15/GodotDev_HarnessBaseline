"""Re-runnable proof for hooks/activity_registry.py's emission cadence (B5).

The peer-activity line is emitted when it is NEW to the model: first prompt of the session,
first prompt after a compaction, or a change in the live peer set. The 10-turn heartbeat is
gone; the fingerprint branch stays. Cases feed real UserPromptSubmit payloads and assert on
stdout.

Both temp roots are redirected: HARNESS_HOOK_STATE_DIR for the shared session state, TMP/TEMP/TMPDIR for
the machine-wide registry directory and the hook's own dedupe file.

    python3 .claude/tests/test_activity_registry_cadence.py
"""
import json
import os
import shutil
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
                   capture_output=True, text=True, timeout=60,
                   env=dict(env, CLAUDE_PROJECT_DIR=env["HARNESS_HOOK_STATE_DIR"]))


# The hook runs under a real `node` parent, which its session_root walk takes as this session's Claude Code root. The
# node runner plants the records first, so they can name its own pid as the owner root a gate passes its children.
NODE_RUNNER = r"""
const fs = require('fs'); const cp = require('child_process');
const [hook, recDir, livePid, stdin] = process.argv.slice(2);
const now = Date.now() / 1000;
const rec = (label, checkout, root) => ({pid: Number(livePid), kind: 'suite', checkout, sessionId: null, label,
  startedAt: now, heartbeatAt: now, expectedSec: 300, ownerRootPid: root, ownerRootStart: null});
fs.mkdirSync(recDir, {recursive: true});
fs.writeFileSync(recDir + '/suite-own.json', JSON.stringify(rec('gate child of a detached gate', 'C:/repo', process.pid)));
fs.writeFileSync(recDir + '/suite-foreign.json', JSON.stringify(rec('another session run', 'C:/repo', process.pid + 100000)));
fs.writeFileSync(recDir + '/suite-worktree.json', JSON.stringify(rec('own-root worktree run', 'C:/repo/.claude/worktrees/x', process.pid)));
const r = cp.spawnSync(process.env.PY, [hook], {input: stdin, env: process.env, encoding: 'utf8'});
process.stdout.write(r.stdout || '');
process.stderr.write(r.stderr || '');
process.exit(r.status === null ? 99 : r.status);
"""


def ownership_cases():
    """A gate's child records the gate's owner root (the HARNESS_OWNER_ROOT_PID pair), so its record reads as the
    session's own run whatever its own chain; a record from another root, or from a nested worktree, is a peer."""
    node = shutil.which("node")
    if not node:
        return [("CANNOT-RUN node is not on PATH, so no process can stand in as the session root", False)]
    state = tempfile.mkdtemp(prefix="arcown_")
    tmp = tempfile.mkdtemp(prefix="arcowntmp_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp, PYTHONIOENCODING="utf-8", PY=sys.executable)
    runner = os.path.join(tmp, "runner.js")
    with open(runner, "w", encoding="utf-8") as fh:
        fh.write(NODE_RUNNER)
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        r = subprocess.run([node, runner, HOOK, os.path.join(tmp, "harness-activity"), str(live.pid),
                            json.dumps({"session_id": "arcown01", "cwd": "C:/repo", "prompt": "next"})],
                           capture_output=True, text=True, timeout=60, env=env, encoding="utf-8")
        out = (r.stdout or "").strip()
    finally:
        live.terminate()
    return [
        ("setup: the hook ran under the node session root and reported (%s)" % (out[:160] or r.stderr[:160]),
         r.returncode == 0 and "[activity]" in out),
        ("a record carrying this session's owner root (a detached gate's child) is not a peer",
         "gate child of a detached gate" not in out),
        ("a same-checkout record from another owner root is a peer", "another session run" in out),
        ("a record from a .claude/worktrees/<x> checkout is a peer even with this session's root",
         "own-root worktree run" in out),
    ]


def main():
    state = tempfile.mkdtemp(prefix="arcstate_")
    tmp = tempfile.mkdtemp(prefix="arctmp_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
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
                  prompt(dict(env, TMP=empty, TEMP=empty, TMPDIR=empty), session="arc00002") == ""))

    cases += ownership_cases()

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
