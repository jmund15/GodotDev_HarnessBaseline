"""Path spelling is lexical; only native lookup depends on the host OS."""
import importlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "hooks"))
import _win_path as win


class WinPathTests(unittest.TestCase):
    def test_drive_spelling(self):
        cases = (
            ("/c/x", "C:/x"), ("/C/x", "C:/x"), ("/z/", "Z:/"),
            ("/c", "C:/"), ("/c/x/", "C:/x/"), ("/c//x", "C://x"),
            ("/c/a b/../x", "C:/a b/../x"),
            ("c:/x", "c:/x"), ("C:\\x", "C:\\x"),
            ("//server/share/", "//server/share/"),
            ("\\\\server\\share", "\\\\server\\share"),
            ("/mnt/c/x", "/mnt/c/x"), ("/dev/null", "/dev/null"),
            ("/cc/x", "/cc/x"), ("/é/x", "/é/x"), ("/c\n", "/c\n"),
            ("relative/x", "relative/x"), ("../x", "../x"),
            ("c:relative", "c:relative"), ("word", "word"),
            ("", ""), (None, None), ("/", "/"),
            ('"/c/x"', '"/c/x"'),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(win.drive_path(value), expected)
                with patch.object(win, "os", SimpleNamespace(name="nt")):
                    self.assertEqual(win.native_path(value), expected)
                with patch.object(win, "os", SimpleNamespace(name="posix")):
                    self.assertEqual(win.native_path(value), value)

    def test_callers_apply_converter(self):
        sys.path.insert(0, str(REPO / ".claude" / "tools"))
        cases = (
            ("foreground_wait_guard", "drive_path", lambda m: m._resolved("/c/arm", "", "")),
            ("kill_guard", "native_path", lambda m: m._local_path("/c/arm", "")),
            ("model_registry", "drive_path", lambda m: m.expand_login_dir("/c/arm")),
            ("reap", "native_path", lambda m: m._resolve("/c/arm")),
            ("reap", "drive_path", lambda m: m._same_path("/c/arm", "C:/arm")),
        )
        for name, converter, call in cases:
            with self.subTest(caller=name, converter=converter):
                module = importlib.import_module(name)
                with patch.object(module, converter, wraps=getattr(module, converter)) as shared:
                    call(module)
                    shared.assert_any_call("/c/arm")

    def test_callers_share_converter(self):
        sys.path.insert(0, str(REPO / ".claude" / "tools"))
        for name in ("foreground_wait_guard", "kill_guard", "model_registry", "reap"):
            with self.subTest(caller=name):
                module = importlib.import_module(name)
                self.assertTrue(any(value is win.drive_path or value is win.native_path
                                    for value in vars(module).values()))


class IdentityTests(unittest.TestCase):
    """A path's checkout and its path inside it: one file has one identity whatever its spelling."""

    root = "C:/Repo" if os.name == "nt" else "/repo"
    alias = "/c/Repo" if os.name == "nt" else "/repo/../repo"
    other = "C:/Other" if os.name == "nt" else "/other"

    def test_one_file_has_one_identity_in_every_spelling(self):
        self.assertTrue(win.same(win.identity(self.root + "/Plans/X.MD", self.root),
                                 win.identity(self.alias + "/Plans/X.md", self.alias)))

    def test_a_relative_path_names_the_root_checkout(self):
        self.assertTrue(win.same(win.identity("plans/x.md", self.root),
                                 win.identity(self.alias + "/plans/x.md", self.alias)))

    def test_another_checkouts_file_is_not_this_one(self):
        self.assertFalse(win.same(win.identity("plans/x.md", self.root),
                                  win.identity(self.other + "/plans/x.md", self.root)))

    def test_the_same_relative_path_in_two_checkouts_differs(self):
        self.assertFalse(win.same(win.identity(self.root + "/plans/x.md", self.root),
                                  win.identity(self.other + "/plans/x.md", self.other)))

    def test_a_path_only_matches_by_suffix_is_not_the_same_file(self):
        self.assertFalse(win.same(win.identity("x.md", self.root),
                                  win.identity(self.alias + "/plans/x.md", self.alias)))


if __name__ == "__main__":
    unittest.main()
