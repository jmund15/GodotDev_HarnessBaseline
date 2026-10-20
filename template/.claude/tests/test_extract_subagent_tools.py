#!/usr/bin/env python3
"""Proof for tools/extract_subagent_tools.py.

Plants transcripts under a temporary login (`CLAUDE_CONFIG_DIR`) and runs the CLI as a child, so the
transcript root, the subagent lookup and the `--session` form of a headless `claude -p` child are
checked end to end.

    python3 .claude/tests/test_extract_subagent_tools.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "extract_subagent_tools.py"
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def transcript(path: Path, names):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"message": {"role": "user", "content": "hi"}})]
    lines.append(json.dumps({"message": {"role": "assistant",
                                         "content": [{"type": "tool_use", "name": n} for n in names]}}))
    lines.append("not json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(login: Path, *args):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(login), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=30, env=env)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        login = Path(tmp) / "login"
        projects = login / "projects"
        transcript(projects / "proj-a" / "sess1" / "subagents" / "agent-abc.jsonl", ["Read", "Grep"])
        transcript(projects / "proj-b" / "headless1.jsonl", ["Bash", "Edit"])

        out = run(login, "abc", "sess1")
        check("a subagent transcript is read from the session's login, tools in call order",
              out.returncode == 0 and out.stdout.split() == ["Read", "Grep"], out.stdout + out.stderr)
        out = run(login, "abc")
        check("a subagent found without its session id by the wide scan",
              out.stdout.split() == ["Read", "Grep"], out.stdout + out.stderr)
        out = run(login, "--session", "headless1")
        check("--session reads a headless child's own transcript under any project",
              out.returncode == 0 and out.stdout.split() == ["Bash", "Edit"], out.stdout + out.stderr)
        out = run(login, "--session", "no-such-session")
        check("--session on an unknown session prints nothing and exits 0",
              out.returncode == 0 and out.stdout.strip() == "", out.stdout + out.stderr)
        out = run(login, "--session")
        check("--session without an id is a usage error", out.returncode == 2 and "usage" in out.stderr, out.stderr)
        out = run(login, "no-such-agent", "sess1")
        check("an unknown subagent prints nothing", out.returncode == 0 and out.stdout.strip() == "", out.stdout)
        out = run(Path(tmp) / "other-login", "abc", "sess1")
        check("another login's transcripts are not read", out.stdout.strip() == "", out.stdout)

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for failure in FAILURES:
            print("  " + failure[:300])
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
