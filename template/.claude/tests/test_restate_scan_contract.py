#!/usr/bin/env python3
"""Minimal proof for tools/restate_scan.py: a repo without an instruction scope is refused, and a configured
scope proves the scan finds a planted near-copy through its own selftest.

    python3 .claude/tests/test_restate_scan_contract.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "restate_scan.py"

SCOPE = {"instruction_scope": {"paths": [".claude/**/*.md"], "rows_dir": ".claude/scratch/restate-rows"}}


def run(repo, *args):
    return subprocess.run([sys.executable, str(TOOL), "--repo", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def planted_repo(directory, scope):
    repo = Path(directory) / "repo"
    (repo / ".claude" / "skills" / "project_subsystems").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    if scope is not None:
        adaptation = repo / ".claude" / "skills" / "project_subsystems" / "adaptation.json"
        adaptation.write_text(json.dumps(scope), encoding="utf-8")
    return repo


def case_repo_without_scope_is_refused(directory):
    repo = planted_repo(directory, None)
    done = run(repo, "pairs")
    assert done.returncode == 2, "no instruction_scope must exit 2, never report zero candidates"
    assert "no instruction_scope" in done.stdout, done.stdout


def case_selftest_finds_planted_near_copy(directory):
    repo = planted_repo(directory, SCOPE)
    done = run(repo, "selftest")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "the planted near-copy is a group" in done.stdout, done.stdout


def plant(repo, files):
    for rel, text in files.items():
        path = repo / ".claude" / "commands" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)


def pair_channels(done):
    """[(channels, member files)] of every pair the scan printed."""
    out = []
    for group in json.loads(done.stdout.splitlines()[0]):
        files = {m["hash"]: m["path"].rsplit("/", 1)[-1] for m in group["members"]}
        out += [(len(p["channels"]), sorted((files[p["a"]], files[p["b"]]))) for p in group["pairs"]]
    return out


def case_strong_keeps_only_pairs_two_channels_agree_on(directory):
    repo = planted_repo(directory, SCOPE)
    plant(repo, {
        "owner.md": "Never run a second native job while the five-hour window is above its threshold; "
                    "the guard denies it and names the reset.\n",
        "copy.md": "Never run a second native job while the five-hour window is over its threshold; "
                   "the guard denies it and names the reset.\n",
        "left.md": "Always cache zeppelin output before the gate runs and never touch the index afterwards.\n",
        "right.md": "Cannot start a zeppelin session until every reviewer has returned no verdict.\n",
    })
    every = pair_channels(run(repo, "pairs", "--json"))
    done = run(repo, "pairs", "--json", "--strong")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [n for n, _ in every if n == 1], "the plant must hold a single-channel pair: %s" % every
    assert pair_channels(done) == [(2, ["copy.md", "owner.md"])], pair_channels(done)
    held = len([n for n, _ in every if n == 1])
    assert "held back %d" % held in done.stderr, done.stderr
    group_id = json.loads(done.stdout.splitlines()[0])[0]["id"]
    ruled = run(repo, "rule", group_id, "distinct", "--reason", "two decisions", "--strong")
    assert ruled.returncode == 0, "a group id printed under --strong is ruled under --strong: " + ruled.stdout
    assert json.loads(run(repo, "pairs", "--json", "--strong").stdout.splitlines()[0]) == []


def case_changed_centers_only_on_sentences_written_since_base(directory):
    repo = planted_repo(directory, SCOPE)
    owner = ("Never run a second native job while the five-hour window is above its threshold; "
             "the guard denies it and names the reset.\n")
    plant(repo, {"owner.md": owner, "copy.md": owner.replace("above", "over")})
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"],
                   check=True, capture_output=True)
    assert pair_channels(run(repo, "pairs", "--json")), "the seeded pair is a candidate over the whole scope"
    assert pair_channels(run(repo, "pairs", "--json", "--changed", "--base", "HEAD")) == [], \
        "a pair neither of whose sentences was written since base is not a candidate"
    plant(repo, {"third.md": owner.replace("above", "beyond")})
    got = pair_channels(run(repo, "pairs", "--json", "--changed", "--base", "HEAD", "--paths", ".claude/commands/third.md"))
    assert got and all("third.md" in files for _n, files in got), got


def main():
    cases = [case_repo_without_scope_is_refused, case_selftest_finds_planted_near_copy,
             case_strong_keeps_only_pairs_two_channels_agree_on,
             case_changed_centers_only_on_sentences_written_since_base]
    failures = []
    for case in cases:
        directory = tempfile.mkdtemp(prefix="restate-scan-proof-")
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
