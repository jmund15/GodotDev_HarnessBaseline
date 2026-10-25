"""post_check `_archive_rows` keeps a finding's `agent` only when it names its seat or one of the seat's lens keys;
any other value (a seat that wrote its own label) becomes the seat key, so `roster_mandates` maps it to the roster
seat's mandates instead of reading it as a lens no registry declares. Items are cut from a captured
`session_digest.build_workflow_result_archive` result."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import post_check  # noqa: E402
import session_digest  # noqa: E402

SEAT = "rt-boundary+rt-abstraction+rt-failuremode+rt-yagni-scope+rt-testability+delta-b"


def item(fid, lens, agent):
    return {"id": fid, "lens": lens, "label": "review:" + lens, "index": 0,
            "finding": {"agent": agent, "action": "FIX", "category": "bug", "critical": False,
                        "file": "design.md:374", "description": "d", "rationale": "r"}}


ARCHIVE = {"status": "completed", "items": [
    item("F1", SEAT, "rt-failuremode"),
    item("F2", SEAT, "redteam-delta-b"),
    item("F3", "rt-boundary", "rt-boundary"),
    item("F4", "rt-boundary", "boundary lens"),
    item("F5", SEAT, SEAT),
]}


class SeatAttributionTests(unittest.TestCase):
    def rows(self):
        with mock.patch.object(session_digest, "build_workflow_result_archive", return_value=ARCHIVE):
            rows, _ = post_check._archive_rows(Path("."), merge=False)
        return {row["id"]: row["agent"] for row in rows}

    def test_an_agent_naming_a_lens_of_its_seat_is_kept(self):
        agents = self.rows()
        self.assertEqual(agents["F1"], "rt-failuremode")
        self.assertEqual(agents["F3"], "rt-boundary")
        self.assertEqual(agents["F5"], SEAT)

    def test_an_agent_naming_no_lens_of_its_seat_becomes_the_seat_key(self):
        agents = self.rows()
        self.assertEqual(agents["F2"], SEAT)
        self.assertEqual(agents["F4"], "rt-boundary")

    def test_the_seat_key_maps_to_the_roster_seats_mandates(self):
        roster = [{"key": SEAT, "mandates": ["rt-boundary", "rt-failuremode"]}]
        mapped = post_check.roster_mandates({"a.md": {self.rows()["F2"]}}, roster)
        self.assertEqual(mapped, {"a.md": {"rt-boundary", "rt-failuremode"}})


if __name__ == "__main__":
    unittest.main()
