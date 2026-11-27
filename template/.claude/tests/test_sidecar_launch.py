#!/usr/bin/env python3
"""Proof for tools/sidecar_launch.py -- the shell choice every Python sidecar caller shares.

The load-bearing case is the refusal: on Windows a bare `bash` can resolve to the System32 WSL shim,
whose HOME is a different filesystem, so a launcher run through it misreports a logged-in account
as logged out. `git_bash()` must never hand that shim back: when `_msys.bash()` finds no Git Bash it
raises instead (tests/test_msys.py decides the search itself, with this caller's planted-install
case). The other cases pin the override and that `run_launcher` passes argv, cwd and merged env
through to a real bash.

Run: python3 .claude/tests/test_sidecar_launch.py
"""
import importlib.util
import os
import sys
import tempfile
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import _supervised_launch

os.environ.update(LAUNCH_MIN_COMMIT_GIB='0', LAUNCH_MIN_PHYSICAL_GIB='0')
MOD = os.path.join(HERE, "..", "tools", "sidecar_launch.py")
spec = importlib.util.spec_from_file_location("sl", MOD)
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

def with_env(sidecar_bash, found):
    """`git_bash()` with SIDECAR_BASH set or unset and `_msys.bash()` answering `found`."""
    with patch.dict(os.environ, {"SIDECAR_BASH": sidecar_bash} if sidecar_bash else {}), \
            patch.object(sl._msys, "bash", return_value=found):
        if not sidecar_bash:
            os.environ.pop("SIDECAR_BASH", None)
        return sl.git_bash()


def raises_when_no_git_bash():
    try:
        with_env(None, None)
    except RuntimeError as exc:
        return "SIDECAR_BASH" in str(exc)
    return False


def main():
    cases = []
    cases.append(("SIDECAR_BASH overrides every other rule",
                  with_env("/custom/bash", "C:/Git/bin/bash.exe") == "/custom/bash"))
    cases.append(("with no override the launch runs the Git Bash _msys finds",
                  with_env(None, "C:/Git/bin/bash.exe") == "C:/Git/bin/bash.exe"))
    cases.append(("NEGATIVE: no Git Bash (only the WSL shim) raises and names the override",
                  raises_when_no_git_bash()))

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

    attempts = os.path.join(tmp, 'attempts')
    with open(launcher, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('n=$(( $(cat "$SL_ATTEMPTS" 2>/dev/null || printf 0) + 1 ))\n'
                 'printf "%s" "$n" > "$SL_ATTEMPTS"\n'
                 '[ "$n" = 1 ] && { echo "REFUSED memory headroom" >&2; exit 12; }\n'
                 'printf "normal:%s" "$1"\nexit 0\n')
    events = []
    real_run = sl.subprocess.run

    def traced_run(cmd, **kwargs):
        events.append('wait' if 'wait' in cmd and str(cmd[1]).endswith('launch_memory.py') else 'child')
        return real_run(cmd, **kwargs)

    with patch.object(_supervised_launch.subprocess, 'run', side_effect=traced_run):
        retried = sl.run_launcher(launcher.replace('\\', '/'), ['same-cell'], work, timeout=60,
                                  env={'SL_ATTEMPTS': attempts, 'LAUNCH_MEMORY_POLL_SEC': '1'})
    cases.append(('memory refusal retries to a normal CompletedProcess',
                  retried.returncode == 0 and retried.stdout == 'normal:same-cell'
                  and retried.stderr == ''))
    cases.append(('real admission runs before each attempt', events == ['wait', 'child', 'wait', 'child']))
    with open(attempts, encoding='utf-8') as fh:
        cases.append(('exit 12 starts no failed job: same launcher runs twice', fh.read() == '2'))
    events.clear()
    try:
        with patch.object(_supervised_launch.subprocess, 'run', side_effect=traced_run):
            sl.run_launcher(launcher, [], work, env={'LAUNCH_MEMORY_POLL_SEC': '0'})
    except sl.subprocess.CalledProcessError:
        cases.append(('invalid admission config dispatches nothing', events == ['wait']))
    else:
        cases.append(('invalid admission config dispatches nothing', False))

    failures = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", name))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
