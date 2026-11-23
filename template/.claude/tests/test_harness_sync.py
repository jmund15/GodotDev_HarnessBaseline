"""harness_sync.py (T7): pull, status and push over a temp shared repo and a temp project. One spawned case: the CLI."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import harness_sync as hs  # noqa: E402

GIT = ["-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]
FLOOR_HOOK = ".claude/hooks/pre_bash.py"
SETTINGS_WITH_FLOOR = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
    {"type": "command", "command": "python3 .claude/hooks/pre_bash.py"}]}]}}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *GIT, *args], check=True, capture_output=True, text=True).stdout.strip()


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def commit(repo, files=None, drop=()):
    """Write `files` (paths relative to template/.claude/), delete `drop`, commit; return the new HEAD sha."""
    for rel, text in (files or {}).items():
        write(repo, "template/.claude/" + rel, text)
    for rel in drop:
        (Path(repo) / "template/.claude" / rel).unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / "shared"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, {"hooks/pre_bash.py": "floor\n", "tools/a.py": "a = 1\n", "tools/gone.py": "gone\n"})
    return repo


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return path


def registered(project):
    write(project, ".claude/settings.json", json.dumps(SETTINGS_WITH_FLOOR))


def test_pull_writes_files_and_manifest(source, project):
    head = git(source, "rev-parse", "HEAD")
    text, code = hs.pull("HEAD", str(source), str(project))
    assert code == 0 and text.startswith(f"pulled {head[:12]}")
    assert (project / ".claude/tools/a.py").read_bytes() == b"a = 1\n"
    manifest = json.loads((project / hs.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["ref"] == head
    assert manifest["floor"] == [FLOOR_HOOK]
    assert manifest["files"][".claude/tools/a.py"] == hs.sha256(b"a = 1\n")
    assert b"\r" not in (project / hs.MANIFEST).read_bytes()


def test_pull_updates_an_unedited_file_and_deletes_a_dropped_one(source, project):
    hs.pull("HEAD", str(source), str(project))
    new_head = commit(source, {"tools/a.py": "a = 2\n"}, drop=["tools/gone.py"])
    text, code = hs.pull(new_head, str(source), str(project))
    assert code == 0
    assert (project / ".claude/tools/a.py").read_bytes() == b"a = 2\n"
    assert not (project / ".claude/tools/gone.py").exists()
    assert ".claude/tools/gone.py" not in json.loads((project / hs.MANIFEST).read_text(encoding="utf-8"))["files"]


def test_pull_refuses_a_hand_edit_and_writes_nothing(source, project):
    hs.pull("HEAD", str(source), str(project))
    write(project, ".claude/tools/a.py", "hand edit\n")
    new_head = commit(source, {"tools/a.py": "a = 2\n", "tools/new.py": "new\n"})
    with pytest.raises(hs.SyncError, match=r"\.claude/tools/a\.py"):
        hs.pull(new_head, str(source), str(project))
    assert (project / ".claude/tools/a.py").read_bytes() == b"hand edit\n"
    assert not (project / ".claude/tools/new.py").exists()


def test_pull_refuses_a_drop_of_an_edited_file(source, project):
    hs.pull("HEAD", str(source), str(project))
    write(project, ".claude/tools/gone.py", "local work\n")
    new_head = commit(source, drop=["tools/gone.py"])
    with pytest.raises(hs.SyncError, match=r"gone\.py"):
        hs.pull(new_head, str(source), str(project))
    assert (project / ".claude/tools/gone.py").read_bytes() == b"local work\n"


def test_first_pull_refuses_an_unmanifested_local_file(source, project):
    write(project, ".claude/tools/a.py", "not shared\n")
    with pytest.raises(hs.SyncError, match=r"a\.py"):
        hs.pull("HEAD", str(source), str(project))
    assert not (project / hs.MANIFEST).exists()


def test_status_lists_edits_and_missing_files(source, project):
    registered(project)
    hs.pull("HEAD", str(source), str(project))
    write(project, ".claude/tools/a.py", "edited\n")
    (project / ".claude/tools/gone.py").unlink()
    text, code = hs.status(str(project))
    assert "edited  .claude/tools/a.py" in text
    assert "missing .claude/tools/gone.py" in text
    assert code == 1


def test_status_fails_only_when_the_floor_is_unregistered(source, project):
    hs.pull("HEAD", str(source), str(project))
    text, code = hs.status(str(project))
    assert code == 1 and f"floor hook not registered" in text and FLOOR_HOOK in text
    registered(project)
    assert hs.status(str(project)) == ("clean", 0)


def test_push_dry_run_lists_only_edited_shared_files(source, project):
    registered(project)
    hs.pull("HEAD", str(source), str(project))
    write(project, ".claude/tools/a.py", "edited\n")
    text, code = hs.push(str(source), str(project), "harness-sync/test", dry_run=True)
    assert code == 0
    assert text.splitlines()[1:] == ["template/.claude/tools/a.py"]
    assert "harness-sync/test" in text.splitlines()[0]
    assert "harness-sync/test" not in git(source, "branch", "--list")


def test_cli_pull_then_status_exit_codes(source, project):
    tool = str(TOOLS / "harness_sync.py")
    pulled = subprocess.run([sys.executable, tool, "pull", "HEAD", "--source", str(source), "--project", str(project)],
                            capture_output=True, text=True)
    assert pulled.returncode == 0, pulled.stderr
    assert (project / ".claude/tools/a.py").is_file()
    status = subprocess.run([sys.executable, tool, "status", "--project", str(project)], capture_output=True, text=True)
    assert status.returncode == 1 and "floor hook not registered" in status.stdout
