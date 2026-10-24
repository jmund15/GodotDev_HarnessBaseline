#!/usr/bin/env python3
"""tools/plugin_registry.py: the one reader of a login's user-scope plugin row and the one build identity."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import plugin_registry as pr

KEY = "semantic-search@dreb"


def write(login, rows):
    path = Path(login) / "plugins" / "installed_plugins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "plugins": {KEY: rows}}), encoding="utf-8")
    return path


class UserRows(unittest.TestCase):
    def test_only_user_scope_rows_of_the_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, [{"scope": "project", "version": "1"}, {"scope": "user", "version": "2"}, "junk"])
            self.assertEqual(pr.user_rows(tmp, KEY), [{"scope": "user", "version": "2"}])

    def test_absent_registry_has_no_rows_but_a_malformed_one_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pr.user_rows(tmp, KEY), [])
            (Path(tmp) / "plugins").mkdir()
            (Path(tmp) / "plugins" / "installed_plugins.json").write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError):
                pr.user_rows(tmp, KEY)

    def test_registry_file_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, [{"scope": "user", "version": "2"}])
            self.assertEqual(len(pr.user_rows(path, KEY)), 1)

    def test_one_row_or_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(pr.user_row(tmp, KEY))
            write(tmp, [{"scope": "user", "version": "2"}])
            self.assertEqual(pr.user_row(tmp, KEY)["version"], "2")
            write(tmp, [{"scope": "user"}, {"scope": "user"}])
            self.assertIsNone(pr.user_row(tmp, KEY))


class BuildIdentity(unittest.TestCase):
    def test_revision_beats_version(self):
        self.assertEqual(pr.build_identity({"version": "2.69.0", "gitCommitSha": "abc"}), "abc")

    def test_two_builds_of_one_version_differ(self):
        a = pr.build_identity({"version": "2.69.0", "gitCommitSha": "abc"})
        b = pr.build_identity({"version": "2.69.0", "gitCommitSha": "def"})
        self.assertNotEqual(a, b)

    def test_no_revision_hashes_the_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir()
            (dist / "search.js").write_text("a", encoding="utf-8")
            row = {"version": "1", "installPath": tmp}
            first = pr.build_identity(row)
            self.assertTrue(first.startswith("dist-sha256:"))
            (dist / "search.js").write_text("b", encoding="utf-8")
            self.assertNotEqual(first, pr.build_identity(row))

    def test_nothing_to_identify_is_none(self):
        self.assertIsNone(pr.build_identity({"version": "1"}))
        self.assertIsNone(pr.build_identity(None))


if __name__ == "__main__":
    unittest.main()
