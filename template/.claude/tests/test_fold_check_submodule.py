"""A rev-kind fold snapshot reads, tracks and diffs a file under a submodule through the submodule's own history."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fold_check

GIT = ["git", "-c", "protocol.file.allow=always", "-c", "user.name=t", "-c", "user.email=t@t"]


def sh(cwd, *args):
    done = subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class SubmoduleRevSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        lib, self.repo = tmp / "lib_origin", tmp / "app"
        for path in (lib, self.repo):
            subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
        (lib / "core").mkdir()
        (lib / "core" / "g.cs").write_bytes(b"one\n")
        sh(lib, "add", "-A")
        sh(lib, "commit", "-q", "-m", "L1")
        sh(self.repo, "submodule", "add", "-q", str(lib), "lib")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "S")
        self.base = sh(self.repo, "rev-parse", "HEAD")
        self.snapshot = fold_check.snapshot_rev(self.repo, self.base, ["lib/core/g.cs"])

    def subject(self):
        return fold_check.Subject(self.repo, ["lib/core/g.cs"], self.snapshot)

    def test_the_pre_fold_bytes_of_a_submodule_file_are_its_bytes_at_the_pinned_commit(self):
        (self.repo / "lib" / "core" / "g.cs").write_bytes(b"folded\n")
        self.assertEqual(self.subject().pre_bytes("lib/core/g.cs"), b"one\n")

    def test_a_submodule_file_is_tracked_at_the_snapshot(self):
        self.assertTrue(self.subject().tracked_at_snapshot("lib/core/g.cs"))

    def test_an_absent_submodule_file_is_not_tracked_at_the_snapshot(self):
        self.assertFalse(self.subject().tracked_at_snapshot("lib/core/new.cs"))

    def test_an_edit_inside_the_submodule_is_in_the_change_set(self):
        (self.repo / "lib" / "core" / "g.cs").write_bytes(b"folded\n")
        self.assertIn("lib/core/g.cs", self.subject().change_set())


if __name__ == "__main__":
    unittest.main()
