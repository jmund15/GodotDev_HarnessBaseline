"""The snapshot owner: one typed answer per path, and the three absences never collapse into each other."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/tools"))
import review_snapshot as rs  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")


def _write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(root):
    """A repository with one committed file, one committed deletion and one untracked file."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.email", "a@b")
    _git(root, "config", "user.name", "a")
    _git(root, "config", "core.autocrlf", "false")
    _write(root, "kept.txt", "kept\n")
    _write(root, "gone.txt", "gone\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "one")
    _git(root, "rm", "-q", "gone.txt")
    _git(root, "commit", "-qm", "two")


class SnapshotSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs_")
        self.root = Path(self.tmp) / "repo"
        build(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_commit_index_and_disk_agree_on_a_present_file(self):
        commit = rs.open(self.root, rs.Commit("HEAD"))
        disk = rs.open(self.root, rs.Disk())
        self.assertIsInstance(commit.hash("kept.txt"), rs.Blob)
        self.assertEqual(commit.hash("kept.txt").sha, disk.hash("kept.txt").sha)
        _write(self.root, "kept.txt", "kept\nchanged\n")
        _git(self.root, "add", "kept.txt")
        staged = rs.open(self.root, rs.Index()).hash("kept.txt")
        self.assertIsInstance(staged, rs.Blob)
        self.assertNotEqual(staged.sha, commit.hash("kept.txt").sha)
        self.assertEqual(staged.sha, rs.open(self.root, rs.Disk()).hash("kept.txt").sha)
        self.assertEqual(commit.hash("kept.txt").sha,
                         _git(self.root, "rev-parse", "HEAD:kept.txt").stdout.strip())

    def test_a_recorded_row_reads_its_own_map(self):
        sha = rs.open(self.root, rs.Commit("HEAD")).hash("kept.txt").sha
        snap = rs.open(self.root, rs.Recorded({"files": {"kept.txt": sha, "gone.txt": "deleted:" + "f" * 40}}))
        self.assertEqual(snap.hash("kept.txt"), rs.Blob(sha))
        self.assertEqual(snap.hash("gone.txt"), rs.Deleted("deleted:" + "f" * 40))
        self.assertEqual(snap.hash("other.txt"), rs.MISSING)

    def test_missing_and_a_committed_deletion_stay_distinct(self):
        snap = rs.open(self.root, rs.Commit("HEAD"))
        self.assertEqual(snap.hash("never.txt"), rs.MISSING)
        gone = snap.hash("gone.txt")
        self.assertIsInstance(gone, rs.Deleted)
        self.assertTrue(gone.tombstone.startswith("deleted:"))
        self.assertEqual(rs.open(self.root, rs.Disk()).hash("gone.txt"), rs.MISSING)

    def test_unreadable_history_is_not_a_deletion(self):
        sha = rs.open(self.root, rs.Commit("HEAD")).hash("kept.txt").sha
        object_path = self.root / ".git/objects" / sha[:2] / sha[2:]
        object_path.chmod(0o700)
        object_path.unlink()
        snap = rs.open(self.root, rs.Commit("HEAD"))
        self.assertIsInstance(snap.hash("kept.txt"), rs.Unreadable,
                              "a removed object is not absent: it is unreadable")
        self.assertIsInstance(snap.deleted("kept.txt"), rs.Unreadable,
                              "unreadable history must never resemble a deletion")
        self.assertEqual(snap.hash("never.txt"), rs.MISSING)

    def test_linked_worktree_path_reads_its_own_checkout(self):
        _git(self.root, "worktree", "add", "-q", ".claude/worktrees/wt", "-b", "wt")
        wt = self.root / ".claude/worktrees/wt"
        _write(wt, "branch.txt", "branch\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", "branch")
        sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
        snap = rs.open(self.root, rs.Commit(sha))
        self.assertIsInstance(snap.hash(".claude/worktrees/wt/branch.txt"), rs.Blob)

    def test_a_foreign_checkout_at_the_worktree_alias_is_not_borrowed(self):
        foreign = self.root / ".claude/worktrees/other"
        foreign.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(foreign)], check=True)
        _write(foreign, "x.txt", "foreign\n")
        _git(foreign, "add", "-A")
        _git(foreign, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "f")
        result = rs.open(self.root, rs.Commit("HEAD")).hash(".claude/worktrees/other/x.txt")
        self.assertNotIsInstance(result, rs.Blob)

    def test_nested_submodule_pin_shift_lists_the_files_between_pins(self):
        inner = Path(self.tmp) / "inner"
        subprocess.run(["git", "init", "-q", str(inner)], check=True)
        _git(inner, "config", "user.email", "a@b")
        _git(inner, "config", "user.name", "a")
        _write(inner, "g.cs", "one\n")
        _git(inner, "add", "-A")
        _git(inner, "commit", "-qm", "one")
        old = _git(inner, "rev-parse", "HEAD").stdout.strip()
        subprocess.run(["git", "-C", str(self.root), "-c", "protocol.file.allow=always",
                        "submodule", "add", "-q", str(inner), "lib/core"], check=True)
        _git(self.root, "commit", "-qm", "add sub")
        _write(inner, "g.cs", "two\n")
        _git(inner, "add", "-A")
        _git(inner, "commit", "-qm", "two")
        new = _git(inner, "rev-parse", "HEAD").stdout.strip()
        _git(self.root / "lib/core", "fetch", "-q")
        _git(self.root / "lib/core", "checkout", "-q", new)
        _git(self.root, "add", "lib/core")
        _git(self.root, "commit", "-qm", "bump")
        self.assertEqual(rs.gitlink_files(self.root, "lib/core", old, new), {"lib/core/g.cs"})
        self.assertIsInstance(rs.open(self.root, rs.Commit("HEAD")).hash("lib/core/g.cs"), rs.Blob)

    def test_range_commits_names_files_and_a_clean_merge_resolves_none(self):
        main_branch = _git(self.root, "symbolic-ref", "--short", "HEAD").stdout.strip()
        _git(self.root, "checkout", "-q", "-b", "side")
        _write(self.root, "side.txt", "side\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "side")
        _git(self.root, "checkout", "-q", main_branch)
        _write(self.root, "main.txt", "main\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "main")
        base = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        _git(self.root, "merge", "-q", "--no-ff", "-m", "merge", "side")
        merge = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        commits = {c["sha"]: c["files"] for c in rs.range_commits(self.root, base, merge)}
        self.assertEqual(commits[merge], [], "a clean merge authors no resolution")
        self.assertEqual(sorted(f for files in commits.values() for f in files), ["side.txt"])
        _write(self.root, "main.txt", "kept\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "plain")
        plain = rs.range_commits(self.root, merge, "HEAD")
        self.assertEqual(plain[0]["files"], ["main.txt"])


if __name__ == "__main__":
    sys.exit(unittest.main())
