#!/usr/bin/env python3
"""Proof for `settings.json` `env.PYTHON_BASIC_REPL`: Python started on a NUL stdin exits.

On win32, Git Bash maps `/dev/null`, and an empty heredoc, to NUL, which Python's isatty() reports as a
console. `python3 -` there starts the REPL; the default REPL's Windows console probe fails and loops
writing tracebacks (hundreds of MB a minute). The basic REPL reads EOF from NUL and exits. The setting
covers every route to a NUL stdin, so no command pattern needs rewriting.

Cases (win32):
- control: with PYTHON_BASIC_REPL removed, `python -` on NUL stdin is still running after the bound
  (the fixture reaches the failure);
- with the composed settings env, `python -` on NUL stdin exits 0 within the bound and writes no
  traceback.
Off win32 NUL is not a console: the settings case runs, the control is skipped.

    python3 .claude/tests/test_python_null_stdin.py
"""
import json
import os
import subprocess
import sys
import tempfile

CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = next((p for p in (os.path.join(CLAUDE_DIR, name) for name in ("settings.json", "settings.base.json"))
                 if os.path.isfile(p)), os.path.join(CLAUDE_DIR, "settings.json"))
BOUND_S = 8


def run_on_null_stdin(env, tmp):
    """(exited, returncode, stderr head) for `python -` on NUL stdin, killed after BOUND_S."""
    with open(os.path.join(tmp, "stderr.txt"), "w+b") as err:
        proc = subprocess.Popen([sys.executable, "-"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=err, env=env)
        try:
            code, exited = proc.wait(timeout=BOUND_S), True
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code, exited = None, False
        err.seek(0)
        return exited, code, err.read(4096)


def main():
    base = {k: v for k, v in os.environ.items() if k != "PYTHON_BASIC_REPL"}
    with open(SETTINGS, encoding="utf-8") as fh:
        settings_env = json.load(fh).get("env", {})
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        if sys.platform == "win32":
            exited, code, _ = run_on_null_stdin(base, tmp)
            if exited:
                failures.append("control: python - on NUL stdin exited (rc %s) without the setting, so the "
                                "fixture never reaches the REPL loop" % code)
        exited, code, head = run_on_null_stdin(dict(base, **settings_env), tmp)
        if not exited:
            failures.append("settings env: python - on NUL stdin still running after %d s (REPL loop); "
                            "settings.json env.PYTHON_BASIC_REPL is %r"
                            % (BOUND_S, settings_env.get("PYTHON_BASIC_REPL")))
        elif code != 0 or b"Traceback" in head:
            failures.append("settings env: rc %s, stderr %r" % (code, head[:300]))
    print("\n%d failure(s)" % len(failures) if failures else "all ok")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
