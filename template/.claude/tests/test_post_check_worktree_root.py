"""Caller artifact paths and a relative store path resolve in the subject checkout, not the invoking cwd."""
import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import post_check


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def panel(tmp_path, monkeypatch):
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init")
    git(main, "config", "user.name", "proof")
    git(main, "config", "user.email", "proof@example.invalid")
    (main / "subject.md").write_text("reviewed subject\n")
    git(main, "add", "subject.md")
    git(main, "commit", "-m", "plant")
    head = git(main, "rev-parse", "HEAD")
    blob = git(main, "rev-parse", "HEAD:subject.md")
    subject = main / ".claude/worktrees/subject"
    git(main, "worktree", "add", "-b", "subject", str(subject))
    artifacts = main / ".claude/scratch/panel"
    directory = artifacts / "sessions/s1/subagents/workflows/wf_live"
    directory.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures/evidence/review/journal.jsonl"
    events = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = copy.deepcopy(next(event for event in events if event.get("type") == "result"))
    started = copy.deepcopy(next(event for event in events if event.get("type") == "started"))
    started.update(agentId="seat", key="v2:seat", label="review:harness-quality", phase="Review")
    result.update(agentId="seat", key="v2:seat")
    result["result"].update(findings=[], gaps=[])
    result["result"]["checked"].update(toolsUsed=["Read:subject.md"], stoppedAt="nothing-in-scope",
                                         mandates={"harness-quality": "clean"})
    (directory / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in (started, result)))
    write(directory / "agent-seat.meta.json", {"agentType": "workflow-subagent", "model": "served-test"})
    write(artifacts / "seats.json", {"review:harness-quality": {
        "model": "requested-test", "effort": "high", "tier": "executor", "mandates": ["harness-quality"], "mustRead": []}})
    write(artifacts / "roster.json", [{"key": "harness-quality", "mandates": ["harness-quality"], "tier": "executor"}])
    write(artifacts / "scope.json", {"files": ["subject.md"]})
    write(artifacts / "premises.json", [head])
    write(artifacts / "blobs.json", {"subject.md": blob})
    monkeypatch.chdir(main)
    monkeypatch.setenv("HARNESS_REVIEW_LEDGER", "ledger.jsonl")
    monkeypatch.setenv("HARNESS_ESCAPE_LOG", "escape.jsonl")
    argv = ["review", "--workflow-dir", str(directory.relative_to(main)), "--seats-file", str((artifacts / "seats.json").relative_to(main)),
            "--root", str(subject.relative_to(main)), "--expect-total", "0", "--stage", "candidate",
            "--subject", "doc:subject.md", "--roster", str((artifacts / "roster.json").relative_to(main)),
            "--scope-json", str((artifacts / "scope.json").relative_to(main)),
            "--premise-commits", str((artifacts / "premises.json").relative_to(main)), "--head", head,
            "--blobs", str((artifacts / "blobs.json").relative_to(main)), "--out", "out/report.json"]
    return main, subject, argv, head, blob


@pytest.mark.parametrize("frozen", [True, False])
def test_relative_artifacts_record_in_subject_checkout(panel, frozen):
    main, subject, argv, head, blob = panel
    if not frozen:
        for flag in ("--premise-commits", "--blobs"):
            index = argv.index(flag)
            del argv[index:index + 2]
    code = post_check.main(argv)
    report = json.loads((main / "out/report.json").read_text())
    assert code == 0, report
    assert report["ledger"]["panelRows"] == 1
    assert not (main / "ledger.jsonl").exists()
    rows = [json.loads(line) for line in (subject / "ledger.jsonl").read_text().splitlines()]
    assert rows[0]["head"] == head
    assert rows[0]["premise_commits"] == ([head] if frozen else [])
    assert rows[0]["files"] == {"subject.md": blob}


