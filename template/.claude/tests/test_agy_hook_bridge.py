"""Proof for hooks/agy_hook_bridge.py: a destructive agy shell call is denied by the loss floor, and a floor that
cannot run denies. Decisions are in process; one spawned case runs the entry point as agy does."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
BRIDGE = HOOKS / "agy_hook_bridge.py"


def load():
    spec = importlib.util.spec_from_file_location("agy_hook_bridge_under_test", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shell(command, name="run_command"):
    return {"conversationId": "c1", "workspacePaths": [str(Path(__file__).resolve().parents[2])],
            "toolCall": {"name": name, "args": {"CommandLine": command, "Cwd": str(Path(__file__).resolve().parents[2])}}}


class AgyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = load()

    def test_a_destructive_shell_call_is_denied_by_the_floor(self):
        for command in ("git reset --hard", "rm -rf /", "git push --force origin main"):
            with self.subTest(command=command):
                self.assertEqual("deny", self.bridge.pre_tool_use(shell(command))["decision"])

    def test_an_ordinary_shell_call_is_allowed(self):
        self.assertEqual("allow", self.bridge.pre_tool_use(shell("echo hi"))["decision"])

    def test_a_tool_the_floor_does_not_judge_is_allowed_without_running_it(self):
        calls = []
        self.bridge._floor = lambda claude: calls.append(claude)
        self.assertEqual("allow", self.bridge.pre_tool_use(shell("ignored", name="view_file"))["decision"])
        self.assertEqual([], calls)

    def test_a_floor_that_cannot_start_denies_the_call(self):
        original = self.bridge.FLOOR
        self.bridge.FLOOR = str(Path(tempfile.mkdtemp(prefix="agy_bridge_")) / "missing_pre_bash.py")
        try:
            result = self.bridge.pre_tool_use(shell("echo hi"))
        finally:
            self.bridge.FLOOR = original
        self.assertEqual("deny", result["decision"])

    def test_the_entry_point_denies_a_destructive_call_and_answers_other_events_empty(self):
        proc = subprocess.run([sys.executable, str(BRIDGE), "PreToolUse"], input=json.dumps(shell("git reset --hard")),
                              text=True, capture_output=True, timeout=120)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("deny", json.loads(proc.stdout)["decision"])
        proc = subprocess.run([sys.executable, str(BRIDGE), "SessionStart"], input="{}", text=True,
                              capture_output=True, timeout=120)
        self.assertEqual(({}), json.loads(proc.stdout))

    def test_a_bridge_copied_without_the_floor_denies_through_the_entry_point(self):
        copy = Path(tempfile.mkdtemp(prefix="agy_bridge_copy_")) / "agy_hook_bridge.py"
        shutil.copy(BRIDGE, copy)
        proc = subprocess.run([sys.executable, str(copy), "PreToolUse"], input=json.dumps(shell("echo hi")),
                              text=True, capture_output=True, timeout=120)
        self.assertEqual("deny", json.loads(proc.stdout)["decision"], proc.stderr)


if __name__ == "__main__":
    unittest.main()
