#!/usr/bin/env python3
"""Proof for a review seat's effort from its lens's reading: `lens.Lens.reading` and `lens_briefs._seat_effort`.

A lens reads as `review` unless its header says `reading: checklist`. The seat's effort comes from the planted
ladder's `effort` cell for that reading (`model_registry.pin_effort`); a written rung above the computed one is
capped unless the run measures. Each case plants its own ladder and registry, so an owner's cell edit never reds it.

    python3 .claude/tests/test_lens_seat_effort.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import lens  # noqa: E402
import lens_briefs  # noqa: E402
import model_registry  # noqa: E402

HEADER = "| model | strengths | evidence | cost | effort | ± |\n|---|---|---|---|---|---|\n"
CELL = ("`medium` checklist audit lenses (a verdict per item), tight-spec execution · `medium` ambiguous execution · "
        "`high`–`xhigh` plan review and design-review lenses, higher as the subject grows · `low` ledger")


def planted_registry():
    models = [{"id": "beta-model", "alias": "beta", "family": "beta", "transport": "anthropic", "lifecycle": "current",
               "roles": ["executor"], "effort": {"evidence": "see-ladder", "avoid": []}}]
    return {"models": models, "transports": {"anthropic": {"effortValues": ["low", "medium", "high", "xhigh", "max"]}}}


class LensReading(unittest.TestCase):
    def lens_with(self, header):
        found = lens.Lens("probe", "probe lens", "review")
        found.lines = header + ["```", "body", "```"]
        return found

    def test_a_lens_without_a_reading_line_reads_as_review(self):
        self.assertEqual(self.lens_with([]).reading, "review")

    def test_a_checklist_header_line_reads_as_checklist_and_stays_out_of_the_body(self):
        found = self.lens_with(["reading: checklist"])
        self.assertEqual(found.reading, "checklist")
        self.assertNotIn("reading: checklist", found.body())

    def test_an_unknown_seat_key_has_no_reading(self):
        self.assertIsNone(lens.seat_reading("no-such-lens-key"))


class SeatEffort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="seat_effort_")
        ladder = Path(self.tmp) / "ladder.md"
        ladder.write_text("## Role guidance\n\n" + HEADER + "| beta | s | use | 5 | %s | + |\n\n## Compaction\n" % CELL,
                          encoding="utf-8")
        self.patches = [
            mock.patch.object(model_registry, "ladder_path", lambda: ladder),
            mock.patch.object(model_registry, "load", planted_registry),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seat(self, reading, explicit=None, measured=False, pick=None):
        seat = dict({"effort": explicit} if explicit else {}, **({"pick": pick} if pick else {}))
        with mock.patch.object(lens_briefs.lens, "seat_reading", lambda key: reading):
            return lens_briefs._seat_effort(seat, "probe", "beta", "author", {}, measured)

    def test_a_checklist_seat_takes_the_checklist_rung(self):
        self.assertEqual(self.seat("checklist"), "medium")

    def test_a_review_seat_takes_the_review_rung(self):
        self.assertEqual(self.seat("review"), "high")

    def test_a_written_rung_above_the_computed_one_is_capped_unless_measured(self):
        self.assertEqual(self.seat("review", explicit="max"), "high")
        self.assertEqual(self.seat("review", explicit="max", measured=True), "max")

    def test_an_owner_named_seat_rung_runs_as_named(self):
        self.assertEqual(self.seat("review", explicit="max", pick="owner"), "max")
        self.assertEqual(self.seat("review", explicit="max", pick="cell"), "high")

    def test_a_seat_with_no_reading_keeps_its_written_rung(self):
        self.assertEqual(self.seat(None, explicit="xhigh"), "xhigh")
        self.assertIsNone(self.seat(None))


if __name__ == "__main__":
    unittest.main()
