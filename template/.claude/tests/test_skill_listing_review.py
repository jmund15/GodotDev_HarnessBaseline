"""Proof for tools/skill_listing_review.py: listing entries no file of ours authors and no ruling keeps.

The transcript row is cut from a live session's skill_listing attachment (fixtures/skill_listing_review).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import skill_listing_review as slr  # noqa: E402

LIVE_ROW = json.loads((HERE / "fixtures" / "skill_listing_review" / "listing_line.json").read_text(encoding="utf-8"))


def plant_claude(root: Path, kept: dict) -> Path:
    """A .claude dir authoring testing (skill), codify, worklog, checklists:parity (commands), explore-fanout (workflow)."""
    claude = root / ".claude"
    (claude / "skills" / "testing").mkdir(parents=True)
    (claude / "skills" / "testing" / "SKILL.md").write_text("---\nname: testing\n---\n", encoding="utf-8")
    (claude / "commands" / "checklists").mkdir(parents=True)
    for rel in ("codify.md", "worklog.md", "checklists/parity.md"):
        (claude / "commands" / rel).write_text("x\n", encoding="utf-8")
    (claude / "workflows").mkdir()
    (claude / "workflows" / "explore_fanout.js").write_text(
        "export const meta = {\n  name: 'explore-fanout',\n}\n", encoding="utf-8")
    (claude / "reference").mkdir()
    (claude / "reference" / "skill_listing_kept.json").write_text(json.dumps({"kept": kept}), encoding="utf-8")
    return claude


def write_transcript(folder: Path, name: str, rows: list, mtime: float) -> Path:
    path = folder / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


class SkillListingReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.transcripts = self.root / "projects"
        self.transcripts.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unruled_lists_outside_entries_no_ruling_keeps(self):
        claude = plant_claude(self.root, {"semantic-search:search": "harness search", "loop": "self-paced loops"})
        write_transcript(self.transcripts, "a.jsonl", [{"type": "user"}, LIVE_ROW], 100)
        self.assertEqual(["anthropic-skills:docx", "anthropic-skills:pdf", "dataviz"],
                         slr.unruled(self.transcripts, claude))

    def test_banner_names_each_entry_and_the_ruling_routes(self):
        claude = plant_claude(self.root, {"semantic-search:search": "", "loop": ""})
        write_transcript(self.transcripts, "a.jsonl", [LIVE_ROW], 100)
        line = slr.banner(self.transcripts, claude)
        for needle in ("anthropic-skills:pdf", "dataviz", "skillOverrides", "skill_listing_kept.json"):
            self.assertIn(needle, line)

    def test_banner_is_silent_when_every_entry_is_ours_or_kept(self):
        kept = {n: "" for n in ("semantic-search:search", "loop", "dataviz", "anthropic-skills:pdf", "anthropic-skills:docx")}
        claude = plant_claude(self.root, kept)
        write_transcript(self.transcripts, "a.jsonl", [LIVE_ROW], 100)
        self.assertEqual("", slr.banner(self.transcripts, claude))

    def test_newest_transcript_with_a_listing_wins(self):
        claude = plant_claude(self.root, {})
        old = dict(LIVE_ROW, attachment=dict(LIVE_ROW["attachment"], names=["dataviz"]))
        write_transcript(self.transcripts, "old.jsonl", [old], 100)
        write_transcript(self.transcripts, "mid.jsonl", [LIVE_ROW], 200)
        write_transcript(self.transcripts, "new.jsonl", [{"type": "user"}], 300)  # no listing yet
        self.assertIn("anthropic-skills:pdf", slr.unruled(self.transcripts, claude))

    def test_no_transcript_with_a_listing_is_silent(self):
        claude = plant_claude(self.root, {})
        self.assertEqual("", slr.banner(self.transcripts, claude))
        self.assertEqual("", slr.banner(self.root / "absent", claude))

    def test_unreadable_ledger_says_unknown(self):
        claude = plant_claude(self.root, {})
        (claude / "reference" / "skill_listing_kept.json").write_text("{not json", encoding="utf-8")
        write_transcript(self.transcripts, "a.jsonl", [LIVE_ROW], 100)
        self.assertIn("UNKNOWN", slr.banner(self.transcripts, claude))


if __name__ == "__main__":
    unittest.main()
