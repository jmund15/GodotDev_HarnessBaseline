#!/usr/bin/env python3
"""Proof that reap.environ_of reads a NATIVE Windows process's environment, not only an MSYS one.

A process this session starts from PowerShell (Start-Process, a detached exe) has no MSYS pid, and once
its launcher exits it has no claude.exe ancestor either: its own environment is the only ownership
proof left. Spawns a live child because a process environment block cannot be planted.
Run: python3 .claude/tests/test_reap_native_env.py
"""
import os, subprocess, sys, time, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import reap  # noqa: E402
import _platform_skip  # noqa: E402

KEY = b"REAP_NATIVE_ENV_PROBE="


def main():
    if os.name != "nt":
        _platform_skip.skip_proof("native Windows environment block", "Windows only")
    token = uuid.uuid4().hex
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            env=dict(os.environ, REAP_NATIVE_ENV_PROBE=token))
    failures = []
    try:
        time.sleep(0.5)
        env = reap.environ_of(proc.pid)
        if KEY + token.encode() not in env:
            failures.append("native child's environment not read (got %d bytes)" % len(env))
        if reap._env_value(env, KEY) != token:
            failures.append("block is not NUL-separated KEY=VALUE like /proc environ")
    finally:
        proc.kill()
        proc.wait()
    if reap.environ_of(4) != b"":  # the System process: access denied must yield empty, never partial
        failures.append("unreadable pid did not yield b''")
    for f in failures:
        print("FAIL:", f)
    print("PASS" if not failures else "%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
