"""Real hook-payload proofs for runtime_mcp_worktree_guard: the MCP bridge only targets a linked worktree."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks/runtime_mcp_worktree_guard.py"


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


class RuntimeMcpWorktreeGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.main = root / "main"
        cls.main.mkdir()
        _git(cls.main, "init", "-q")
        _git(cls.main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "seed")
        cls.linked = root / "linked"
        _git(cls.main, "worktree", "add", "-q", "--detach", str(cls.linked))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def decide(self, tool, tool_input, cwd=None):
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or self.main)}
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                                capture_output=True, timeout=60)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        out = data.get("hookSpecificOutput", {})
        return out.get("permissionDecision", "allow"), out.get("permissionDecisionReason", "")

    def test_main_checkout_is_denied_with_the_fix(self):
        decision, reason = self.decide("mcp__godot__run_project", {"projectPath": str(self.main)})
        self.assertEqual("deny", decision)
        self.assertIn("linked worktree", reason)
        self.assertIn(".claude/worktrees/", reason)
        self.assertIn("rules/godot_files.md", reason)
        self.assertNotIn("whole session", reason)

    def test_attach_on_main_checkout_is_denied(self):
        decision, _ = self.decide("mcp__godot__attach_project", {"projectPath": str(self.main)})
        self.assertEqual("deny", decision)

    def test_linked_worktree_is_allowed(self):
        decision, _ = self.decide("mcp__godot__run_project", {"projectPath": str(self.linked)})
        self.assertEqual("allow", decision)

    def test_relative_path_resolves_against_payload_cwd(self):
        decision, _ = self.decide("mcp__godot__run_project", {"projectPath": "."}, cwd=self.main)
        self.assertEqual("deny", decision)
        decision, _ = self.decide("mcp__godot__run_project", {"projectPath": "."}, cwd=self.linked)
        self.assertEqual("allow", decision)

    def test_missing_or_unreadable_path_is_denied(self):
        decision, _ = self.decide("mcp__godot__run_project", {"projectPath": str(self.main / "nope")})
        self.assertEqual("deny", decision)
        decision, _ = self.decide("mcp__godot__run_project", {})
        self.assertEqual("deny", decision)

    def test_directory_outside_git_is_denied(self):
        plain = Path(self.temp.name) / "plain"
        plain.mkdir(exist_ok=True)
        decision, reason = self.decide("mcp__godot__run_project", {"projectPath": str(plain)})
        self.assertEqual("deny", decision)
        self.assertIn("not inside a git worktree", reason)

    def test_other_tools_pass(self):
        decision, _ = self.decide("mcp__godot__check_project", {"projectPath": str(self.main)})
        self.assertEqual("allow", decision)


if __name__ == "__main__":
    unittest.main()
