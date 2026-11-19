#!/usr/bin/env python3
"""A recursive delete of a folder this session made with a plain `mkdir` is allowed; every near miss stays denied.

The allow rests on facts the guard reads, never on a reason the agent writes: a main-thread Bash call in this
session's transcript ran `mkdir <target>` with no flag, the call finished without error, and the folder's creation
time lies inside that call. A flagless `mkdir` fails on an existing path, so a folder born inside the window is the
call's own. The folder holds no git repository.

The transcript rows are cut from a live capture (the Bash `tool_use` row and its `tool_result` row), trimmed, with
the path and timestamps replaced. Every case runs the hook as the runtime does: a PreToolUse payload on stdin,
judged by exit code and output channel. Planted folders sit outside the child's temp directory, so the temp-dir
allow cannot pass a case.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOK = os.path.join(ROOT, "hooks", "dangerous_shell_guard.py")

BASE = tempfile.mkdtemp(prefix="dsg-session-dir-")
CHILD_TMP = os.path.join(BASE, "child-tmp")
PLANT = os.path.join(BASE, "downloads")
os.makedirs(CHILD_TMP)
os.makedirs(PLANT)


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def msys(path):
    p = path.replace("\\", "/")
    return "/%s%s" % (p[0].lower(), p[2:]) if len(p) > 1 and p[1] == ":" else p


def transcript(name, command, start, end, is_error=False, sidechain=False):
    """Two rows in the live shape: the assistant's Bash tool_use, then the user row carrying its tool_result."""
    use = {"type": "assistant", "timestamp": iso(start), "cwd": ROOT, "isSidechain": sidechain,
           "message": {"role": "assistant", "content": [
               {"type": "tool_use", "id": "toolu_plant", "name": "Bash",
                "input": {"command": command, "description": "Download and extract"}}]}}
    result = {"type": "user", "timestamp": iso(end), "cwd": ROOT, "isSidechain": sidechain,
              "message": {"role": "user", "content": [
                  {"tool_use_id": "toolu_plant", "type": "tool_result", "content": "./out.exe",
                   "is_error": is_error}]},
              "toolUseResult": {"stdout": "./out.exe", "stderr": "", "interrupted": False}}
    path = os.path.join(BASE, name + ".jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(use) + "\n" + json.dumps(result) + "\n")
    return path


def made(name, command_for):
    """A folder created inside its call's window, and the transcript of that call."""
    folder = os.path.join(PLANT, name)
    start = time.time() - 1
    os.makedirs(folder)
    with open(os.path.join(folder, "payload.bin"), "wb") as handle:
        handle.write(b"x")
    return folder, transcript(name, command_for(folder), start, time.time() + 1)


def verdict(command, transcript_path):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "hook_event_name": "PreToolUse",
               "cwd": ROOT, "transcript_path": transcript_path}
    env = {**os.environ, "TMP": CHILD_TMP, "TEMP": CHILD_TMP, "TMPDIR": CHILD_TMP,
           "CLAUDE_PROJECT_DIR": os.path.dirname(ROOT), "CLAUDE_CODE_SESSION_ID": ""}
    proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                          timeout=60, env=env)
    if proc.returncode not in (0, 2) or "Traceback" in proc.stderr:
        return "CRASH: " + proc.stderr[-300:]
    return "deny" if proc.returncode == 2 else "allow"


def live_shape(folder):
    return ('D="%s"; mkdir "$D" && cd "$D" && curl -sL -o artifact.zip "https://example.invalid/a" '
            '&& unzip -q artifact.zip && rm artifact.zip' % msys(folder))


cases = []

folder, tx = made("live", live_shape)
cases.append(("allow", "live-shape mkdir, MSYS path", 'rm -r "%s"' % msys(folder), tx))
cases.append(("allow", "same folder, Windows path", 'rm -rf "%s"' % folder, tx))

folder, tx = made("chained", live_shape)
cases.append(("deny", "unrelated second delete rides along", 'rm -r "%s"; rm -r "%s"' % (msys(folder), msys(PLANT)), tx))

folder, tx = made("flag-p", lambda f: 'mkdir -p "%s"' % msys(f))
cases.append(("deny", "mkdir -p proves no creation", 'rm -r "%s"' % msys(folder), tx))

folder = os.path.join(PLANT, "older")
os.makedirs(folder)
later = time.time() + 30
tx = transcript("older", 'mkdir "%s"' % msys(folder), later, later + 5)
cases.append(("deny", "folder born before the call", 'rm -r "%s"' % msys(folder), tx))

folder, tx = made("errored", lambda f: 'mkdir "%s"' % msys(f))
tx = transcript("errored", 'mkdir "%s"' % msys(folder), os.stat(folder).st_ctime - 1, time.time() + 1, is_error=True)
cases.append(("deny", "call ended in error", 'rm -r "%s"' % msys(folder), tx))

folder, tx = made("sidechain", lambda f: 'mkdir "%s"' % msys(f))
tx = transcript("sidechain", 'mkdir "%s"' % msys(folder), os.stat(folder).st_ctime - 1, time.time() + 1, sidechain=True)
cases.append(("deny", "subagent's call is not this session's", 'rm -r "%s"' % msys(folder), tx))

folder, tx = made("heredoc", lambda f: 'cat > notes.txt <<EOF\nmkdir "%s"\nEOF' % msys(f))
cases.append(("deny", "mkdir only inside a heredoc body", 'rm -r "%s"' % msys(folder), tx))

folder, tx = made("withgit", lambda f: 'mkdir "%s"' % msys(f))
os.makedirs(os.path.join(folder, "repo", ".git"))
cases.append(("deny", "holds a git repository", 'rm -r "%s"' % msys(folder), tx))

folder, tx = made("parent", lambda f: 'mkdir "%s"' % msys(f))
inner = os.path.join(folder, "inner")
os.makedirs(inner)
cases.append(("deny", "a folder inside the made one", 'rm -r "%s"' % msys(inner), tx))

folder, tx = made("other", lambda f: 'mkdir "%s"' % msys(f))
cases.append(("deny", "a folder the call never made", 'rm -r "%s"' % msys(PLANT), tx))
cases.append(("deny", "no transcript", 'rm -r "%s"' % msys(folder), None))

fails = 0
print("dangerous_shell_guard: session-made folder delete")
if not hasattr(os.stat(PLANT), "st_birthtime"):
    print("  skip  this platform reports no creation time; the allow fails closed here")
    cases = [c for c in cases if c[0] == "deny"]
for want, label, command, tx in cases:
    got = verdict(command, tx)
    ok = got == want
    fails += not ok
    print("  %s %s: want %s, got %s" % ("ok  " if ok else "FAIL", label, want, got))
print("ALL PASS (%d cases)" % len(cases) if not fails else "%d of %d FAIL" % (fails, len(cases)))
sys.exit(1 if fails else 0)
