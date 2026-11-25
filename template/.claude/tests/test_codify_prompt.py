#!/usr/bin/env python3
"""Re-runnable proof for tools/codify_prompt.py (plan codify-r22 D5) and the dispatch_size preflight check
of a hand-edited codify prompt. The transcript fixture is two owner rows cut from session 5a10d094's live
transcript (the 23:22 multi-part message and the 23:40 correction).

    python3 .claude/tests/test_codify_prompt.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOLS = os.path.join(ROOT, ".claude", "tools")
FIXTURE = os.path.join(HERE, "codify_prompt_fixture", "5a10d094_owner_rows.jsonl")
SID = "5a10d094-0ffb-4cba-b8eb-733dac619b81"
CLOSING = "completely neutral and unbiased, right?"
sys.path.insert(0, TOOLS)


def run(root, *args, spawn=False):
    import codify_prompt

    argv = ["--root", root, "--transcript", FIXTURE, "--session", SID, *args]
    if spawn:
        return subprocess.run([sys.executable, os.path.join(TOOLS, "codify_prompt.py"), *argv],
                              capture_output=True, text=True, encoding="utf-8")
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = codify_prompt.main(argv)
    return subprocess.CompletedProcess(argv, code, stdout.getvalue(), stderr.getvalue())


class Render(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name

    def test_correction_renders_the_i4_prompt(self):
        done = run(self.root, "--at", "2026-11-18 23:40", "--closing", CLOSING, "--base", "7afed94d", spawn=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        path = os.path.join(self.root, ".claude", "scratch", "codify_5a10d094_1", "prompt.md")
        text = Path(path).read_text(encoding="utf-8")
        packet = Path(path).with_name("evidence.md")
        self.assertTrue(packet.is_file())
        digest = hashlib.sha256(packet.read_bytes()).hexdigest()
        self.assertIn(".claude/scratch/codify_5a10d094_1/evidence.md` (sha256 `%s`)" % digest, text)
        self.assertEqual(done.stdout.splitlines(), [p.as_posix() for p in (packet, Path(path))])
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
        glm = Path(base, "codify_5a10d094_2", "prompt-glm.md")
        self.assertIn("delegate-glm/", glm.read_text(encoding="utf-8"))
        self.assertTrue(glm.with_name("evidence-glm.md").is_file())

    def test_part_opening_names_the_part(self):
        done = run(self.root, "--at", "2026-11-18 23:22", "--closing", "is what round 21 did, right?",
                   "--part-opens", "but I imagine when I write 40 comments in")
        self.assertEqual(done.returncode, 0, done.stderr)
        text = Path(self.root, ".claude", "scratch", "codify_5a10d094_1", "prompt.md").read_text(encoding="utf-8")
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


class Evidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transcript = self.root / "turns.jsonl"

    def write_turns(self, reply_count):
        rows = []

        def row(kind, minute, second, content, **extra):
            rows.append({"type": kind, "timestamp": f"2026-11-18T23:{minute:02d}:{second:02d}.000Z",
                         "message": {"role": kind, "content": content}, **extra})

        row("assistant", 29, 1, "OPENING-ONLY")
        for i in range(5):
            minute = 30 + i
            row("assistant", minute, 1, [{"type": "text", "text": f"LIST-REPLY-{i}"}])
            row("assistant", minute, 2, f"STRING-REPLY-{i}")
            row("assistant", minute, 3, [{"type": "tool_use", "name": "Read", "input": {"path": f"FILE-{i}"}}])
            row("assistant", minute, 4, "SIDECHAIN-ONLY", isSidechain=True)
            if i == 3:
                for n in range(reply_count):
                    row("assistant", minute, 5, f"BULK-ROW-{n:04d}")
            row("user", minute, 10, f"OWNER-{i}\nSecond line  stays verbatim.")
        row("assistant", 34, 11, "AFTER-CORRECTION")
        row("user", 34, 12, "LATER-SAME-MINUTE")
        row("user", 35, 10, "LATER-OWNER")
        self.transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def test_transcript_packet_and_wider_window(self):
        import codify_prompt
        rows_per_turn = 170
        self.write_turns(rows_per_turn)
        owner_texts = [f"OWNER-{i}\nSecond line  stays verbatim." for i in range(5)]
        original_open = open
        for width in (None, 5):
            with self.subTest(turns=width):
                opens = []

                def track_open(path, *args, **kwargs):
                    if Path(path) == self.transcript:
                        opens.append(path)
                    return original_open(path, *args, **kwargs)

                stdout = StringIO()
                argv = ["--root", str(self.root), "--transcript", str(self.transcript), "--session", SID,
                        "--at", "2026-11-18 23:34 UTC", "--closing", "OWNER-4"]
                if width is not None:
                    argv += ["--turns", str(width)]
                with patch("builtins.open", side_effect=track_open), redirect_stdout(stdout):
                    self.assertEqual(codify_prompt.main(argv), 0)
                packet = Path(stdout.getvalue().splitlines()[0]).read_text(encoding="utf-8")
                for owner_text in owner_texts:
                    self.assertIn(owner_text, packet)
                for outside in ("SIDECHAIN-ONLY", "AFTER-CORRECTION", "LATER-SAME-MINUTE", "LATER-OWNER"):
                    self.assertNotIn(outside, packet)
                self.assertIn("last %d owner messages" % (width or 3), packet)
                self.assertIn("LIST-REPLY-4", packet)
                self.assertIn("STRING-REPLY-4", packet)
                self.assertIn('Read: {"path": "FILE-4"}', packet)
                self.assertEqual("OPENING-ONLY" in packet, width == 5)
                self.assertEqual("LIST-REPLY-0" in packet, width == 5)
                self.assertIn("BULK-ROW-0000", packet)
                self.assertIn("BULK-ROW-0169", packet)
                self.assertNotIn("BULK-ROW-0080", packet)
                self.assertEqual(packet.count("rows left out"), 1)
                self.assertEqual(len(opens), 1)


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

    def test_missing_or_edited_packet_is_flagged_by_check_and_preflight(self):
        import codify_prompt
        import dispatch_size
        packet = Path(self.prompt).with_name("evidence.md")
        self.assertTrue(packet.is_file())
        for mutation in (None, lambda: packet.write_bytes(b"changed"), packet.unlink):
            if mutation is not None:
                mutation()
            stdout = StringIO()
            with redirect_stdout(stdout):
                codify_prompt.main(["--check", self.prompt])
            self.assertEqual(bool(stdout.getvalue()), mutation is not None)
            self.assertEqual(bool(dispatch_size.codify_prompt_lines(self.spec(self.prompt))), mutation is not None)

    def test_relocated_pair_does_not_validate_a_different_packet(self):
        import codify_prompt

        prompt = Path(self.prompt)
        packet = prompt.with_name("evidence.md")
        sibling = prompt.parent.with_name("codify_5a10d094_2")
        sibling.mkdir()
        (sibling / packet.name).write_bytes(packet.read_bytes())
        moved = prompt.rename(sibling / prompt.name)
        packet.write_bytes(b"changed at the path the prompt names")
        self.assertTrue(codify_prompt.differs(moved))

    def test_other_prompt_names_are_not_checked(self):
        import dispatch_size
        other = os.path.join(self.root, "brief.md")
        Path(other).write_text("any text", encoding="utf-8")
        self.assertEqual(dispatch_size.codify_prompt_lines(self.spec(other)), [])


if __name__ == "__main__":
    unittest.main()
