"""Coverage clause 10: a row covers a request only at the rigor it was recorded at or above it."""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))

import review_ledger  # noqa: E402

CATALOG = review_ledger.load_catalog()
PATH, MANDATE = next((path, key) for path in ("a.md", "a.py") for key, entry in CATALOG.items()
                     if not (entry or {}).get("interaction") and review_ledger._owes(path, key))


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp):
    repo = Path(tmp) / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / PATH).write_text("a\n", encoding="utf-8")
    git(repo, "add", PATH)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "file")
    return repo, base


@contextlib.contextmanager
def env(**values):
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def plant(records, task, rigor, session=None):
    body = {"task_id": task, "rigor": rigor}
    if session:
        body["session_id"] = session
    (records / f"{task}.json").write_text(json.dumps(body), encoding="utf-8")


def row_at(repo, base, rigor=None):
    ctx = review_ledger.Ctx(repo, base)
    row = {"v": 1, "id": "t:1", "stage": "pre-merge", "subject": "session:s1", "head": ctx.head,
           "mergeBase": ctx.merge_base(), "mandate": MANDATE, "ruleHash": review_ledger.rule_hash(MANDATE),
           "files": {PATH: ctx.hash(PATH)}, "outcome": "clean", "reviewer": "inline:fold",
           "ts": "2026-01-01T00:00:00Z"}
    if rigor is not None:
        row["rigor"] = rigor
    return row


def covers(tmp, repo, base, row_rigor, request_rigor):
    """Whether a row recorded at `row_rigor` covers a request at `request_rigor` (None: no resolvable rigor)."""
    records = Path(tmp) / "records"
    records.mkdir(exist_ok=True)
    plant(records, "req", request_rigor if request_rigor is not None else "bogus")
    row = row_at(repo, base, row_rigor)
    with env(HARNESS_TASK_RECORD_DIR=str(records), CLAUDE_CODE_SESSION_ID=None):
        cover = review_ledger.Cover(review_ledger.Ctx(repo, base), CATALOG, [row], [], "session", task_id="req")
        return cover.covers(row, MANDATE, PATH)


def test_light_row_does_not_cover_a_thorough_request():
    with tempfile.TemporaryDirectory() as tmp:
        repo, base = make_repo(tmp)
        assert covers(tmp, repo, base, "light", "thorough") is False


def test_thorough_row_covers_a_light_request():
    with tempfile.TemporaryDirectory() as tmp:
        repo, base = make_repo(tmp)
        assert covers(tmp, repo, base, "thorough", "light") is True


def test_row_without_rigor_covers_every_request():
    with tempfile.TemporaryDirectory() as tmp:
        repo, base = make_repo(tmp)
        for request in ("light", "normal", "thorough", None):
            assert covers(tmp, repo, base, None, request) is True, request


def test_request_without_rigor_needs_a_thorough_or_legacy_row():
    with tempfile.TemporaryDirectory() as tmp:
        repo, base = make_repo(tmp)
        assert covers(tmp, repo, base, "normal", None) is False
        assert covers(tmp, repo, base, "thorough", None) is True


def test_light_row_does_not_cover_an_unrated_request():
    with tempfile.TemporaryDirectory() as tmp:
        repo, base = make_repo(tmp)
        assert covers(tmp, repo, base, "light", None) is False


def test_owed_range_reads_the_named_tasks_rigor():
    with tempfile.TemporaryDirectory() as tmp:
        repo, base = make_repo(tmp)
        records = Path(tmp) / "records"
        records.mkdir()
        plant(records, "thorough-task", "thorough")
        plant(records, "light-task", "light")
        ledger = Path(tmp) / "ledger.jsonl"
        ledger.write_text(json.dumps(row_at(repo, base, "light")) + "\n", encoding="utf-8")
        with env(HARNESS_REVIEW_LEDGER=str(ledger), HARNESS_TASK_RECORD_DIR=str(records),
                 CLAUDE_CODE_SESSION_ID=None):
            owed = review_ledger.owed_range(repo, base, "HEAD", [MANDATE], None, None, task_id="thorough-task")
            assert len(owed) == 1, owed
            owed = review_ledger.owed_range(repo, base, "HEAD", [MANDATE], None, None, task_id="light-task")
            assert owed == [], owed


def test_normal_row_covers_normal_request_through_owed_range():
    with tempfile.TemporaryDirectory() as tmp:
        repo, base = make_repo(tmp)
        records = Path(tmp) / "records"
        records.mkdir()
        session = "sess0000-normal"
        plant(records, "session-task", "normal", session=session)
        ledger = Path(tmp) / "ledger.jsonl"
        ledger.write_text(json.dumps(row_at(repo, base, "normal")) + "\n", encoding="utf-8")
        with env(HARNESS_REVIEW_LEDGER=str(ledger), HARNESS_TASK_RECORD_DIR=str(records),
                 CLAUDE_CODE_SESSION_ID=session):
            owed = review_ledger.owed_range(repo, base, "HEAD", [MANDATE], None, None)
            assert owed == [], owed


TESTS = (
    test_light_row_does_not_cover_a_thorough_request,
    test_thorough_row_covers_a_light_request,
    test_row_without_rigor_covers_every_request,
    test_request_without_rigor_needs_a_thorough_or_legacy_row,
    test_light_row_does_not_cover_an_unrated_request,
    test_owed_range_reads_the_named_tasks_rigor,
    test_normal_row_covers_normal_request_through_owed_range,
)


def main():
    failures = []
    for test in TESTS:
        try:
            test()
            print("PASS", test.__name__)
        except Exception as exc:
            failures.append(test.__name__)
            print("FAIL", test.__name__, repr(exc))
    print(f"{'FAIL' if failures else 'PASS'} test_review_ledger_bar ({len(TESTS) - len(failures)}/{len(TESTS)})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
