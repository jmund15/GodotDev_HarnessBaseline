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
import shutil
import subprocess

from _supervised_launch import launch as supervised_launch

# Git Bash, in install-order preference. `bin/bash.exe` is the wrapper Git intends callers to
# use; `usr/bin/bash.exe` is the raw MSYS binary and is the fallback, not the default.
_GIT_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def git_bash():
    """Absolute path to a POSIX bash that shares the user's Windows HOME.

    Never returns the System32 WSL shim. On a non-Windows host, `shutil.which` is authoritative
    because the WSL collision cannot occur there.
    """
    if os.environ.get("SIDECAR_BASH"):
        return os.environ["SIDECAR_BASH"]
    if os.name != "nt":
        return shutil.which("bash") or "bash"
    for candidate in _GIT_BASH_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
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
