"""Re-runnable proof for hooks/video_read_hint.py: a Read of a video or GIF gets a one-line advisory.

Cases feed real PreToolUse payloads on stdin and assert on the emitted channel. The advisory is
`hookSpecificOutput.additionalContext`; the hook never denies. An exit code outside {0, 2}, a traceback or a
missing hook file is a CRASH, never an allow. The registration cases read the live settings files and run the Read dispatcher.

    python3 .claude/tests/test_video_read_hint.py
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
HOOK = CLAUDE / "hooks" / "video_read_hint.py"
EXTENSIONS = (".mp4", ".webm", ".mov", ".avi", ".mkv", ".gif")


def fire(payload, raw=None):
    """Run the hook once. Returns (exit code, parsed stdout or None)."""
    assert HOOK.exists(), f"CRASH: {HOOK} is missing"
    res = subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload),
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode in (0, 2), f"CRASH: exit {res.returncode}: {res.stderr}"
    assert "Traceback" not in res.stderr, f"CRASH: {res.stderr}"
    out = res.stdout.strip()
    return res.returncode, (json.loads(out) if out else None)


def read(path, **extra):
    return {"tool_name": "Read", "session_id": "vrh00001", "tool_input": {"file_path": path, **extra}}


def advisory(doc):
    return ((doc or {}).get("hookSpecificOutput") or {}).get("additionalContext")


class Advisory(unittest.TestCase):
    def test_mp4_read_names_the_skill_and_the_script(self):
        code, doc = fire(read("C:/clips/run.mp4"))
        self.assertEqual(code, 0)
        text = advisory(doc)
        self.assertIn("video_frames", text)
        self.assertIn(".claude/scripts/video_frames.py", text)
        self.assertIn("C:/clips/run.mp4", text)
        self.assertEqual(doc["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", doc["hookSpecificOutput"])

    def test_advisory_is_one_line(self):
        self.assertNotIn("\n", advisory(fire(read("C:/clips/run.mp4"))[1]))

    def test_every_video_extension_gets_it(self):
        for ext in EXTENSIONS:
            with self.subTest(ext=ext):
                self.assertIn("video_frames", advisory(fire(read(f"C:/clips/a{ext}"))[1]))

    def test_extension_case_and_windows_separators(self):
        self.assertIn("video_frames", advisory(fire(read("C:\\clips\\RUN.GIF"))[1]))

    def test_a_read_with_a_line_range_still_gets_it(self):
        self.assertIn("video_frames", advisory(fire(read("C:/clips/run.mov", offset=1, limit=10))[1]))


class Silent(unittest.TestCase):
    def assertSilent(self, payload):
        code, doc = fire(payload)
        self.assertEqual(code, 0)
        self.assertIsNone(advisory(doc), doc)

    def test_png_read_is_silent(self):
        self.assertSilent(read("C:/shots/frame.png"))

    def test_extension_must_be_the_last_suffix(self):
        self.assertSilent(read("C:/notes/run.mp4.md"))
        self.assertSilent(read("C:/notes/mp4"))

    def test_bash_that_only_mentions_a_clip_is_silent(self):
        self.assertSilent({"tool_name": "Bash", "tool_input": {"command": "ls clip.mp4 && cat clip.gif"}})

    def test_grep_and_glob_over_a_clip_are_silent(self):
        self.assertSilent({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": "C:/clips/run.mp4"}})
        self.assertSilent({"tool_name": "Glob", "tool_input": {"pattern": "**/*.mp4"}})

    def test_the_video_tool_itself_is_not_advised_against_itself(self):
        self.assertSilent(read("C:/repo/.claude/scripts/video_frames.py"))

    def test_malformed_payloads_allow(self):
        for raw in ("", "not json", "[]", "{}", '{"tool_name": "Read"}', '{"tool_name": "Read", "tool_input": null}',
                    '{"tool_name": "Read", "tool_input": {"file_path": 5}}'):
            with self.subTest(raw=raw):
                code, doc = fire(None, raw=raw)
                self.assertEqual(code, 0)
                self.assertIsNone(advisory(doc))


class Registration(unittest.TestCase):
    """The hint runs inside hooks/pre_read_dispatch.py, the one Read PreToolUse command (its proof holds that the
    Read surface is exactly one command), never as its own settings entry."""

    def test_not_registered_on_its_own(self):
        files = sorted(CLAUDE.glob("settings*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(file=path.name):
                hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks", {}).get("PreToolUse", [])
                own = [h["command"] for group in hooks for h in group["hooks"] if "video_read_hint.py" in h.get("command", "")]
                self.assertEqual(own, [])

    def test_the_read_dispatcher_emits_it(self):
        dispatch = CLAUDE / "hooks" / "pre_read_dispatch.py"
        res = subprocess.run([sys.executable, str(dispatch)], input=json.dumps(read("C:/clips/run.mp4")),
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertIn(res.returncode, (0, 2), res.stderr)
        self.assertNotIn("Traceback", res.stderr)
        self.assertIn("video_frames", advisory(json.loads(res.stdout or "{}")) or "")


if __name__ == "__main__":
    unittest.main()
