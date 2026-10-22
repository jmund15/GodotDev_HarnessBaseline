#!/usr/bin/env python3
"""Contract proof for .claude/scripts/godot_bin.sh.

Refuses a missing engine path. Runs a planted engine stub (a copy of node, never Godot) and asserts it
receives the forwarded arguments and the isolated user data folder. Writes only under its temp root.
Requires a POSIX `bash` on PATH (Git Bash on Windows) and `node` on PATH.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))
from sidecar_launch import git_bash  # noqa: E402

BASH = git_bash()
NODE = shutil.which("node")
STUB = "const fs = require('fs');\nfs.writeFileSync(process.env.STUB_OUT, JSON.stringify({ argv: process.argv.slice(2), userdir: process.env.USERDIR }));\n"


class GodotBinContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="godot-bin-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        scripts = self.temp / ".claude" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("godot_bin.sh", "engine_userdata.py"):
            shutil.copy(ROOT / ".claude" / "scripts" / name, scripts / name)
        (self.temp / "stub.js").write_text(STUB, encoding="utf-8")
        self.engine = self.temp / "fake_engine.exe"
        try:
            os.link(NODE, self.engine)
        except OSError:
            shutil.copy(NODE, self.engine)
        self.out = self.temp / "stub_out.json"
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "LOCALAPPDATA": (self.temp / "local").as_posix(),
            "STUB_OUT": self.out.as_posix(),
            "GODOT_BIN": self.engine.as_posix(),
        }

    def run_script(self, *args, env=None):
        return subprocess.run([BASH, (self.temp / ".claude" / "scripts" / "godot_bin.sh").as_posix(), *args],
                              cwd=self.temp, env=env or self.env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def test_missing_engine_is_refused(self):
        env = dict(self.env, GODOT_BIN=(self.temp / "missing.exe").as_posix())
        done = self.run_script("stub.js", "--headless", env=env)
        self.assertNotEqual(done.returncode, 0, done.stderr)
        self.assertIn("GODOT_BIN is unset or missing", done.stderr)
        self.assertFalse(self.out.exists())

    def test_engine_receives_forwarded_args_and_isolated_userdata(self):
        done = self.run_script("stub.js", "--headless", "--path", "proj")
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(seen["argv"], ["--headless", "--path", "proj"])
        self.assertIn("/agent-appdata/", seen["userdir"].replace("\\", "/"))
        self.assertRegex(seen["userdir"].replace("\\", "/"), r"/[Gg]odot/app_userdata/[^/]+$")
        self.assertIn("USERDIR=", done.stderr)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
