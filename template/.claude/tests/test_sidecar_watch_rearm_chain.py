"""Prove the rearm member survives a long transcript through the full prompt_dispatch chain."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
DISPATCH = ROOT / "hooks" / "prompt_dispatch.py"
SID = "rearmproof-0000-0000-0000-000000000000"


def process(pid):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + (
        "winpid=%d " % pid if os.name == "nt" else "") + "pid=%d" % pid


with tempfile.TemporaryDirectory(prefix="rearm_chain_test_") as temp:
    root = Path(temp)
    state = root / "state"
    state.mkdir()
    record = root / "running.record.json"
    (root / "running.record.json.pid").write_text(process(os.getpid()))
    entry = {"kind": "sidecar", "id": str(record), "record": str(record), "launched_at": time.time(),
             "jobs": [{"label": "running", "launcher": "test"}]}
    (state / (SID[:8] + ".json")).write_text(json.dumps({"dispatch_table_pending": [entry]}))
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(state), HARNESS_PROMPT_DISPATCH_TIMING=str(root / "timing.jsonl"),
               CLAUDE_PROJECT_DIR=str(root), HARNESS_GIT_STATE_CACHE_DIR=str(root / "git-state-cache"))
    transcript = root / "session.jsonl"
    transcript.write_bytes(b"x" * (9 * 1024 * 1024))
    long_payload = {"session_id": SID, "transcript_path": str(transcript),
                    "prompt": "<task-notification><status>killed</status></task-notification>"}
    long_run = subprocess.run([sys.executable, str(DISPATCH)], input=json.dumps(long_payload),
                              text=True, capture_output=True, env=env, timeout=40)
    if str(record) not in long_run.stdout:
        raise AssertionError("registry survives a transcript longer than 8 MiB: %s" % long_run.stderr)
    print("PASS registry survives a transcript longer than 8 MiB")

print("test_sidecar_watch_rearm_chain: 1 passed, 0 failed")
