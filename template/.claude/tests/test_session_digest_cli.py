"""session_digest's modes: which transcript each one picks, the explore journal read, and the packet's bound.

Transcripts and journals are written under a temp folder; the scan cache and the transcript store are planted
there too. One spawned case proves the entry point; every other case calls the tool's functions in process.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / "tools" / "session_digest.py"
spec = importlib.util.spec_from_file_location("session_digest", TOOL)
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)


def user_row(text):
    return {"type": "user", "timestamp": "2026-11-22T10:00:00Z", "message": {"content": text}}


def assistant_row(text):
    return {"type": "assistant", "timestamp": "2026-11-22T10:01:00Z",
            "message": {"content": [{"type": "text", "text": text}]}}


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


class Store(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = self.root / "store"
        self.store.mkdir()
        for name, value in (("HARNESS_HOOK_STATE_DIR", str(self.root / "state")),):
            patch = mock.patch.dict(os.environ, {name: value})
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(digest, "project_dirs", return_value=[self.store])
        patch.start()
        self.addCleanup(patch.stop)

    def transcript(self, sid, rows=None, mtime=None):
        path = write_rows(self.store / f"{sid}.jsonl", rows or [user_row("A real user request.")])
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def active(self, sid):
        return mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": sid})


class Selection(Store):
    def test_missing_active_identity_refuses_to_pick_a_peer(self):
        self.transcript("peer", mtime=100)
        for value in ("missing", ""):
            with self.active(value), self.assertRaises(SystemExit):
                digest.locate(str(self.root), Namespace(session=None, match_file=None, previous=True))

    def test_previous_is_the_transcript_before_the_active_one_not_the_newest_peer(self):
        previous = self.transcript("previous", mtime=100)
        self.transcript("active", mtime=200)
        self.transcript("peer", mtime=300)
        with self.active("active"):
            self.assertEqual(previous, digest.locate(str(self.root), Namespace(session=None, match_file=None, previous=True)))

    def test_ambiguous_prefix_is_rejected(self):
        self.transcript("same-one")
        self.transcript("same-two")
        with self.assertRaises(SystemExit):
            digest.locate(str(self.root), Namespace(session="same", match_file=None, previous=False))

    def test_exact_id_beats_longer_prefix_matches(self):
        expected = self.transcript("same")
        self.transcript("same-longer", mtime=2000000000)
        self.assertEqual(expected, digest.locate(str(self.root), Namespace(session="same", match_file=None, previous=False)))

    def test_match_file_finds_the_session_whose_assistant_wrote_the_paste(self):
        words = ("reviewed the checkout ledger carefully then rebuilt quartz tooling while waiting for "
                 "nightly lattice verification results across several parallel workers")
        target = self.transcript("wrote", [user_row("go"), assistant_row(
            "Closing report: " + words + ". The remaining items are listed in the state file for next time.")])
        self.transcript("other", [assistant_row("Unrelated reply about something else entirely, written at length "
                                                "so that it is long enough to be a candidate block for matching here.")])
        paste = self.root / "paste.txt"
        paste.write_text("> " + words + " (pasted, re-rendered)", encoding="utf-8")
        self.assertEqual(target, digest.locate(str(self.root), Namespace(session=None, match_file=str(paste), previous=False)))

    def test_a_paste_never_matches_the_session_that_received_it(self):
        words = ("reviewed the checkout ledger carefully then rebuilt quartz tooling while waiting for "
                 "nightly lattice verification results across several parallel workers")
        self.transcript("receiver", [user_row(words)])
        paste = self.root / "paste.txt"
        paste.write_text(words, encoding="utf-8")
        with self.assertRaises(SystemExit):
            digest.locate(str(self.root), Namespace(session=None, match_file=str(paste), previous=False))

    def test_listing_marks_only_the_exact_active_session(self):
        self.transcript("activeid", mtime=100)
        self.transcript("peer-id", mtime=200)
        with self.active("activeid"):
            lines = digest.list_sessions([self.store], 5).splitlines()
        self.assertEqual(2, len(lines))
        self.assertEqual(1, sum("(active session)" in line for line in lines))
        self.assertTrue(next(line for line in lines if line.startswith("activeid")).endswith("(active session)"))

    def test_listing_spans_every_store_of_the_checkout_newest_first(self):
        other = self.root / "worktree-store"
        other.mkdir()
        self.transcript("here", mtime=100)
        write_rows(other / "there.jsonl", [user_row("A request made in a linked worktree.")])
        os.utime(other / "there.jsonl", (200, 200))
        lines = digest.list_sessions([self.store, other], 5).splitlines()
        self.assertEqual(["there", "here"], [line.split()[0] for line in lines])

    def test_task_anchor_wrappers_are_the_commands_that_exist(self):
        self.assertIn("/close", digest._TASK_ANCHOR_WRAPPER_COMMANDS)
        self.assertFalse({"/session_end", "/clean_push"} & digest._TASK_ANCHOR_WRAPPER_COMMANDS)


class ExploreJournal(Store):
    def journal(self, *rows):
        run = self.root / "run"
        run.mkdir(exist_ok=True)
        (run / "journal.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return run

    @staticmethod
    def started(agent, key, label, phase="Explore"):
        return {"type": "started", "agentId": agent, "key": key, "label": label, "phase": phase}

    @staticmethod
    def claim(**overrides):
        claim = {"subject": "digest", "polarity": "exists", "claim": "the tool exists", "bearing": "context"}
        claim.update(overrides)
        return claim

    def test_claims_gaps_and_lens_status_follow_each_result(self):
        run = self.journal(
            self.started("a1", "k1", "explore:alpha"),
            {"type": "result", "agentId": "a1", "key": "k1", "result": {
                "claims": [self.claim(), self.claim(bearing="nonsense")],
                "checked": {"toolsUsed": ["Grep"], "stoppedAt": "exhausted-leads", "basis": "searched"},
                "gaps": ["no test for the cache"]}},
            self.started("b1", "k2", "explore:beta"),
            {"type": "result", "agentId": "b1", "key": "k2", "result": {
                "claims": [], "checked": {"toolsUsed": [], "stoppedAt": "blocked", "basis": "denied"}}},
            self.started("c1", "k3", "explore:gamma"),
            {"type": "failed", "agentId": "c1", "key": "k3"},
            self.started("v1", "k4", "verify:alpha", phase="Verify"),
            {"type": "result", "agentId": "v1", "key": "k4", "result": {"verdict": "ok"}},
        )
        value = digest.read_explore_journal(run)
        self.assertRegex(value["journal_sha256"], r"^[0-9a-f]{64}$")
        by_label = {row["label"]: row for row in value["lenses"]}
        self.assertEqual("partial", by_label["explore:alpha"]["status"])
        self.assertEqual(1, by_label["explore:alpha"]["rejected"])
        self.assertEqual("uncovered", by_label["explore:beta"]["status"])
        self.assertEqual("failed", by_label["explore:gamma"]["status"])
        self.assertEqual("completed", by_label["verify:alpha"]["status"])
        self.assertEqual(["C1"], [claim["id"] for claim in value["claims"]])
        self.assertEqual("no test for the cache", value["gaps"][0]["gap"])
        self.assertEqual("uncovered", value["status"])

    def test_a_terminal_without_its_start_is_refused(self):
        run = self.journal({"type": "result", "agentId": "x", "key": "k", "result": {}})
        with self.assertRaises(ValueError):
            digest.read_explore_journal(run)

    def test_a_journal_with_no_started_agent_is_refused(self):
        run = self.journal({"type": "note"})
        with self.assertRaises(ValueError):
            digest.read_explore_journal(run)


class Output(Store):
    def test_the_packet_is_bounded_and_keeps_the_latest_directive(self):
        rows = [user_row(f"owner prompt number {index} " + "x" * 300) for index in range(600)]
        rows.append(user_row("the latest directive, kept at the end"))
        path = self.transcript("long", rows + [assistant_row("y" * 4000)])
        packet = digest.render_packet(digest.digest(path, str(self.root)), path, str(self.root))
        self.assertLessEqual(len(packet.encode("utf-8")), digest.HANDOFF_MAX_BYTES)
        self.assertIn("the latest directive, kept at the end", packet)
        self.assertIn("## Task anchor", packet)

    def test_full_export_keeps_every_prompt(self):
        path = self.transcript("all", [user_row(f"prompt {index}") for index in range(450)])
        text = digest.render_full(digest.digest(path, str(self.root)), path, str(self.root))
        self.assertIn("## User prompts (450)", text)
        self.assertIn("prompt 449", text)

    def test_full_export_lands_under_the_checkouts_scratch_folder(self):
        path = self.transcript("exported", [user_row("A request.")])
        out = digest.export_full(digest.digest(path, str(self.root)), path, str(self.root))
        self.assertEqual(self.root / ".claude" / "scratch" / "session_digest_exported.full.md", out)
        self.assertIn("## User prompts (1)", out.read_text(encoding="utf-8"))


class EntryPoint(unittest.TestCase):
    def test_workflow_dir_prints_the_journal_as_json_and_refuses_a_broken_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "journal.jsonl").write_text(json.dumps(
                {"type": "started", "agentId": "a", "key": "k", "label": "explore:alpha", "phase": "Explore"}) + "\n",
                encoding="utf-8")
            done = subprocess.run([sys.executable, str(TOOL), "--workflow-dir", str(run)],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual("missing terminal event", json.loads(done.stdout)["lenses"][0]["issues"][0])
            (run / "journal.jsonl").write_text("not json\n", encoding="utf-8")
            broken = subprocess.run([sys.executable, str(TOOL), "--workflow-dir", str(run)],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            self.assertEqual(1, broken.returncode)
            self.assertIn("malformed journal JSON", broken.stderr)


if __name__ == "__main__":
    unittest.main()
