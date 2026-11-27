#!/usr/bin/env python3
"""Invoke a sidecar launcher from Python correctly.

Exists because `subprocess.run(["bash", ...])` is WRONG on this machine and fails in a way that
blames the launcher. Windows Python resolves a bare `bash` against the Windows PATH, where the
first hit is `C:\\Windows\\System32\\bash.exe` -- **WSL's** bash, a different filesystem
namespace. Measured 2026-08-20: it reports a WSL-side `HOME=/home/<user>`, so every `$HOME`-relative
credential path misses and the launcher exits 3 claiming the account is not logged in, on a
machine that is logged in. Nothing errors at the shell level; the symptom names the wrong
subsystem entirely.

Every Python caller that dispatches a sidecar routes through here, so the shell choice is made
once instead of being rediscovered per caller.
"""
import os
import subprocess
import sys

from _supervised_launch import launch as supervised_launch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
import _msys  # noqa: E402


def git_bash():
    """A POSIX bash that shares the user's Windows HOME: `SIDECAR_BASH` when set, else `_msys.bash()`, which
    never returns the System32 WSL shim. Raises when neither names one, since a launch must not fall back to it."""
    if os.environ.get("SIDECAR_BASH"):
        return os.environ["SIDECAR_BASH"]
    found = _msys.bash()
    if found:
        return found
    raise RuntimeError(
        "no Git Bash found and the only `bash` on PATH is the WSL shim, which does not share "
        "this user's HOME. Set SIDECAR_BASH to a POSIX bash that does."
    )


def run_launcher(launcher, args, cwd, timeout=1800, env=None):
    """Wait for memory, retry exit 12, and return the final CompletedProcess.

    `launcher` and `cwd` are paths as the OS sees them; bash receives the launcher as a plain
    argument, so forward slashes work either way.
    """
    cmd = [git_bash(), str(launcher)] + [str(a) for a in args]
    merged = dict(os.environ)
    if env:
        merged.update(env)
    label = os.path.basename(str(launcher))
    for index in range(2, len(cmd) - 1):
        if cmd[index] == '-l':
            label = cmd[index + 1]
            break
    result = None

    def child():
        nonlocal result
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                                timeout=timeout, env=merged)
        return result.returncode

    supervised_launch(label, child, merged)
    return result


if __name__ == "__main__":
    print(git_bash())