def test_a_subject_from_another_repository_never_becomes_the_ledger_home(panel, tmp_path):
    # Captured live: wf_58595384-a21 folded a main-repo subject and a baseline-repo worktree; the baseline
    # subject came back first, the frozen --head named no commit there, and the panel recorded no row at all.
    main, subject, argv, head, blob = panel
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init")
    first, own = SimpleNamespace(root=other), SimpleNamespace(root=subject)
    home, recorded, foreign = post_check.ledger_home([first, own], main)
    assert Path(home).resolve() == subject.resolve()
    assert recorded == [own] and foreign == [first]
    home, recorded, foreign = post_check.ledger_home([first], main)
    assert Path(home).resolve() == main.resolve() and recorded == [] and foreign == [first]
    home, recorded, foreign = post_check.ledger_home([], subject)
    assert Path(home).resolve() == subject.resolve() and recorded == [] and foreign == []


def test_intake_absolutizes_paths_but_not_revisions(panel, monkeypatch):
    main, subject, argv, head, blob = panel
    captured = []
    monkeypatch.setattr(post_check, "review", lambda args, **kw: captured.append(args) or 0)
    assert post_check.main(argv) == 0
    args = captured[0]
    for name in ("root", "workflow_dir", "seats_file", "roster", "scope_json", "premise_commits", "blobs", "out"):
        path = Path(getattr(args, name))
        assert path.is_absolute(), (name, path)
        assert path.is_relative_to(main)
    assert args.head == head
    assert args.subject == "doc:subject.md"


def test_other_path_flags_at_intake(panel, monkeypatch):
    main, subject, argv, head, blob = panel
    captured = []
    monkeypatch.setattr(post_check, "review", lambda args, **kw: captured.append(args) or 0)
    assert post_check.main(["review", "--findings", "findings.json", "--spill-dir", "spills",
        "--digest", "one.json", "--digest", "two.json", "--snapshot", "one.snap.json",
        "--snapshot", "two.snap.json", "--files", "subject.md", "--out", "report.json"]) == 0
    args = captured[0]
    assert args.findings == str(main / "findings.json")
    assert args.spill_dir == str(main / "spills")
    assert args.digest == [str(main / "one.json"), str(main / "two.json")]
    assert args.snapshot == [str(main / "one.snap.json"), str(main / "two.snap.json")]
    assert args.files == ["subject.md"]
    monkeypatch.setattr(post_check, "snapshot", lambda args: captured.append(args) or 0)
    assert post_check.main(["snapshot", "--root", str(subject), "--files", "subject.md",
        "--new-paths", "new.md", "--base", "HEAD~1", "--out", "manifest.json"]) == 0
    args = captured[-1]
    assert args.out == str(main / "manifest.json")
    assert (args.files, args.new_paths, args.base) == (["subject.md"], ["new.md"], "HEAD~1")
    monkeypatch.setattr(post_check, "claims", lambda args: captured.append(args) or 0)
    assert post_check.main(["claims", "--result", "result.json", "--out", "claims.json"]) == 0
    assert captured[-1].result == str(main / "result.json")


def test_sidecar_close_uses_same_intake(panel, monkeypatch):
    import sidecar_fanout
    main, subject, argv, head, blob = panel
    captured = []
    monkeypatch.setattr(post_check, "review", lambda args, **kw: captured.append(args) or 0)
    args = SimpleNamespace(ledger_stage="candidate", ledger_subject="doc:subject.md",
        ledger_roster=".claude/scratch/panel/roster.json", ledger_scope_json=".claude/scratch/panel/scope.json",
        ledger_task="proof", ledger_premise_commits=".claude/scratch/panel/premises.json", ledger_head=head,
        ledger_blobs=".claude/scratch/panel/blobs.json", no_new_decision=False, root=str(subject.relative_to(main)))
    assert sidecar_fanout.close_review(args, Path(".claude/scratch/panel")) == 0
    parsed = captured[0]
    for name in ("sidecar_dir", "out", "root", "roster", "scope_json", "premise_commits", "blobs"):
        assert Path(getattr(parsed, name)).is_absolute(), name
    assert parsed.head == head
