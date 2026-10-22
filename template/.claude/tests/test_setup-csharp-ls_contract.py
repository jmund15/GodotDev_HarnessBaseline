#!/usr/bin/env python3
"""Contract proof for .claude/tools/setup-csharp-ls.sh: the `--dry-run` argument and missing-binary paths.

Runs with HOME pointed at its temp root, so no global tool, plugin file or adapter copy is touched and no
dotnet call is made. Refuses when no csharp-ls binary is installed; a dry run with one planted reports the
pinned install and writes nothing. Requires a POSIX `bash` on PATH (Git Bash on Windows).
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
VERSION = "9.9.9-contract"


class SetupCsharpLsContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="setup-csharp-ls-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        tools = self.temp / ".claude" / "tools"
        tools.mkdir(parents=True)
        for name in ("setup-csharp-ls.sh", "csharp-ls-adapter.js"):
            shutil.copy(ROOT / ".claude" / "tools" / name, tools / name)
        config = self.temp / ".config"
        config.mkdir()
        (config / "dotnet-tools.json").write_text(
            json.dumps({"tools": {"csharp-ls": {"version": VERSION}}}), encoding="utf-8")
        self.home = self.temp / "home"
        self.tools_dir = self.home / ".dotnet" / "tools"
        self.tools_dir.mkdir(parents=True)
        self.script = tools / "setup-csharp-ls.sh"

    def run_script(self):
        env = dict(os.environ, HOME=self.home.as_posix())
        return subprocess.run([BASH, self.script.as_posix(), "--dry-run"], cwd=self.temp, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def test_missing_csharp_ls_binary_is_refused(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("neither csharp-ls.exe nor csharp-ls-original.exe found", done.stdout)

    def test_dry_run_with_planted_binary_names_pinned_install_and_writes_nothing(self):
        (self.tools_dir / "csharp-ls-original.exe").write_bytes(b"")
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Would run: dotnet tool update --global csharp-ls --version %s" % VERSION, done.stdout)
        self.assertIn("[DRY RUN] No files will be modified.", done.stdout)
        self.assertEqual(sorted(p.name for p in self.tools_dir.iterdir()), ["csharp-ls-original.exe"])
        self.assertFalse((self.home / ".claude").exists())


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
