"""Launcher renames wait out a reader holding the target (Windows WinError 5), so a record still publishes."""
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
import _hook_state  # noqa: E402

LAUNCHERS = sorted((ROOT / "scripts").rglob("*.sh"))


class LauncherRenames(unittest.TestCase):
    def test_no_bare_os_replace_in_launcher_python(self):
        bare = [f"{p.relative_to(ROOT).as_posix()}:{n}" for p in LAUNCHERS
                for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                if re.search(r"\bos\.replace\(", line)]
        self.assertEqual(bare, [], "rename through _hook_state.replace_path, which waits out a reader")

    @unittest.skipUnless(os.name == "nt", "only Windows refuses a rename onto an open file")
    def test_replace_path_waits_out_a_reader(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMP")) as tmp:
            target, source = Path(tmp, "record.json"), Path(tmp, "new.json")
            target.write_text("old")
            source.write_text("new")
            held = open(target, encoding="utf-8")
            threading.Thread(target=lambda: (time.sleep(0.4), held.close()), daemon=True).start()
            with self.assertRaises(PermissionError):
                os.replace(source, target)
            _hook_state.replace_path(source, target, wait_s=5)
            self.assertEqual(target.read_text(), "new")

    @unittest.skipUnless(os.name == "nt", "only Windows refuses a rename onto an open file")
    def test_replace_path_raises_after_its_wait(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMP")) as tmp:
            target, source = Path(tmp, "record.json"), Path(tmp, "new.json")
            target.write_text("old")
            source.write_text("new")
            with open(target, encoding="utf-8"):
                with self.assertRaises(PermissionError):
                    _hook_state.replace_path(source, target, wait_s=0.3)


if __name__ == "__main__":
    unittest.main()
