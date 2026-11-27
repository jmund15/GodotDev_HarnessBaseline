#!/usr/bin/env python3
"""Every transcript reader finds this session's transcripts under its own login.

A session on a second login (`CLAUDE_CONFIG_DIR`) writes transcripts under that login's `projects`
dir. A reader that hardcodes `~/.claude/projects` finds nothing there, so the reaper refuses an
owner-worded stop and the usage scans come back empty. Each reader's default root is observed in a
child process with the login planted, then with it unset.

    python3 .claude/tests/test_session_projects_dir.py
"""
import os
import subprocess
import sys
import tempfile

TOOLS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

# Each probe prints the root its reader uses when the caller names none.
PROBES = {
    "model_registry.session_projects_dir": "import model_registry as m; print(m.session_projects_dir())",
    "reap.PROJECTS_ROOT": "import reap; print(reap.PROJECTS_ROOT)",
    "extract_subagent_tools.PROJECTS_ROOT": "import extract_subagent_tools as e; print(e.PROJECTS_ROOT)",
    "orchestration_metrics._projects_root": "import orchestration_metrics as o; print(o._projects_root())",
    "transcript_usage.scan": (
        "import transcript_usage as t\n"
        "seen = []\n"
        "t.transcript_files = lambda root, since: seen.append(root) or []\n"
        "t.scan()\n"
        "print(seen[0])"),
}

failures = []


def norm(p):
    return os.path.normcase(os.path.normpath(p.strip()))


def resolved(code, env):
    got = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n%s" % (TOOLS, code)],
                         env=env, capture_output=True, text=True, timeout=60)
    lines = got.stdout.strip().splitlines()
    return (lines[-1] if lines else ""), got.stderr[-300:]


def main():
    base = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECTS_ROOT")}
    default = norm(os.path.expanduser("~/.claude/projects"))
    with tempfile.TemporaryDirectory() as td:
        login = norm(os.path.join(td, "projects"))
        for name, code in PROBES.items():
            got, err = resolved(code, dict(base, CLAUDE_CONFIG_DIR=td))
            ok = norm(got) == login
            print(("  PASS  " if ok else "  FAIL  ") + "%s reads the second login's projects dir" % name
                  + ("" if ok else "\n        got %r %s" % (got, err)))
            if not ok:
                failures.append(name)
            got, err = resolved(code, base)
            ok = norm(got) == default
            print(("  PASS  " if ok else "  FAIL  ") + "%s reads ~/.claude/projects with no login set" % name
                  + ("" if ok else "\n        got %r %s" % (got, err)))
            if not ok:
                failures.append(name + " (default)")
    print("test_session_projects_dir: %d failed" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
