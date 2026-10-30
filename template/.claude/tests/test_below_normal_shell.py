"""Proof for scripts/lib/below_normal_shell.sh: it runs its one shell-string argument and passes the
command's output and exit status through, so a CLI using it as CLAUDE_CODE_SHELL_PREFIX sees every
command exactly as it would unprefixed.

    python3 .claude/tests/test_below_normal_shell.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sidecar_launch import git_bash  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "below_normal_shell.sh"
BASH = git_bash()
failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"{'ok' if ok else 'FAIL'} {label}" + ("" if ok else f" :: {detail}"))
    failures += not ok


if BASH is None:
    print("CANNOT RUN: bash not on PATH")
    sys.exit(1)
r = subprocess.run([BASH, str(SCRIPT), "printf '%s|%s' one 'two words'"], capture_output=True, text=True,
                   stdin=subprocess.DEVNULL)
check("the shell string runs as one command", r.returncode == 0 and r.stdout == "one|two words", (r.returncode, r.stdout, r.stderr))
r = subprocess.run([BASH, str(SCRIPT), "echo err >&2; exit 7"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
check("exit status and stderr pass through", r.returncode == 7 and r.stderr.strip() == "err", (r.returncode, r.stderr))
print("all ok" if not failures else f"{failures} failure(s)")
sys.exit(int(bool(failures)))
