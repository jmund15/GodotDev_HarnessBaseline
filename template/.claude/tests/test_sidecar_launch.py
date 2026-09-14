#!/usr/bin/env python3
"""Proof for tools/sidecar_launch.py -- the shell choice every Python sidecar caller shares.

The load-bearing case is the refusal: on Windows a bare `bash` can resolve to the System32 WSL shim,
whose HOME is a different filesystem, so a launcher run through it misreports a logged-in account
as logged out. `git_bash()` must never hand that shim back -- it raises instead. The other cases pin
the override, the non-Windows path, candidate preference, and that `run_launcher` passes argv, cwd
and merged env through to a real bash.

Run: python3 .claude/tests/test_sidecar_launch.py
"""
import importlib.util
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "sidecar_launch.py")
spec = importlib.util.spec_from_file_location("sl", MOD)
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

REAL_OS = sl.os
REAL_WHICH = sl.shutil.which
REAL_CANDIDATES = sl._GIT_BASH_CANDIDATES


def fake_os(name, environ, isfile):
    """A stand-in for the module's `os` with a controllable platform, environment and file probe."""
    path = types.SimpleNamespace(isfile=isfile)
    return types.SimpleNamespace(name=name, environ=environ, path=path)


def with_env(name, environ, candidates=REAL_CANDIDATES, isfile=lambda p: False, which=lambda _: None):
    sl.os = fake_os(name, environ, isfile)
    sl._GIT_BASH_CANDIDATES = candidates
    sl.shutil.which = which
    try:
        return sl.git_bash()
    finally:
        sl.os = REAL_OS
        sl._GIT_BASH_CANDIDATES = REAL_CANDIDATES
        sl.shutil.which = REAL_WHICH


def raises_on_wsl_only():
    try:
        with_env("nt", {}, candidates=("C:/nope/bash.exe",),
                 which=lambda _: r"C:\Windows\System32\bash.exe")
    except RuntimeError as exc:
        return "SIDECAR_BASH" in str(exc)
    return False


def main():
    cases = []
    cases.append(("SIDECAR_BASH overrides every other rule",
                  with_env("nt", {"SIDECAR_BASH": "/custom/bash"}) == "/custom/bash"))
    cases.append(("a non-Windows host trusts shutil.which",
                  with_env("posix", {}, which=lambda _: "/usr/bin/bash") == "/usr/bin/bash"))
    cases.append(("a non-Windows host with no bash on PATH falls back to the bare name",
                  with_env("posix", {}) == "bash"))
    first, second = "C:/Git/bin/bash.exe", "C:/Git/usr/bin/bash.exe"
    cases.append(("Windows prefers the first existing Git Bash candidate",
                  with_env("nt", {}, candidates=(first, second), isfile=lambda p: True) == first))
    cases.append(("Windows skips a missing candidate",
                  with_env("nt", {}, candidates=(first, second),
                           isfile=lambda p: p == second) == second))
    cases.append(("Windows accepts a non-System32 bash from PATH when no candidate exists",
                  with_env("nt", {}, candidates=(first,),
                           which=lambda _: "D:/tools/msys/bash.exe") == "D:/tools/msys/bash.exe"))
    cases.append(("NEGATIVE: Windows refuses the System32 WSL shim and names the override",
                  raises_on_wsl_only()))

    tmp = tempfile.mkdtemp(prefix="sidecar_launch_")
    launcher = os.path.join(tmp, "probe.sh")
    with open(launcher, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('printf "%s|%s|%s" "$1" "$(basename "$PWD")" "$SL_PROBE"\nexit 3\n')
    work = os.path.join(tmp, "workdir")
    os.makedirs(work)
    try:
        proc = sl.run_launcher(launcher.replace("\\", "/"), ["alpha"], work, timeout=60,
                               env={"SL_PROBE": "merged"})
        cases.append(("run_launcher passes argv, cwd and merged env to a real bash",
                      proc.stdout.strip() == "alpha|workdir|merged"))
        cases.append(("run_launcher returns the launcher's own exit code", proc.returncode == 3))
        kept = sl.run_launcher(launcher.replace("\\", "/"), ["beta"], work, timeout=60)
        cases.append(("NEGATIVE: env=None keeps the inherited environment rather than emptying it",
                      kept.returncode == 3 and kept.stdout.startswith("beta|workdir|")))
    except RuntimeError as exc:
        print("CANNOT-RUN no usable bash for the live cases:", exc)
        return 2

    failures = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", name))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
