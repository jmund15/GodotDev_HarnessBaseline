#!/usr/bin/env python3
"""Re-runnable proof for tools/codify_prompt.py (plan codify-r22 D5) and the dispatch_size preflight check
of a hand-edited codify prompt. The transcript fixture is two owner rows cut from session 5a10d094's live
transcript (the 23:22 multi-part message and the 23:40 correction).

    python3 .claude/tests/test_codify_prompt.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOLS = os.path.join(ROOT, ".claude", "tools")
FIXTURE = os.path.join(HERE, "codify_prompt_fixture", "5a10d094_owner_rows.jsonl")
SID = "5a10d094-0ffb-4cba-b8eb-733dac619b81"
CLOSING = "completely neutral and unbiased, right?"
sys.path.insert(0, TOOLS)


def run(root, *args):
    return subprocess.run([sys.executable, os.path.join(TOOLS, "codify_prompt.py"), "--root", root,
                           "--transcript", FIXTURE, "--session", SID, *args],
                          capture_output=True, text=True, encoding="utf-8")


class Render(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name

    def test_correction_renders_the_i4_prompt(self):
        done = run(self.root, "--at", "2026-11-18 23:40", "--closing", CLOSING, "--base", "7afed94d")
        self.assertEqual(done.returncode, 0, done.stderr)
        path = os.path.join(self.root, ".claude", "scratch", "codify_5a10d094_1", "prompt.md")
        text = open(path, encoding="utf-8").read()
        for want in ("# Independent /codify derivation", "Run Steps 0 to 5 of", "commands/codify.md",
                     "Land nothing", "Session id: `%s`" % SID, "2026-11-18 at 23:40 UTC",
                     'closing "%s"' % CLOSING, "session_digest.py", "suspect's account",
                     "git show 7afed94d:<path>", "codify_5a10d094_1/",
                     "delegate/"):
            self.assertIn(want, text)
        self.assertNotIn("This pass covers", text)

    def test_second_render_takes_the_next_folder_and_a_suffix_joins_the_latest(self):
        run(self.root, "--at", "2026-11-18 23:40", "--closing", CLOSING)
        run(self.root, "--at", "2026-11-18 23:40", "--closing", CLOSING)
        run(self.root, "--at", "2026-11-18 23:40", "--closing", CLOSING, "--out-suffix", "-glm")
        base = os.path.join(self.root, ".claude", "scratch")
        self.assertTrue(os.path.isfile(os.path.join(base, "codify_5a10d094_2", "prompt-glm.md")))
        glm = open(os.path.join(base, "codify_5a10d094_2", "prompt-glm.md"), encoding="utf-8").read()
        self.assertIn("delegate-glm/", glm)

    def test_part_opening_names_the_part(self):
        done = run(self.root, "--at", "2026-11-18 23:22", "--closing", "is what round 21 did, right?",
                   "--part-opens", "but I imagine when I write 40 comments in")
        self.assertEqual(done.returncode, 0, done.stderr)
        text = open(os.path.join(self.root, ".claude", "scratch", "codify_5a10d094_1", "prompt.md"),
                    encoding="utf-8").read()
        self.assertIn('the part that opens "but I imagine when I write 40 comments in"', text)
        self.assertIn("separate passes", text)

    def test_words_not_in_the_message_are_refused(self):
        done = run(self.root, "--at", "2026-11-18 23:40", "--closing", "was never neutral, right?")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("not in the owner message", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".claude", "scratch")))
        done = run(self.root, "--at", "2026-11-18 23:22", "--closing", "is what round 21 did, right?",
                   "--part-opens", "an invented opening")
        self.assertNotEqual(done.returncode, 0)

    def test_no_message_at_the_time_is_refused(self):
        done = run(self.root, "--at", "2026-11-18 23:41", "--closing", CLOSING)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no owner message", done.stderr)


class Backstop(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        run(self.root, "--at", "2026-11-18 23:40", "--closing", CLOSING)
        self.prompt = os.path.join(self.root, ".claude", "scratch", "codify_5a10d094_1", "prompt.md")

    def spec(self, path):
        return {"jobs": [{"label": "d", "promptFile": path}]}

    def test_rendered_prompt_is_not_flagged_and_an_edited_one_is(self):
        import dispatch_size
        import codify_prompt
        self.assertEqual(codify_prompt.differs(self.prompt), [])
        self.assertEqual(dispatch_size.codify_prompt_lines(self.spec(self.prompt)), [])
        with open(self.prompt, "a", encoding="utf-8") as fh:
            fh.write("\n- The session found the cause was the topic labels.\n")
        self.assertTrue(codify_prompt.differs(self.prompt))
        lines = dispatch_size.codify_prompt_lines(self.spec(self.prompt))
        self.assertEqual(len(lines), 1)
        self.assertIn("preflight: unmet", lines[0])
        self.assertIn("void and relaunch", lines[0])

    def test_other_prompt_names_are_not_checked(self):
        import dispatch_size
        other = os.path.join(self.root, "brief.md")
        open(other, "w").write("any text")
        self.assertEqual(dispatch_size.codify_prompt_lines(self.spec(other)), [])


if __name__ == "__main__":
    unittest.main()
