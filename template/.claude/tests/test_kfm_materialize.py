"""KFM read-only seats get one body per file without reading the full catalog."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REAL_TOOLS = Path(__file__).resolve().parents[1] / "tools"

# A project's catalog is its own (an empty seed in a fresh project). The proof plants a
# tree holding the real tool and a two-entry catalog, so it reads no entry of this project.
PLANTED = tempfile.TemporaryDirectory(prefix="kfm-proof-")
ROOT = Path(PLANTED.name)
SCRIPT = ROOT / ".claude/tools/kfm.py"
SCRIPT.parent.mkdir(parents=True)
for _name in ("kfm.py", "cli_usage.py"):
    shutil.copyfile(REAL_TOOLS / _name, SCRIPT.parent / _name)
(ROOT / ".claude/reference").mkdir()
(ROOT / ".claude/reference/known_failure_modes_entries.md").write_text(
    "# Known Failure Modes\n\n## Runtime failures\n\n"
    "### 1. First-planted\n**Incident**: planted.\n**Catches you when**: the first trigger fires.\n\n"
    "### 2. Second-planted\n**Incident**: planted.\n**Catches you when**: the second trigger fires.\n",
    encoding="utf-8", newline="\n")
SPEC = importlib.util.spec_from_file_location("kfm", SCRIPT)
kfm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kfm)


class KfmMaterializeTests(unittest.TestCase):
    def setUp(self):
        # The gitignored scratch folder is absent in a close's private checkout.
        (ROOT / ".claude/scratch").mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".claude/scratch", prefix="kfm-test-")
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "entries"

    def call(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=30)

    def test_materialized_index_selects_one_body(self):
        proc = self.call("materialize", "--out", str(self.output))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        index = self.output / "index.md"
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        entries = kfm.parse()
        self.assertEqual(len(manifest["entries"]), len(entries))
        body = self.output / manifest["entries"][0]["file"]
        self.assertIn(f"### {entries[0].id}. {entries[0].name}", body.read_text(encoding="utf-8"))
        self.assertNotIn(f"### {entries[1].id}. {entries[1].name}", body.read_text(encoding="utf-8"))
        text = index.read_text(encoding="utf-8")
        self.assertIn(manifest["entries"][0]["path"], text)
        self.assertIn(entries[0].trigger, text)
        self.assertIn(manifest["catalogSha256"], text)
        self.assertEqual(self.call("check", "--dir", str(self.output)).returncode, 0)
        self.assertEqual(self.call("materialize", "--out", str(self.output)).returncode, 0)

    def test_tampered_body_or_catalog_revision_is_not_current(self):
        self.assertEqual(self.call("materialize", "--out", str(self.output)).returncode, 0)
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        body = self.output / manifest["entries"][0]["file"]
        original = body.read_text(encoding="utf-8")
        body.write_text("tampered\n", encoding="utf-8")
        self.assertNotEqual(self.call("check", "--dir", str(self.output)).returncode, 0)
        self.assertNotEqual(self.call("materialize", "--out", str(self.output)).returncode, 0)
        body.write_text(original, encoding="utf-8", newline="\n")
        synthetic = Path(self.tmp.name) / "catalog.md"
        synthetic.write_bytes(kfm.CATALOG.read_bytes() + b"\n<!-- new entry -->\n")
        self.assertFalse(kfm.snapshot_is_current(self.output, synthetic))
        self.assertTrue(kfm.snapshot_is_current(self.output, kfm.CATALOG))

    def test_injected_extra_file_makes_snapshot_not_current(self):
        self.assertEqual(self.call("materialize", "--out", str(self.output)).returncode, 0)
        (self.output / "999.md").write_text("### 999. Planted\n", encoding="utf-8")
        self.assertFalse(kfm.snapshot_is_current(self.output, kfm.CATALOG))
        self.assertNotEqual(self.call("check", "--dir", str(self.output)).returncode, 0)
        self.assertNotEqual(self.call("materialize", "--out", str(self.output)).returncode, 0)

    def test_existing_unrelated_directory_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "notes.md"
        sentinel.write_text("owned by someone else\n", encoding="utf-8")
        proc = self.call("materialize", "--out", str(self.output))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "owned by someone else\n")

    def test_the_output_directory_appears_only_once_complete(self):
        seen = []
        real_body = kfm.Entry.body

        def watching(entry):
            seen.append(self.output.exists())
            return real_body(entry)
        kfm.Entry.body = watching
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                kfm.cmd_materialize(kfm.parse(), str(self.output))
        finally:
            kfm.Entry.body = real_body
        self.assertTrue(seen and not any(seen), "the final directory existed while entries were still being written")
        self.assertTrue(kfm.snapshot_is_current(self.output, kfm.CATALOG))
        self.assertEqual([p.name for p in self.output.parent.iterdir()], ["entries"])

    def test_a_peer_that_published_first_is_accepted_not_reported_stale(self):
        real_replace = kfm.os.replace

        def peer_wins(source, target):
            self.assertEqual(self.call("materialize", "--out", str(self.output)).returncode, 0)
            return real_replace(source, target)
        kfm.os.replace = peer_wins
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                kfm.cmd_materialize(kfm.parse(), str(self.output))
        finally:
            kfm.os.replace = real_replace
        self.assertTrue(kfm.snapshot_is_current(self.output, kfm.CATALOG))
        self.assertEqual([p.name for p in self.output.parent.iterdir()], ["entries"])


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        PLANTED.cleanup()
