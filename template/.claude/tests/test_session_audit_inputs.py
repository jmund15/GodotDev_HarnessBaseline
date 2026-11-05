"""B3: materialize the trigger's immutable input packet in a planted repository."""
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import lens
import session_audit_inputs as inputs

import test_review_ledger as fixtures
from _ledger_rows import ledger_row

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule
TOOL = Path(__file__).resolve().parents[1] / "tools/session_audit_inputs.py"
DOCTRINE = ".claude/rules/example.md"
RULE = "Read premises.\n"
# The seat's tier is the registry's own: a consumer's session-audit registry may hold it at another tier.
SEAT_TIER = lens.seat_tier("sa-harness-quality")
ROSTER = [{"key": "sa-harness-quality", "mandates": ["harness-quality"], "tier": SEAT_TIER, "effort": "none"}]


class DoctrineFixture(fixtures.LedgerFixture):
    def _plant_claude(self):
        super()._plant_claude()
        path = self.claude / "skills/project_subsystems/adaptation.json"
        data = json.loads(path.read_text())
        data["review_mandates"]["harness-quality"] = {
            "home": "commands/agents/session_audit_agents.md#sa-harness-quality", "interaction": False}
        path.write_text(json.dumps(data), encoding="utf-8")
        path = self.claude / "commands/agents/session_audit_agents.md"
        path.write_text(path.read_text() + "\n### sa-harness-quality\n\n" + RULE, encoding="utf-8")

    def _commit(self, message):
        return super()._commit(message + "\n\nTriage: reach=shared reversibility=clean design=settled grounding=verified "
                                         "oracle=new-test size=milestones kind=decision")

    def audit(self, paths=(DOCTRINE,), **over):
        fields = dict(head=self.head(), files=over.get("files", {p: self.blob(p) for p in paths
                                                                 if (self.repo / p).is_file()}),
                      tier=SEAT_TIER, effort="high", seat="sa-harness-quality")
        fields.update(over)
        if "premise_commits" not in fields:
            fields["premise_commits"] = subprocess.check_output(
                ["git", "-C", str(self.repo), "rev-list", "--reverse", self.base_sha + ".." + self.head()],
                text=True).splitlines()
        # Independent hash of the fixture's complete section, not the production rule_hash function.
        text = "### sa-harness-quality\n\n" + RULE.rstrip("\n")
        row = ledger_row("harness-quality", "sha256:" + hashlib.sha256(text.encode()).hexdigest(), **fields)
        previous = [json.loads(line) for line in self.ledger.read_text().splitlines()] if self.ledger.is_file() else []
        self.plant_rows(*previous, row)

    def delete_doctrine(self):
        self.r.files.pop(DOCTRINE)
        (self.repo / DOCTRINE).unlink()
        return self._commit("delete doctrine only")

    def deletion_base(self):
        self._write(DOCTRINE, "existing doctrine\n")
        self.base_sha = self._commit("existing doctrine")
        self.r.set_upstream(self.base_sha)
        return self.base_sha


