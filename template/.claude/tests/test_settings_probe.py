"""Cases for tests/_settings_probe.py in a template tree: no composed settings.json, so the probe
composes the base, every adopted layer overlay and the project file into a temp document."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import _settings_probe

CLAUDE = Path(__file__).resolve().parents[1]
PLANTED = {layer: f"python3 .claude/hooks/planted_{layer}_overlay_probe.py" for layer in ("coding", "godot")}


def overlay(layer):
    """A planted layer overlay adding one hook row: a pure consumer ships no coding or godot overlay to copy,
    and a project file disabling a real overlay row would refuse to compose over it."""
    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": PLANTED[layer]}]}]}}


def template_tree(layers):
    tmp = Path(tempfile.mkdtemp(prefix="settings_probe_tree_"))
    shutil.copy(CLAUDE / "settings.base.json", tmp / "settings.base.json")
    (tmp / "settings.project.json").write_text("{}", encoding="utf-8", newline="\n")
    for layer in layers:
        (tmp / f"settings.{layer}.json").write_text(json.dumps(overlay(layer)), encoding="utf-8", newline="\n")
    (tmp / "tools").mkdir()
    shutil.copy(CLAUDE / "tools" / "baseline_compose.py", tmp / "tools" / "baseline_compose.py")
    shutil.copytree(CLAUDE / "hooks", tmp / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
    for layer in PLANTED:
        (tmp / "hooks" / f"planted_{layer}_overlay_probe.py").write_text("", encoding="utf-8")
    return tmp


def commands(document):
    return {hook["command"] for groups in document.get("hooks", {}).values()
            for group in groups for hook in group.get("hooks", [])}


def godot_only_commands():
    return {PLANTED["godot"]} - commands(json.loads((CLAUDE / "settings.base.json").read_text(encoding="utf-8")))


class TemplateTreeFallback(unittest.TestCase):
    def test_composed_document_carries_every_adopted_layer(self):
        tree = template_tree(["coding", "godot"])
        self.assertFalse((tree / "settings.json").exists())
        path = _settings_probe.settings_path(tree)
        self.assertTrue(os.path.isfile(path))
        self.assertNotEqual(os.path.normpath(path), os.path.normpath(tree / "settings.json"))
        composed = commands(json.loads(Path(path).read_text(encoding="utf-8")))
        self.assertTrue(godot_only_commands(), "godot overlay adds no rows; the case proves nothing")
        self.assertTrue(godot_only_commands() <= composed,
                        "godot overlay rows missing from the composed document")

    def test_unadopted_layer_contributes_nothing(self):
        tree = template_tree(["coding"])
        composed = commands(_settings_probe.settings(tree))
        self.assertTrue(godot_only_commands(), "godot overlay adds no rows; the case proves nothing")
        self.assertFalse(godot_only_commands() & composed,
                         "godot overlay rows reached a tree that did not adopt the godot layer")


if __name__ == "__main__":
    unittest.main()
