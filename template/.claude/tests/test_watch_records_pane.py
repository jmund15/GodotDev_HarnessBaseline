"""Proof of `watch_records.py --pane`: the owner's live progress view, a background shell no wake reads.

The pane appends a frame when its check-in lines change, plus a heartbeat, so the desktop client's offset reader sees
each one. The wake watcher's pane hint prints only while work is live and no
pane runs. A second pane beside a live one exits COVERED.
"""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
WATCH = ROOT / "tools" / "watch_records.py"
SID = "paneproof-0000-0000-0000-000000000000"
PASS = 0
sys.path.insert(0, str(ROOT / "tools"))
import watch_records as w  # noqa: E402


def check(condition, label, detail=""):
    global PASS
    if not condition:
        raise AssertionError("%s: %s" % (label, detail))
    PASS += 1
    print("PASS", label)


base = Path(tempfile.mkdtemp(prefix="pane_proof_", dir=os.environ.get("TMP")))

# Frames append: desktop reads only bytes past their saved offset, so a rewritten file never reaches them.
# The stamp line closes each frame, since a viewer shows the output's tail.
idle = w.pane_frame([], now=0)
check("idle" in idle and idle.rstrip("\n").splitlines()[-1].startswith(w.PANE_TITLE), "an idle frame ends on its stamp line", idle)
busy = w.pane_frame(["RUNNING lens-a for 3 min, last write 0 min ago", "  progress: tool calls 4"], now=0)
check("lens-a" in busy and "idle" not in busy and busy.rstrip("\n").splitlines()[-1].endswith("1 running"),
      "a live frame lists its check-in lines and counts its jobs on the stamp line", busy)

# A frame prints when its lines change, or as a heartbeat once PANE_HEARTBEAT_SECONDS pass unchanged.
check(w.pane_due(("a",), None, 0, now=1), "the first poll prints")
check(not w.pane_due(("a",), ("a",), 100, now=101), "unchanged lines inside the heartbeat print nothing")
check(w.pane_due(("b",), ("a",), 100, now=101), "changed lines print")
check(w.pane_due(("a",), ("a",), 100, now=100 + w.PANE_HEARTBEAT_SECONDS), "an unchanged pane prints its heartbeat")

# The hint: only while work is live and no pane is registered.
state_dir = base / "state"
state_dir.mkdir()
os.environ["HARNESS_HOOK_STATE_DIR"] = str(state_dir)
state_file = Path(w._hook_state.state_path(SID))
check(w.pane_hint(SID, live=2) is not None and w.PANE_COMMAND in w.pane_hint(SID, live=2),
      "live work with no pane prints the pane hint")
check(w.pane_hint(SID, live=0) is None, "no live work prints no pane hint")
w._hook_state.update_json_locked(str(state_file), lambda s: s.__setitem__(w.PANE_KEY, w.process_line()))
check(w.pane_hint(SID, live=2) is None, "a live registered pane silences the hint")
w._hook_state.update_json_locked(str(state_file), lambda s: s.__setitem__(w.PANE_KEY, "2020-01-01T00:00:00Z pid=1 winpid=1"))
check(w.pane_hint(SID, live=2) is not None, "a dead registered pane does not silence the hint")
w._hook_state.update_json_locked(str(state_file), lambda s: s.pop(w.PANE_KEY, None))

# End to end: the real pane prints one idle frame across many unchanged polls, and a second pane exits COVERED.
env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, HARNESS_HOOK_STATE_DIR=str(state_dir), WATCH_RECORDS_PANE_SEC="0.2")
out_path = base / "pane.out"
with open(out_path, "w", encoding="utf-8") as out:
    pane = subprocess.Popen([sys.executable, str(WATCH), "--pane"], stdout=out, stderr=subprocess.PIPE, env=env)
try:
    deadline = time.time() + 20
    text = ""
    while time.time() < deadline:
        time.sleep(0.5)
        text = out_path.read_text(encoding="utf-8", errors="replace")
        if text.count(w.PANE_TITLE) == 1 and time.time() > deadline - 18:
            break
    check(pane.poll() is None, "the pane keeps running", pane.stderr.read().decode() if pane.poll() is not None else "")
    check(text.count(w.PANE_TITLE) == 1 and "idle" in text, "unchanged polls append no second frame", repr(text))
    second = subprocess.run([sys.executable, str(WATCH), "--pane"], capture_output=True, text=True, timeout=30, env=env)
    check(second.returncode == 0 and "COVERED" in second.stdout, "a second pane beside a live one exits COVERED",
          second.stdout + second.stderr)
finally:
    pane.kill()
    pane.wait(timeout=10)

# The wake watcher hands its live count to `closing`, which is where the hint joins a wake's last lines.
import contextlib  # noqa: E402
w.CHECKIN_SECONDS = 0
seen = []
shell_unit = {"running-shell:x": {"running_shell": "x", "records": [], "fanout_dir": None}}
with contextlib.redirect_stdout(io.StringIO()):
    w.wake_loop(lambda: shell_unit, closing=lambda live: seen.append(live) or [])
    w.wake_loop(lambda: {}, closing=lambda live: seen.append(live) or [])
check(seen[:1] == [1], "a check-in wake hands closing its live count", repr(seen))

# The session's closing lines are the production closing: the pane hint joins the unrated line only while work is live.
closing_at = w.session_closing(SID)
check(any("PANE none" in t for t in closing_at(2)), "the session closing prints the pane hint while work is live",
      repr(closing_at(2)))
check(not any("PANE" in t for t in closing_at(0)), "the session closing prints no pane hint while no work is live",
      repr(closing_at(0)))
w._hook_state.update_json_locked(str(state_file), lambda s: s.__setitem__(w.PANE_KEY, w.process_line()))
check(not any("PANE" in t for t in closing_at(2)), "the session closing omits the pane hint beside a live pane",
      repr(closing_at(2)))
w._hook_state.update_json_locked(str(state_file), lambda s: s.pop(w.PANE_KEY, None))

print("test_watch_records_pane: %d passed, 0 failed" % PASS)
