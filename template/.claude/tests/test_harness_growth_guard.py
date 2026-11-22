"""harness_growth_guard is a library now: it keeps the preimage and line helpers other hooks import and
the shared rule-unit definitions (`UNIT`, `EXCLUDED`, `DENSITY_AUDIT`) the commit-time density advisory uses.
It emits nothing on an edit; the removed-sentence measure lives at commit time (`triage.py sentences`).
"""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("harness_growth_guard", Path(__file__).resolve().parents[1] / "hooks/harness_growth_guard.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class NoEmissionTests(unittest.TestCase):
    def test_a_large_harness_markdown_edit_prints_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / ".claude/CLAUDE.md"
            target.parent.mkdir(parents=True)
            target.write_text("- " + "large " * 6000, encoding="utf-8")
            event = {"tool_name": "Write", "session_id": "nogrow01", "cwd": temp,
                     "tool_input": {"file_path": str(target)}, "tool_response": {"type": "create"}}
            with patch.object(hook.sys, "stdin", io.StringIO(json.dumps(event))), \
                    patch.dict(os.environ, {"HARNESS_HOOK_STATE_DIR": temp}), \
                    redirect_stdout(io.StringIO()) as output:
                try:
                    hook.main()
                except AttributeError:
                    pass
            self.assertEqual(output.getvalue(), "")

    def test_no_per_edit_entry_or_report_text_remains(self):
        for name in ("process", "removal_report", "RE_HOME", "HOME_SEARCH_SECONDS"):
            self.assertFalse(hasattr(hook, name), name)

    def test_the_library_surface_callers_need_stays(self):
        self.assertEqual(hook.added_lines("a\nb\n", "a\nc\nb\n"), ["c"])
        for name in ("UNIT", "EXCLUDED", "DENSITY_AUDIT"):
            self.assertTrue(hasattr(hook, name), name)


# Captured Write update from top-level session 00d8a771-c98d-41a8-814a-ff440c801325.jsonl.
LIVE_WRITE = json.loads('{"content": "from pathlib import Path\\n\\npath = Path(r\\"C:\\\\Users\\\\dev\\\\.local\\\\bin\\\\claude.exe\\")\\ndata = path.read_bytes()\\nterms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]\\nfor needle in terms:\\n    found = data.find(needle)\\n    print(f\\"=== {needle.decode()} offset={found} ===\\")\\n    if found < 0:\\n        continue\\n    raw = data[max(0, found - 180):min(len(data), found + len(needle) + 1100)]\\n    text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)\\n    text = \\" \\".join(text.split())\\n    print(text[:1300])\\n", "type": "update", "originalFile": "from pathlib import Path\\n\\npath = Path(r\\"C:\\\\Users\\\\dev\\\\.local\\\\bin\\\\claude.exe\\")\\ndata = path.read_bytes()\\nterms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]\\nfor needle in terms:\\n    print(f\\"=== {needle.decode()} ===\\")\\n    start = 0\\n    count = 0\\n    while count < 6:\\n        found = data.find(needle, start)\\n        if found < 0:\\n            break\\n        raw = data[max(0, found - 300):min(len(data), found + len(needle) + 2200)]\\n        text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)\\n        text = \\" \\".join(text.split())\\n        print(f\\"[{count + 1}] offset={found} {text[:2500]}\\")\\n        count += 1\\n        start = found + len(needle)\\n", "structuredPatch": [{"oldStart": 4, "oldLines": 16, "newStart": 4, "newLines": 11, "lines": [" data = path.read_bytes()", " terms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]", " for needle in terms:", "-    print(f\\"=== {needle.decode()} ===\\")", "-    start = 0", "-    count = 0", "-    while count < 6:", "-        found = data.find(needle, start)", "-        if found < 0:", "-            break", "-        raw = data[max(0, found - 300):min(len(data), found + len(needle) + 2200)]", "-        text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)", "-        text = \\" \\".join(text.split())", "-        print(f\\"[{count + 1}] offset={found} {text[:2500]}\\")", "-        count += 1", "-        start = found + len(needle)", "+    found = data.find(needle)", "+    print(f\\"=== {needle.decode()} offset={found} ===\\")", "+    if found < 0:", "+        continue", "+    raw = data[max(0, found - 180):min(len(data), found + len(needle) + 1100)]", "+    text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)", "+    text = \\" \\".join(text.split())", "+    print(text[:1300])"]}]}')

class LiveWritePatchTests(unittest.TestCase):
    def test_live_write_patch_reconstructs_preimage(self):
        # The producer's originalFile is an independent oracle for its structuredPatch.
        response = dict(LIVE_WRITE, originalFile=None)
        assert hook.pre_edit_text({'tool_name': 'Write', 'tool_response': response}, LIVE_WRITE['content']) == LIVE_WRITE['originalFile']
        assert hook.pre_edit_text({'tool_name': 'Write'}, LIVE_WRITE['content']) is None
        response['structuredPatch'] = [dict(response['structuredPatch'][0], newStart=999)]
        assert hook.pre_edit_text({'tool_name': 'Write', 'tool_response': response}, LIVE_WRITE['content']) is None


if __name__ == "__main__":
    unittest.main()
