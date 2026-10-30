"""A --ledger-blobs entry under a submodule is checked against the submodule's object store, not the superproject's."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import review_result

GIT = ["git", "-c", "protocol.file.allow=always", "-c", "user.name=t", "-c", "user.email=t@t"]


def sh(cwd, *args):
    done = subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class SubmoduleBlobMapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        lib, self.repo = tmp / "lib_origin", tmp / "app"
        for path in (lib, self.repo):
            subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
        (lib / "g.cs").write_text("one\n", encoding="utf-8")
        sh(lib, "add", "-A")
        sh(lib, "commit", "-q", "-m", "L1")
        (self.repo / "a.cs").write_text("app\n", encoding="utf-8")
        sh(self.repo, "submodule", "add", "-q", str(lib), "lib")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "S")
        self.sub_blob = sh(self.repo / "lib", "rev-parse", "HEAD:g.cs")
        self.app_blob = sh(self.repo, "rev-parse", "HEAD:a.cs")
        self.gitlink = sh(self.repo, "rev-parse", "HEAD:lib")

    def problems(self, blobs):
        path = Path(self._tmp.name) / "blobs.json"
        path.write_text(json.dumps(blobs), encoding="utf-8")
        return review_result._blob_problems(str(path), self.repo)

    def test_a_blob_only_the_submodule_holds_is_a_blob(self):
        self.assertEqual(self.problems({"a.cs": self.app_blob, "lib/g.cs": self.sub_blob}), [])

    def test_a_missing_submodule_blob_is_still_refused(self):
        problems = self.problems({"lib/g.cs": "0" * 40})
        self.assertEqual(len(problems), 1)
        self.assertIn("lib/g.cs", problems[0])

    def test_a_gitlink_commit_is_still_refused(self):
        problems = self.problems({"lib": self.gitlink})
        self.assertEqual(len(problems), 1)
        self.assertIn("gitlink", problems[0])


if __name__ == "__main__":
    unittest.main()
