"""Worktree evidence must use subject bytes, except shared gitignored evidence."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import verify_claims as vc


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()


@pytest.fixture
def checkouts(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init")
    git(main, "config", "user.name", "proof")
    git(main, "config", "user.email", "proof@example.invalid")
    (main / ".gitignore").write_text(".claude/scratch/\n.claude/worktrees/\n")
    (main / "subject.md").write_text("main checkout quote\n")
    (main / "deleted.md").write_text("deleted quote\n")
    (main / "untracked.md").write_text("untracked main quote\n")
    git(main, "add", ".gitignore", "subject.md", "deleted.md")
    git(main, "commit", "-m", "plant")
    subject = main / ".claude/worktrees/subject"
    git(main, "worktree", "add", "-b", "subject", str(subject))
    (subject / "subject.md").write_text("subject checkout quote\n")
    (subject / "deleted.md").unlink()
    scratch = main / ".claude/scratch/evidence.md"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("shared scratch quote\n")
    return main, subject


@pytest.mark.parametrize("cited,quote,verdict", [
    (".claude/worktrees/subject/subject.md", "subject checkout quote", "VERIFIED"),
    (".claude/scratch/evidence.md", "shared scratch quote", "VERIFIED"),
    ("deleted.md", "deleted quote", "UNVERIFIED"),
    (".claude/worktrees/subject/deleted.md", "deleted quote", "UNVERIFIED"),
    ("untracked.md", "untracked main quote", "UNVERIFIED"),
    ("subject.md", "main checkout quote", "UNVERIFIED"),
])
def test_worktree_citation(checkouts, cited, quote, verdict):
    main, subject = checkouts
    result = vc.check_claims([{"id": "F1", "evidence": [{"file": cited, "quote": quote}]}], root=subject)
    claim = result["verdicts"]["F1"]
    assert claim["verdict"] == verdict, claim
    if "deleted.md" in cited or cited == "untracked.md":
        assert claim["items"][0]["reason"] == "missing-file"


def test_subject_scratch_wins(checkouts):
    main, subject = checkouts
    scratch = subject / ".claude/scratch/evidence.md"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("subject scratch quote\n")
    result = vc.check_claims([{"id": "F1", "evidence": [{
        "file": ".claude/scratch/evidence.md", "quote": "subject scratch quote"}]}], root=subject)
    assert result["verdicts"]["F1"]["verdict"] == "VERIFIED"


def test_reach_citations(checkouts):
    import post_check
    main, subject = checkouts
    rows, errors = post_check.result_claims("reach:proof", {
        "root": str(subject), "ruling": "local", "cites": [{
            "path": ".claude/worktrees/subject/subject.md", "quote": "subject checkout quote"}]})
    assert not errors
    assert vc.check_claims(rows, root=subject)["counts"]["VERIFIED"] == 1


def test_ledger_finding_paths(checkouts):
    import review_ledger
    main, subject = checkouts
    assert review_ledger._finding_paths([
        {"file": ".claude/worktrees/subject/subject.md:1"}], subject) == ["subject.md"]


def test_ledger_read_engagement(checkouts):
    import review_ledger
    main, subject = checkouts
    assert review_ledger._must_read_engaged([{
        "path": ".claude/worktrees/subject/subject.md", "read": [[1, 1]]}], subject) == {"subject.md"}


@pytest.mark.parametrize("called", [None, [".claude/worktrees/subject/subject.md"]])
def test_ledger_target_engagement(checkouts, called):
    from types import SimpleNamespace
    import review_ledger as rl
    main, subject = checkouts
    seat = rl._sidecar_seats({"coverage": [{"label": "proof", "status": "completed",
        "mandates": ["harness-quality"], "tier": "executor", "effort": "high",
        "checked": {"toolsUsed": ["Read:.claude/worktrees/subject/subject.md"],
                    "mandates": {"harness-quality": "clean"}, "stoppedAt": "nothing-in-scope"}}]}, rl.load_catalog())[0]
    seat["called"] = called
    record = rl._RecordContext({}, SimpleNamespace(toplevel=subject), {"subject.md": "blob"},
                               {}, "now", "proof", rl.load_catalog(), False, ())
    rows = rl._seat_rows(seat, [], record)
    assert any(row["outcome"] == "clean" and row["files"] == {"subject.md": "blob"} for row in rows), rows


def test_non_git_root(tmp_path):
    (tmp_path / "evidence.md").write_text("vault quote\n")
    result = vc.check_claims([{"id": "F1", "evidence": [{
        "file": "evidence.md", "quote": "vault quote"}]}], root=tmp_path)
    assert result["verdicts"]["F1"]["verdict"] == "VERIFIED"
