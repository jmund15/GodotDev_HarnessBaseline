#!/usr/bin/env python3
"""The paths and commands the edited `/codify` and `/autolearn` docs cite resolve in this checkout.

The subject is the live checkout's tree (its tracked names and ignored folders), so this proof reads the live
git and reruns with the repository; `test_verify_doc_citations.py` holds the tool's own cases on a planted repo.
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, ".claude", "tools", "verify_doc_citations.py")


class TestPremiseSelfCheckLive(unittest.TestCase):
    def test_edited_command_paths_and_commands_resolve(self):
        docs = [os.path.join(ROOT, ".claude", "commands", name + ".md")
                for name in ("codify", "autolearn")]
        p = subprocess.run([sys.executable, TOOL, *docs, "--repo", ROOT, "--kinds", "path,command",
                            "--json", "--only", "missing"], capture_output=True, text=True)
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["files_checked"], 2)
        # A fresh or sparse checkout lacks runtime state the harness writes (logs, temp output) and the files
        # outside its cone; a cite is broken only when it is neither and git tracks no file by that path.
        runtime = ("logs/", ".claude/logs/", "/tmp/")
        # Each consumer owns its own outcomes ledger and baseline lock; the publish template never ships them.
        runtime += (".claude/autolearn_outcomes.jsonl", "baseline.lock.json")
        tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.split("\n")
        # A directory cite outside `.claude/` names a project folder (a game's `Tests/`); the published template
        # does not ship project folders, so only the harness tree must resolve.
        project_dir = lambda text: text.endswith("/") and not text.startswith(".claude/")  # noqa: E731
        broken = [c["text"] for f in data["files"] for c in f["citations"]
                  if c["status"] == "missing" and not c["text"].startswith(runtime) and not project_dir(c["text"])
                  and not any(t == c["text"] or t.endswith("/" + c["text"]) for t in tracked)]
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
