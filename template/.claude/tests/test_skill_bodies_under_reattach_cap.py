"""Every invocable body stays under the client's re-attach cap (SKILL_REATTACH_CHARS): skills/*/SKILL.md,
commands/*.md and commands/checklists/*.md. Every `reference/<file>` a skill body points to (a `→ ` arrow or
a markdown link) resolves.

The client re-attaches an invoked body after a compaction and cuts a body past the cap, so a body over it
returns truncated."""
import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "hooks"))
from _hook_state import SKILL_REATTACH_CHARS  # noqa: E402

spec = importlib.util.spec_from_file_location("load_census_cap", CLAUDE / "tools" / "load_census.py")
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)

REFERENCE_CITE = re.compile(r"→ `(reference/[A-Za-z0-9_./-]+\.md)`|\]\((reference/[A-Za-z0-9_./-]+\.md)\)")


class SkillBodiesUnderCapTests(unittest.TestCase):
    def test_no_live_invocable_body_is_over_the_cap(self):
        rows = lc.reattach_over_cap(CLAUDE)
        self.assertEqual(rows, [], "invocable bodies over %d characters: %s" % (SKILL_REATTACH_CHARS, rows))

    def test_a_planted_oversized_skill_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = root / "skills" / "planted" / "SKILL.md"
            body.parent.mkdir(parents=True)
            body.write_text("x" * (SKILL_REATTACH_CHARS + 1), encoding="utf-8", newline="")
            small = root / "skills" / "small" / "SKILL.md"
            small.parent.mkdir(parents=True)
            small.write_text("x" * SKILL_REATTACH_CHARS, encoding="utf-8", newline="")
            command = root / "commands" / "planted.md"
            command.parent.mkdir(parents=True)
            command.write_text("x" * (SKILL_REATTACH_CHARS + 1), encoding="utf-8", newline="")
            checklist = root / "commands" / "checklists" / "planted.md"
            checklist.parent.mkdir(parents=True)
            checklist.write_text("x" * (SKILL_REATTACH_CHARS + 1), encoding="utf-8", newline="")
            self.assertEqual([r["path"] for r in lc.reattach_over_cap(root)],
                             [".claude/commands/checklists/planted.md", ".claude/commands/planted.md",
                              ".claude/skills/planted/SKILL.md"])

    def test_every_reference_a_skill_body_cites_exists(self):
        missing = []
        for body in sorted((CLAUDE / "skills").glob("*/SKILL.md")):
            text = body.read_text(encoding="utf-8")
            for m in REFERENCE_CITE.finditer(text):
                rel = m.group(1) or m.group(2)
                if not (body.parent / rel).is_file():
                    missing.append("%s -> %s" % (body.parent.name, rel))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