class TriggerInputsTests(DoctrineFixture):
    def freeze(self, *args):
        roster = self.root / "roster.json"
        roster.write_text(json.dumps(ROSTER), encoding="utf-8")
        out = self.repo / ".claude/scratch/audit/inputs.json"
        result = subprocess.run(
            [sys.executable, str(TOOL), "--root", str(self.repo), "--base-ref", self.base_sha,
             "--session-base", self.base_sha, "--roster", str(roster), "--out", str(out), *args],
            env={**os.environ, **self.env()}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(out.read_text()), out

    def read_input(self, path):
        return (self.repo / path).read_text(encoding="utf-8")

    def test_doctrine_commits_uses_shared_enumeration_without_mutating_it(self):
        commits = [
            {"sha": "first", "subject": "mixed", "files": ["src/a.cs", DOCTRINE]},
            {"sha": "second", "subject": "memory", "files": [".claude/auto-memory/note.md"]},
            {"sha": "third", "subject": "empty merge", "files": []},
            {"sha": "fourth", "subject": "deleted", "files": [DOCTRINE]},
        ]
        original = json.loads(json.dumps(commits))
        with patch.object(inputs.ledger, "range_commits", return_value=commits, create=True) as enumerate_range:
            actual = inputs.doctrine_commits(self.repo, self.base_sha, self.head())
            enumerate_range.assert_called_once_with(self.repo, self.base_sha, self.head())
        self.assertEqual(actual, [
            {"sha": "first", "subject": "mixed", "files": [DOCTRINE]},
            {"sha": "fourth", "subject": "deleted", "files": [DOCTRINE]},
        ])
        self.assertEqual(commits, original)

    def test_explicit_base_ref_freezes_a_pushed_range(self):
        self._write(DOCTRINE, "A\n")
        pushed = self._commit("pushed doctrine")
        git = ["git", "-C", str(self.repo)]
        branch = subprocess.check_output(git + ["rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        subprocess.run(git + ["branch", "-q", "up", pushed], check=True)
        subprocess.run(git + ["config", "branch.%s.remote" % branch, "."], check=True)
        subprocess.run(git + ["config", "branch.%s.merge" % branch, "refs/heads/up"], check=True)
        data, _ = self.freeze("--widen")
        self.assertEqual(json.loads(self.read_input(data["premiseCommits"])), [pushed])

    def test_a_subject_with_no_change_anywhere_refuses_to_freeze(self):
        # Every named file is unchanged between the session base, HEAD and the working tree: a seat over
        # this freeze would read empty diffs and its coverage could never hold (run r8 of session 42d95cf4).
        self._write(DOCTRINE, "A\n")
        self.base_sha = self._commit("landed doctrine")
        self.r.set_upstream(self.base_sha)
        # The fixture commits without an index; track the file so it reads as committed, not as a new file.
        subprocess.run(["git", "-C", str(self.repo), "add", "--", DOCTRINE], check=True)
        roster = self.root / "roster.json"
        roster.write_text(json.dumps(ROSTER), encoding="utf-8")
        out = self.repo / ".claude/scratch/audit/inputs.json"
        result = subprocess.run(
            [sys.executable, str(TOOL), "--root", str(self.repo), "--base-ref", self.base_sha,
             "--session-base", self.base_sha, "--roster", str(roster), "--out", str(out), "--files", DOCTRINE],
            env={**os.environ, **self.env()}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("no change", result.stderr)
        self.assertIn(DOCTRINE, result.stderr)
        self.assertFalse(out.exists())

    def test_without_base_ref_the_range_is_upstream_to_head(self):
        self._write(DOCTRINE, "A\n")
        pushed = self._commit("pushed doctrine")
        git = ["git", "-C", str(self.repo)]
        branch = subprocess.check_output(git + ["rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        subprocess.run(git + ["branch", "-q", "up", pushed], check=True)
        subprocess.run(git + ["config", "branch.%s.remote" % branch, "."], check=True)
        subprocess.run(git + ["config", "branch.%s.merge" % branch, "refs/heads/up"], check=True)
        self._write(DOCTRINE, "B\n")
        unpushed = self._commit("unpushed doctrine")
        roster = self.root / "roster.json"
        roster.write_text(json.dumps(ROSTER), encoding="utf-8")
        out = self.repo / ".claude/scratch/audit/inputs.json"
        result = subprocess.run(
            [sys.executable, str(TOOL), "--root", str(self.repo), "--session-base", self.base_sha,
             "--roster", str(roster), "--out", str(out), "--widen"],
            env={**os.environ, **self.env()}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text())
        self.assertEqual(json.loads(self.read_input(data["premiseCommits"])), [unpushed])

    def test_prior_session_and_both_changes_materialize_messages(self):
        self._write(DOCTRINE, "A\n")
        first = self._commit('prior session\n\n```premises\n{"claim":"all items","evidence":[]}\n```')
        self._write(DOCTRINE, "B\n")
        second = self._commit("current session")
        data, _ = self.freeze("--widen")
        self.assertEqual(data["files"], [DOCTRINE])
        self.assertEqual(json.loads(self.read_input(data["premiseCommits"])), [first, second])
        prompt = self.read_input(data["doctrineCommits"])
        for text in (first, second, "prior session", "```premises", '"claim":"all items"'):
            self.assertIn(text, prompt)
        self.assertTrue(data["needsAudit"])

    def test_text_covered_doctrine_commits_do_not_seat_audit(self):
        self._write(DOCTRINE, "A\n")
        first = self._commit("A")
        self.audit(premise_commits=[first])
        self._write(DOCTRINE, "B\n")
        second = self._commit("B")
        self._write(DOCTRINE, "A\n")
        third = self._commit("A again")
        data, _ = self.freeze("--widen")
        self.assertEqual(data["uncoveredText"], [])
        self.assertNotIn("uncoveredPremises", data)
        self.assertFalse(data["needsAudit"])

    def test_deletion_and_rename_source_have_must_read_diffs(self):
        self.deletion_base()
        deleted = self.delete_doctrine()
        data, _ = self.freeze("--widen")
        self.assertIn(DOCTRINE, data["files"])
        diffs = "\n".join(self.read_input(p) for p in data["mustRead"])
        self.assertIn("deleted file mode", diffs)
        self.assertIn("-existing doctrine", diffs)
        self.assertIn(deleted, self.read_input(data["doctrineCommits"]))

    def test_rename_out_retains_doctrine_source(self):
        self.deletion_base()
        content = (self.repo / DOCTRINE).read_text()
        self.r.files.pop(DOCTRINE)
        (self.repo / DOCTRINE).unlink()
        self._write("notes/moved.md", content)
        renamed = self._commit("rename out")
        data, _ = self.freeze("--widen")
        self.assertIn(DOCTRINE, data["files"])
        self.assertEqual(json.loads(self.read_input(data["premiseCommits"])), [renamed])
        self.assertIn("deleted file mode", "\n".join(self.read_input(p) for p in data["mustRead"]))

    def test_transient_path_keeps_both_commits_and_deletion_diff(self):
        self._write(DOCTRINE, "temporary\n")
        created = self._commit("create")
        deleted = self.delete_doctrine()
        data, _ = self.freeze("--widen")
        self.assertEqual(json.loads(self.read_input(data["premiseCommits"])), [created, deleted])
        self.assertIn(DOCTRINE, data["files"])
        self.assertIn("-temporary", "\n".join(self.read_input(p) for p in data["mustRead"]))

    def test_record_route_boundary_only_records_frozen_inputs(self):
        self.deletion_base()
        frozen = self.delete_doctrine()
        data, _ = self.freeze("--widen")
        self._write(DOCTRINE, "restored\n")
        self._commit("restore")
        later = self.delete_doctrine()
        result = self.run_tool(
            "record", "--stage", "candidate", "--subject", "session:frozen-trigger",
            "--inline", "audit", "--mandate", "harness-quality", "--outcome", "clean",
            "--files", *data["files"], "--head", data["head"],
            "--blobs", str(self.repo / data["blobs"]),
            "--premise-commits", str(self.repo / data["premiseCommits"]))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        row = json.loads(self.ledger.read_text().splitlines()[-1])
        self.assertEqual(row["premise_commits"], [frozen])
        self.assertEqual(row["files"][DOCTRINE], "deleted:" + frozen)
        self.assertNotEqual(later, frozen)

    def test_session_decisions_include_evidence_items(self):
        path = self.repo / ".claude/logs/tasks/test.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task_id": "test", "session_id": "abcdefgh-full",
            "decisions": [{"id": "D1", "text": "cause", "evidence_items": [{"owner": "yes"}],
                           "evidence_session": "abcdefgh-full"}]}), encoding="utf-8")
        data, _ = self.freeze("--widen", "--session", "abcdefgh-full")
        prompt = self.read_input(data["doctrineCommits"])
        for text in ("D1", "cause", "evidence_items", '"owner": "yes"'):
            self.assertIn(text, prompt)
        self.assertTrue(data["needsAudit"])

    def test_archived_decisions_are_frozen_and_live_duplicate_wins(self):
        path = self.repo / ".claude/logs/tasks/test.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        archived = {"id": "D1", "kind": "decision", "text": "archived cause",
                    "evidence_items": [{"owner": "yes"}], "evidence_session": "abcdefgh-full"}
        old = {"id": "D2", "kind": "decision", "text": "old cause"}
        live = {"id": "D2", "text": "current cause", "evidence_items": [{"owner": "now"}]}
        path.write_text(json.dumps({"task_id": "test", "session_id": "abcdefgh-full",
                                    "decisions": [live]}), encoding="utf-8")
        path.with_suffix(".archive.jsonl").write_text(
            "\n".join(json.dumps(row) for row in (archived, old)) + "\n", encoding="utf-8")
        data, _ = self.freeze("--session", "abcdefgh-full")
        rows = json.loads(self.read_input(next(p for p in data["mustRead"] if p.endswith("decisions.json"))))
        self.assertEqual([r["decision"] for r in rows], [archived, live])
        self.assertTrue(data["needsAudit"])
        self.assertIn("archived cause", self.read_input(data["doctrineCommits"]))

    def test_archive_only_decision_admits_seat_from_selected_root(self):
        path = self.repo / ".claude/logs/tasks/test.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task_id": "test", "session_id": "abcdefgh-full",
                                    "decisions": []}), encoding="utf-8")
        archived = {"id": "D1", "kind": "decision", "text": "closed cause",
                    "evidence_items": [{"owner": "yes"}]}
        path.with_suffix(".archive.jsonl").write_text(json.dumps(archived) + "\n", encoding="utf-8")
        data, _ = self.freeze("--session", "abcdefgh-full")
        self.assertTrue(data["needsAudit"])
        self.assertIn("closed cause", self.read_input(data["doctrineCommits"]))

    def test_task_and_plan_are_frozen_without_harness_text(self):
        path = self.repo / ".claude/logs/tasks/game.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        plan = ".claude/plans/game.md"
        self._write(plan, "# Game plan\nKeep the contract.\n")
        record = {"task_id": "game", "session_id": "earlier-session", "rigor": "thorough",
                  "triage": {"kind": "decision"}, "decisions": [],
                  "scope": {"files": [plan]}}
        path.write_text(json.dumps(record), encoding="utf-8")
        data, _ = self.freeze("--task", "game")
        packet = "\n".join(self.read_input(p) for p in data["mustRead"])
        self.assertIn('"rigor": "thorough"', packet)
        self.assertIn("Keep the contract.", packet)
        self.assertEqual(data["files"], [])
        self.assertTrue(data["needsAudit"])
        path.write_text('{}', encoding="utf-8")
        (self.repo / plan).write_text("Changed later.", encoding="utf-8")
        self.assertEqual(packet, "\n".join(self.read_input(p) for p in data["mustRead"]))

    def test_a_linked_worktree_freezes_the_main_checkouts_task_and_untracked_plan(self):
        plan = ".claude/plans/lane.md"
        (self.repo / plan).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / plan).write_text("# Lane plan\nOnly the main checkout holds me.\n", encoding="utf-8")
        record = self.repo / ".claude/logs/tasks/lane.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"task_id": "lane", "session_id": "s", "decisions": [],
                                      "scope": {"files": [plan]}}), encoding="utf-8")
        lane = self.root / "lane-wt"
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(lane)], cwd=self.repo,
                       env={**os.environ, **self.env()}, check=True, capture_output=True)
        roster = self.root / "roster.json"
        roster.write_text(json.dumps(ROSTER), encoding="utf-8")
        out = lane / ".claude/scratch/audit/inputs.json"
        env = {k: v for k, v in {**os.environ, **self.env()}.items() if k != "HARNESS_TASK_RECORD_DIR"}
        result = subprocess.run(
            [sys.executable, str(TOOL), "--root", str(lane), "--base-ref", self.base_sha,
             "--session-base", self.base_sha, "--roster", str(roster), "--out", str(out), "--task", "lane"],
            env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text())
        self.assertIn("Only the main checkout holds me.",
                      "\n".join((lane / p).read_text(encoding="utf-8") for p in data["mustRead"]))

    def test_explicit_plan_is_frozen_without_task(self):
        self._write("short_plan.md", "# Plan\nChoose composition.\n")
        data, _ = self.freeze("--plan", "short_plan.md")
        self.assertIn("Choose composition.", "\n".join(self.read_input(p) for p in data["mustRead"]))
        self.assertTrue(data["needsAudit"])

    def test_missing_selected_task_refuses_freeze(self):
        with self.assertRaisesRegex(AssertionError, "game"):
            self.freeze("--task", "game")

    def test_peer_dirty_widened_path_reads_only_committed_input(self):
        self._write(DOCTRINE, "committed\n")
        sha = self._commit("committed")
        blob = self.blob(DOCTRINE)
        (self.repo / DOCTRINE).write_text("peer dirty\n", encoding="utf-8")
        data, _ = self.freeze("--widen", "--committed-only", DOCTRINE)
        self.assertEqual(json.loads(self.read_input(data["blobs"]))[DOCTRINE], blob)
        inputs = "\n".join(self.read_input(p) for p in data["mustRead"])
        self.assertNotIn("peer dirty", inputs)
        self.assertIn("committed", inputs)
        self.assertEqual(json.loads(self.read_input(data["premiseCommits"])), [sha])

    def test_packet_carries_the_restate_scan_for_the_subject_files(self):
        self._write(DOCTRINE, "committed\n")
        self._commit("committed")
        data, _ = self.freeze("--widen")
        self.assertIn(data["restateGroups"], data["mustRead"])
        self.assertIsInstance(data["restateScanExit"], int)
        self.assertIn("restate_scan", self.read_input(data["restateGroups"]))

    def test_restate_scan_takes_a_subject_list_longer_than_a_command_line(self):
        self._write(DOCTRINE, "committed\n")
        self._commit("committed")
        files = [DOCTRINE] + [f".claude/commands/subject_list_case_{i:05d}.md" for i in range(1500)]
        self.assertGreater(sum(len(f) + 1 for f in files), 32767)
        code, text = inputs.restate_groups(self.repo, self.base_sha, files)
        self.assertIsInstance(code, int)
        self.assertIn("restate_scan", text)

    def test_a_commit_touching_more_files_than_a_command_line_freezes(self):
        names = [f".claude/commands/subject_list_case_{i:05d}.md" for i in range(800)]
        self.assertGreater(sum(len(n) + 1 for n in names), 32767)
        commits = [{"sha": "big", "subject": "wide", "files": names}]
        calls = []

        def fake_git(root, *args):
            calls.append(args)
            if sum(len(a) + 1 for a in args) > 32767:
                raise FileNotFoundError(206, "The filename or extension is too long")
            return "diff for " + " ".join(a for a in args if a.endswith(".md"))
        with patch.object(inputs, "git", side_effect=fake_git):
            text = inputs.commit_diff_text(self.repo, commits[0])
        self.assertIn("subject_list_case_00799.md", text)
        self.assertIn("subject_list_case_00000.md", text)

    def test_peer_dirty_path_stays_out_of_the_restate_scan(self):
        self._write(DOCTRINE, "committed\n")
        self._commit("committed")
        (self.repo / DOCTRINE).write_text("peer dirty\n", encoding="utf-8")
        data, _ = self.freeze("--widen", "--committed-only", DOCTRINE)
        self.assertEqual(data["restateScanExit"], 2)
        self.assertIn("no subject files", self.read_input(data["restateGroups"]))

    def test_frozen_dirty_blob_resolves_after_the_file_moves_on(self):
        # The packet's blob ids are recorded later by review_ledger, which accepts only an object git holds or
        # bytes still on disk; a working-tree file edited after the freeze must not strand its frozen id.
        self._write(DOCTRINE, "committed\n")
        self._commit("committed")
        (self.repo / DOCTRINE).write_text("reviewed while uncommitted\n", encoding="utf-8")
        data, _ = self.freeze("--files", DOCTRINE)
        blob = json.loads(self.read_input(data["blobs"]))[DOCTRINE]
        (self.repo / DOCTRINE).write_text("edited after the freeze\n", encoding="utf-8")
        held = subprocess.run(["git", "-C", str(self.repo), "cat-file", "-e", blob + "^{blob}"],
                              env={**os.environ, **self.env()}, capture_output=True)
        self.assertEqual(held.returncode, 0, "frozen blob %s is not in the object store" % blob)

    def test_dirty_input_drift_refuses_packet_before_dispatch(self):
        self._write(DOCTRINE, "first\n")
        self._commit("first")
        roster = self.root / "roster.json"
        roster.write_text(json.dumps(ROSTER), encoding="utf-8")
        out = self.repo / ".claude/scratch/drift/inputs.json"
        actual_git = inputs.git
        changed = False

        def git_with_drift(root, *argv, **kwargs):
            nonlocal changed
            answer = actual_git(root, *argv, **kwargs)
            if argv[0] == "hash-object" and not changed:
                changed = True
                (self.repo / DOCTRINE).write_text("changed during freeze\n", encoding="utf-8")
            return answer

        with patch.dict(os.environ, self.env()), patch.object(inputs, "git", side_effect=git_with_drift):
            with self.assertRaisesRegex(inputs.ledger.ToolError, "changed while freezing"):
                inputs.freeze(root=str(self.repo), out=str(out), base_ref=self.base_sha,
                              session_base=self.base_sha, widen=True, roster=str(roster))
        self.assertFalse(out.exists())

    def test_fully_covered_commit_allows_skip(self):
        self._write(DOCTRINE, "A\n")
        self._commit("A")
        self.audit()
        data, _ = self.freeze("--widen")
        self.assertFalse(data["needsAudit"])
        self.assertNotIn("uncoveredPremises", data)

    def test_no_upstream_uses_base_ref_and_excludes_memory(self):
        self._write(".claude/auto-memory/note.md", "note\n")
        self._commit("memory")
        self._write(DOCTRINE, "rule\n")
        sha = self._commit("rule")
        data, _ = self.freeze("--widen")
        self.assertEqual(data["files"], [DOCTRINE])
        self.assertEqual(json.loads(self.read_input(data["premiseCommits"])), [sha])

    def test_frozen_evidence_is_typed_and_the_mandate_view_drops_full_source(self):
        self._write(DOCTRINE, "first\n")
        self._commit("first")
        self._write(DOCTRINE, "second\n")
        self._commit("second")
        data, _ = self.freeze("--widen")
        kinds = {artifact["kind"] for artifact in data["artifacts"]}
        self.assertTrue({"doctrine", "decisions", "restate", "commit-diff", "subject-diff", "source"} <= kinds, kinds)
        view = data["views"]["harness-quality"]
        diff = next(a for a in data["artifacts"] if a["kind"] == "subject-diff" and a.get("file") == DOCTRINE)
        source = next(a for a in data["artifacts"] if a["kind"] == "source" and a.get("file") == DOCTRINE)
        self.assertIn(diff["artifact"], view["mustRead"])
        self.assertNotIn(source["artifact"], view["mustRead"])
        self.assertEqual(diff["source"], source["artifact"])
        self.assertTrue((self.repo / source["artifact"]).is_file(), "complete frozen evidence stays on disk")
        self.assertEqual(data["mustRead"], view["mustRead"])
        self.assertEqual(data["sizes"]["views"]["harness-quality"]["bytes"],
                         sum(a["bytes"] for a in view["artifacts"]))

    def test_every_admitted_pair_names_an_artifact_in_its_view(self):
        self._write(DOCTRINE, "first\n")
        self._commit("first")
        data, _ = self.freeze("--widen")
        self.assertTrue(data["uncoveredText"])
        for pair in data["uncoveredText"]:
            path, mandate = pair[0], pair[1]
            named = set()
            for artifact in data["views"][mandate]["artifacts"]:
                if artifact.get("file"):
                    named.add(artifact["file"])
                named.update(artifact.get("files") or [])
            self.assertIn(path, named, f"{path} / {mandate} lost its evidence")

    def test_an_unknown_mandate_keeps_full_source(self):
        self.assertFalse(inputs.source_required("harness-quality"))
        self.assertTrue(inputs.source_required("cq.C"))


if __name__ == "__main__":
    unittest.main()
