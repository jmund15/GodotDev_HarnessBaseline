#!/usr/bin/env python3
"""Composition proof: the UserPromptSubmit dispatcher is registered once per profile, and no CHAIN
member is also registered standalone.

Composes this repo's settings inputs with `baseline_compose.compose_settings` for three profiles. Each
profile's hooks dir holds the real dispatcher plus empty stubs for the members that profile ships.
The godot overlay is a stand-in: this repo ships no `settings.godot.json`, and the stand-in registers
no dispatcher. An install without the coding layer has no `settings.coding.json`, so its two coding
profiles skip; an install without `settings.project.json` composes with an empty project overlay.
Writes only under its temp root.
"""
import ast
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
TOOLS = CLAUDE / "tools"
DISPATCH_SRC = CLAUDE / "hooks" / "prompt_dispatch.py"
DISPATCH_NAME = "prompt_dispatch.py"

PURE_MEMBERS = ("prompt_memory_loader", "critical_analysis_reminder", "prompt_git_state_delta",
                "shell_census", "sidecar_watch_rearm", "budget_posture", "overnight_ask_guard")
CODING_MEMBERS = ("tool_routing_cumulative_reset", "tool_routing_prompt_synthesis")
GODOT_MEMBERS = ("activity_registry",)
GODOT_STANDIN = {"hooks": {"SessionStart": [{"hooks": [{
    "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/tres_script_strip_guard.py\"",
    "timeout": 30, "type": "command"}]}]}}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _overlay(name: str):
    path = CLAUDE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _chain_names():
    tree = ast.parse(DISPATCH_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CHAIN" for t in node.targets):
            return [name for name, _budget in ast.literal_eval(node.value)]
    raise AssertionError("CHAIN literal not found in prompt_dispatch.py")


class PromptDispatchComposition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bc = _load(TOOLS / "baseline_compose.py", "baseline_compose")
        cls.base = json.loads((CLAUDE / "settings.base.json").read_text(encoding="utf-8"))
        cls.coding = _overlay("settings.coding.json")
        cls.project = _overlay("settings.project.json") or {}
        cls.chain = _chain_names()

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="prompt-dispatch-compose-"))
        self.addCleanup(shutil.rmtree, self.temp, True)

    def hooks_dir(self, members):
        hooks = self.temp / ".claude" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy(DISPATCH_SRC, hooks / DISPATCH_NAME)
        for name in members:
            (hooks / f"{name}.py").write_text("", encoding="utf-8")
        return hooks

    def compose(self, members, overlays):
        hooks = self.hooks_dir(members)
        return self.compose_with(hooks, overlays)

    def compose_with(self, hooks, overlays):
        return self.bc.compose_settings(self.base, self.project, hooks, overlays=overlays)

    def user_prompt_commands(self, merged):
        return [h["command"] for group in merged.get("hooks", {}).get("UserPromptSubmit", [])
                for h in group.get("hooks", [])]

    def assert_dispatcher_once_and_no_standalone_member(self, merged):
        commands = self.user_prompt_commands(merged)
        dispatched = [c for c in commands if DISPATCH_NAME in c]
        self.assertEqual(len(dispatched), 1, commands)
        standalone = [c for c in commands if DISPATCH_NAME not in c
                      and Path(c.split("/")[-1].strip('"')).stem in self.chain]
        self.assertEqual(standalone, [], commands)

    def test_pure_profile(self):
        merged = self.compose(PURE_MEMBERS, overlays=())
        self.assert_dispatcher_once_and_no_standalone_member(merged)

    def require_coding(self):
        if self.coding is None:
            self.skipTest("this install ships no settings.coding.json")

    def test_pure_coding_profile(self):
        self.require_coding()
        members = PURE_MEMBERS + CODING_MEMBERS
        merged = self.compose(members, overlays=(self.coding,))
        self.assert_dispatcher_once_and_no_standalone_member(merged)

    def test_pure_coding_godot_profile(self):
        self.require_coding()
        members = PURE_MEMBERS + CODING_MEMBERS + GODOT_MEMBERS
        merged = self.compose(members, overlays=(self.coding, GODOT_STANDIN))
        self.assert_dispatcher_once_and_no_standalone_member(merged)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
