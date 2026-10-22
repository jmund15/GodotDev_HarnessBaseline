#!/usr/bin/env python3
"""Contract proof for .claude/scripts/engine_userdata.py.

Refuses to resolve user data when the platform's data root is unset, and refuses an unsupported platform.
Resolves a planted local-data root to the agent-owned folder, through the function and the `--shell` CLI.
Reads and writes nothing outside its temp root.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / ".claude" / "scripts" / "engine_userdata.py"
sys.path.insert(0, str(TOOL.parent))
import engine_userdata  # noqa: E402


class EngineUserdataContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="engine-userdata-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.repo = self.temp / "checkout"
        self.repo.mkdir()
        (self.repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        self.local = (self.temp / "local").as_posix()

    def test_unset_local_data_root_is_refused(self):
        with self.assertRaisesRegex(ValueError, "LOCALAPPDATA is required"):
            engine_userdata.isolation(self.repo, env={}, platform="win32")

    def test_unsupported_platform_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Unsupported engine data isolation platform"):
            engine_userdata.isolation(self.repo, env={"LOCALAPPDATA": self.local}, platform="darwin")

    def test_planted_local_root_resolves_to_agent_owned_folder(self):
        values = engine_userdata.isolation(self.repo, env={"LOCALAPPDATA": self.local}, platform="win32")
        self.assertTrue(values["APPDATA"].startswith(self.local + "/"), values["APPDATA"])
        self.assertTrue(values["APPDATA"].endswith("/agent-appdata/checkout"), values["APPDATA"])
        self.assertRegex(values["USERDIR"], "^" + re.escape(values["APPDATA"]) + r"/Godot/app_userdata/[^/]+$")

    def test_shell_cli_prints_exports(self):
        env = dict(os.environ, LOCALAPPDATA=self.local)
        env.pop("ENGINE_OWNER_USERDATA", None)
        done = subprocess.run([sys.executable, str(TOOL), "--repo", str(self.repo), "--shell"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                              timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("export USERDIR=", done.stdout)
        self.assertRegex(done.stdout, r"app_userdata/[^/\s']+")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
