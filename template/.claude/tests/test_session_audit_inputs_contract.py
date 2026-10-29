#!/usr/bin/env python3
"""Minimal proof for tools/session_audit_inputs.py: an unreadable roster refused, an out path outside the
checkout refused, and a two-commit planted checkout frozen into a fresh input folder.

    python3 .claude/tests/test_session_audit_inputs_contract.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "session_audit_inputs.py"
GIT_IDENTITY = ["-c", "user.name=planted", "-c", "user.email=planted@example.invalid"]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *GIT_IDENTITY, *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def planted_checkout(directory):
    repo = Path(directory) / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "planted.txt").write_text("first\n", encoding="utf-8")
    git(repo, "add", "planted.txt")
    git(repo, "commit", "-q", "-m", "planted base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "planted.txt").write_text("second\n", encoding="utf-8")
    git(repo, "add", "planted.txt")
    git(repo, "commit", "-q", "-m", "planted change")
    roster = Path(directory) / "roster.json"
    roster.write_text("[]\n", encoding="utf-8")
    return repo, base, roster


def run(repo, base, roster, out):
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CODE_SESSION_ID"}
    return subprocess.run([sys.executable, str(TOOL), "--root", str(repo), "--base-ref", "HEAD~1",
                           "--session-base", base, "--roster", str(roster), "--out", out],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def case_unreadable_roster_exits_nonzero(directory):
    repo, base, _ = planted_checkout(directory)
    done = run(repo, base, Path(directory) / "absent.json", ".claude/scratch/run/inputs.json")
    assert done.returncode == 1, "an unreadable roster must exit 1"
    assert "cannot read roster" in done.stderr, done.stderr


def case_out_path_outside_checkout_exits_nonzero(directory):
    repo, base, roster = planted_checkout(directory)
    outside = str(Path(directory) / "elsewhere" / "inputs.json")
    done = run(repo, base, roster, outside)
    assert done.returncode == 1, "an out path outside the checkout must exit 1"
    assert not Path(outside).parent.exists(), "a refused freeze must write nothing"


def case_planted_checkout_freezes_inputs(directory):
    repo, base, roster = planted_checkout(directory)
    done = run(repo, base, roster, ".claude/scratch/run/inputs.json")
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout).get("head"), done.stdout
    frozen = repo / ".claude" / "scratch" / "run" / "inputs.json"
    assert frozen.is_file(), "the frozen packet must be written"


def case_must_read_holds_whole_copies_only_of_whole_judged_files(directory):
    repo, _, roster = planted_checkout(directory)
    edited = {".claude/commands/doc.md": "rule one\n", ".claude/hooks/guard.py": "'''doc'''\nA = 1\n",
              ".claude/tools/tool.py": "B = 1\n"}
    for name, text in edited.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", *edited)
    git(repo, "commit", "-q", "-m", "planted subjects")
    base = git(repo, "rev-parse", "HEAD")
    for name, text in edited.items():
        (repo / name).write_text(text + "changed\n", encoding="utf-8")
    (repo / ".claude/tools/new_tool.py").write_text("C = 1\n", encoding="utf-8")
    subjects = [*edited, ".claude/tools/new_tool.py"]
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CODE_SESSION_ID"}
    done = subprocess.run([sys.executable, str(TOOL), "--root", str(repo), "--base-ref", "HEAD~1",
                           "--session-base", base, "--roster", str(roster), "--out", ".claude/scratch/run/inputs.json",
                           "--files", *subjects], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    must = [Path(p).name for p in json.loads(done.stdout)["mustRead"]]
    whole = {i for i, name in enumerate(subjects) if f"subject-{i}.txt" in must}
    assert whole == {0, 1}, f"whole copies on must-read for {sorted(subjects[i] for i in whole)}; want the doc and the hook"
    assert all(f"subject-{i}.diff" in must for i in range(len(subjects))), must
    assert "decisions.json" not in must, "decisions repeat inside doctrine-commits.md"
    folder = repo / ".claude" / "scratch" / "run"
    assert all((folder / f"subject-{i}.txt").is_file() for i in range(3)), "each frozen copy stays on disk"


def main():
    cases = [case_unreadable_roster_exits_nonzero, case_out_path_outside_checkout_exits_nonzero,
             case_planted_checkout_freezes_inputs, case_must_read_holds_whole_copies_only_of_whole_judged_files]
    failures = []
    for case in cases:
        directory = tempfile.mkdtemp(prefix="session-audit-inputs-proof-")
        try:
            case(directory)
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
