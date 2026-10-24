"""Sidecar review ingestion preserves sources and cannot turn missing work into clean."""
import copy
import json
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/tools"))
import review_result


# Trimmed blocks from sr_runs/sr-D1.progress.jsonl: Read 20/21, Bash 18/19.
# Failed Read: sr-D6b.progress.jsonl 1969/1970. Only tool/input and ids are planted below.
_CAPTURED_READ = [
    {"type": "tool_use", "id": "call_ced555ac026348e4b3ea3c2e8f249c72", "name": "Read",
     "input": {"file_path": "/work/proj/.claude/worktrees/sr-d/.claude/plans/scaled-rigor.md", "limit": 92}},
    {"type": "tool_result", "tool_use_id": "call_ced555ac026348e4b3ea3c2e8f249c72",
     "content": "1\t# Scaled rigor: three inputs every scaled check reads, and no blanket checks", "is_error": False},
]
_CAPTURED_BASH = [
    {"type": "tool_use", "id": "call_1ede0e25d4fc4d0caf99338c6a9088fa", "name": "Bash",
     "input": {"command": "git status --short; git log --format=%B $(git merge-base HEAD main)..HEAD | grep '^Slice:'",
               "description": "Check worktree state and completed slices"}},
    {"type": "tool_result", "tool_use_id": "call_1ede0e25d4fc4d0caf99338c6a9088fa",
     "content": "(Bash completed with no output)", "is_error": False},
]
_CAPTURED_FAILED_READ = [
    {"type": "tool_use", "id": "call_aaa97b1b00e9447eaf2a1e854e0ec6f5", "name": "Read",
     "input": {"file_path": "/work/proj/.claude/worktrees/sr-d6b/.claude/scratch/sr_brief/D6b.md.amend.md"}},
    {"type": "tool_result", "tool_use_id": "call_aaa97b1b00e9447eaf2a1e854e0ec6f5",
     "content": "File does not exist.", "is_error": True},
]


def captured_events(tool, given, *, error=False, cid="s0"):
    """Plant an operation in a captured call/result pair, retaining the producer's fields."""
    call, result = copy.deepcopy(_CAPTURED_FAILED_READ if error else
                                 _CAPTURED_BASH if tool in ("Bash", "PowerShell") else _CAPTURED_READ)
    call.update(name=tool, input=given, id=cid)
    result["tool_use_id"] = cid
    return [{"message": {"content": [block]}} for block in (call, result)]


def captured_mention(text):
    # sr-D1.progress.jsonl 48, trimmed to its message/content; text is the planted mention.
    event = {"message": {"content": [{"text": "I’ll commit S29, S27, S21 and S5 in order.", "type": "text"}]}}
    event["message"]["content"][0]["text"] = text
    return event


def finding(**overrides):
    return {"agent": "plc-instruction-quality",
            "action": "FIX", "category": "bug", "critical": False,
            "file": ".claude/plans/p.md:3", "description": "defect", "rationale": "reason",
            "evidence": [{"file": ".claude/CLAUDE.md:1", "quote": "@CLAUDE.core.md"}],
            **overrides}


def seat(label, payload=None, *, mandates=None, record=None):
    return {"label": label, "mandates": mandates or ["plc-instruction-quality"],
            "payload": payload, "record": record if record is not None else {
                "exitCode": 0, "schemaValid": True, "engagement": "proven"}}


def reply(*findings, basis="plc-instruction-quality: checked source and found defects",
          tools=None, stopped="exhausted-leads"):
    return {"structured_output": {
        "checked": {"toolsUsed": tools if tools is not None else ["Read:.claude/plans/p.md"],
                    "stoppedAt": stopped, "basis": basis},
        "gaps": [], "findings": list(findings)}, "is_error": False}


class ReviewIngestionTests(unittest.TestCase):
    def test_runtime_identity_survives_ingestion_outside_findings(self):
        record = {"exitCode": 0, "schemaValid": True, "engagement": "proven", "transport": "codex",
                  "attestedModel": "runtime-reviewer", "servedModel": "client-claim", "tier": "architect",
                  "effort": "high"}
        result = review_result.ingest([seat("runtime", reply(), record=record)])
        coverage = result["coverage"][0]
        self.assertEqual(coverage["model"], "runtime-reviewer")
        self.assertTrue(coverage["modelObserved"])
        self.assertEqual(coverage["identityBasis"], "attestedModel")
        self.assertEqual((coverage["tier"], coverage["effort"]), ("architect", "high"))
        unknown = review_result.ingest([seat("unknown", reply())])["coverage"][0]
        self.assertFalse(unknown["modelObserved"])
        self.assertIsNone(unknown["model"])

    def test_missing_required_attestation_and_unread_seat_never_become_clean(self):
        missing = {"exitCode": 0, "schemaValid": True, "engagement": "proven", "transport": "codex",
                   "servedModel": "child-claim", "modelVersion": "client-version"}
        result = review_result.ingest([seat("missing", reply(), record=missing)])
        coverage = result["coverage"][0]
        self.assertFalse(coverage["modelObserved"])
        self.assertIsNone(coverage["model"])
        self.assertEqual(coverage["status"], "uncovered")
        unread = seat("unread", reply(), record=dict(missing, attestedModel="runtime"))
        unread["mustRead"] = [{"path": "src/a.cs", "lines": 4}]
        coverage = review_result.ingest([unread])["coverage"][0]
        self.assertEqual(coverage["status"], "failed")
        self.assertEqual(coverage["uncoveredMandates"], ["plc-instruction-quality"])

    def test_valid_neighbor_is_retained_when_one_finding_is_invalid(self):
        bad = finding()
        bad.pop("rationale")
        result = review_result.ingest([seat("valid", reply(finding(), bad))])
        self.assertEqual(result["counts"], {"seats": 1, "completed": 0, "partial": 1, "failed": 0,
                                             "uncovered": 0, "received": 2, "valid": 1,
                                             "rejected": 1})
        self.assertEqual([row["id"] for row in result["sources"]], ["F1"])
        self.assertEqual(result["coverage"][0]["status"], "partial")

    def test_missing_malformed_blocked_and_unengaged_are_uncovered(self):
        rows = [seat("ok", reply(finding())),
                seat("missing", None),
                seat("malformed", {"structured_output": {"findings": "wrong"}}),
                seat("blocked", reply(basis="plc-instruction-quality: blocked", stopped="blocked")),
                seat("tool-less", reply(tools=[])),
                seat("unengaged", reply(), record={"exitCode": 0, "schemaValid": True,
                                                    "engagement": "unproven"})]
        result = review_result.ingest(rows)
        self.assertEqual(result["counts"]["seats"], 6)
        self.assertEqual(result["counts"]["completed"], 1)
        self.assertEqual(result["counts"]["uncovered"], 5)
        self.assertEqual(result["counts"]["valid"], 1)
        self.assertEqual([row["id"] for row in result["sources"]], ["F1"])
        self.assertEqual({r["label"] for r in result["coverage"] if r["status"] == "uncovered"},
                         {"missing", "malformed", "blocked", "tool-less", "unengaged"})

    def test_honest_capability_gaps_without_findings_are_partial_not_uncovered(self):
        gapped = reply()
        gapped["structured_output"]["gaps"] = [{"item": "scene.tscn", "reason": "capability"}]
        tool_less = reply(tools=[])
        tool_less["structured_output"]["gaps"] = [{"item": "scene.tscn", "reason": "capability"}]
        result = review_result.ingest([seat("gapped", gapped), seat("tool-less", tool_less)])
        by = {row["label"]: row for row in result["coverage"]}
        self.assertEqual(by["gapped"]["status"], "partial")
        self.assertIn("seat reported capability gaps", by["gapped"]["issues"])
        self.assertEqual(by["tool-less"]["status"], "uncovered")
        self.assertEqual(result["counts"]["partial"], 1)
        self.assertEqual(result["counts"]["uncovered"], 1)

    def test_each_coverage_entry_carries_its_seats_legal_gaps(self):
        gap = {"item": "src/a.cs", "reason": "capability"}
        gapped = reply()
        gapped["structured_output"]["gaps"] = [gap]
        malformed = reply()
        malformed["structured_output"]["gaps"] = "not a list"
        result = review_result.ingest([seat("gapped", gapped), seat("clean", reply()), seat("malformed", malformed),
                                       seat("missing", None)])
        by = {row["label"]: (row.get("gaps", "absent"), row["status"]) for row in result["coverage"]}
        self.assertEqual(by, {"gapped": ([gap], "partial"), "clean": ([], "completed"),
                              "malformed": (None, "failed"), "missing": (None, "uncovered")})

    def test_a_capability_partial_seat_carries_its_fact_and_is_partial_with_no_finding(self):
        keys = ["plc-instruction-quality", "plc-doctrine-consistency"]

        def gapped(mapping, *findings):
            body = reply(*findings, basis="read every changed file")
            body["structured_output"]["gaps"] = [{"item": "scene.tscn", "reason": "capability"}]
            body["structured_output"]["checked"]["mandates"] = mapping
            return body
        rejected = gapped({keys[0]: "clean", keys[1]: "findings"}, finding(agent=keys[1]), {"agent": keys[1]})
        blocked = gapped({keys[0]: "clean", keys[1]: "clean"})
        blocked["structured_output"]["checked"]["stoppedAt"] = "blocked"
        result = review_result.ingest([
            seat("both", gapped({keys[0]: "clean", keys[1]: "clean"}), mandates=keys),
            seat("not-reached", gapped({keys[0]: "clean", keys[1]: "not-reached"}), mandates=keys),
            seat("rejected", rejected, mandates=keys),
            seat("blocked", blocked, mandates=keys),
            seat("no-map", gapped(None), mandates=keys),
            seat("clean", reply(), mandates=["plc-instruction-quality"])])
        by = {row["label"]: (row.get("capabilityPartial", "absent"), row["status"]) for row in result["coverage"]}
        self.assertEqual(by, {"both": (True, "partial"), "not-reached": (True, "partial"),
                              "rejected": (False, "partial"), "blocked": (False, "uncovered"),
                              "no-map": (False, "uncovered"), "clean": (False, "completed")})

    def test_seat_status_survives_reworded_issue_messages(self):
        keys = ["plc-instruction-quality", "plc-doctrine-consistency"]
        body = reply(basis="read every changed file")
        body["structured_output"]["gaps"] = [{"item": "scene.tscn", "reason": "capability"}]
        body["structured_output"]["checked"]["mandates"] = {keys[0]: "clean", keys[1]: "not-reached"}
        real = review_result._coverage

        def reworded(*args):
            checked, errors, missing = real(*args)
            return checked, ["reworded: " + error for error in errors], missing

        review_result._coverage = reworded
        try:
            row = review_result.ingest([seat("not-reached", body, mandates=keys)])["coverage"][0]
        finally:
            review_result._coverage = real
        self.assertEqual((row["capabilityPartial"], row["status"]), (True, "partial"), row["issues"])
        self.assertEqual(row["uncoveredMandates"], [keys[1]])

    def test_merged_seat_must_account_for_each_mandate(self):
        merged = seat("merged", reply(finding()),
                      mandates=["plc-instruction-quality", "plc-doctrine-consistency"])
        result = review_result.ingest([merged])
        self.assertEqual(result["coverage"][0]["status"], "partial")
        self.assertEqual(result["coverage"][0]["uncoveredMandates"], ["plc-doctrine-consistency"])
        self.assertEqual(result["counts"]["valid"], 1)
        self.assertEqual(result["sources"][0]["finding"]["description"], "defect")

    def test_merged_seat_finding_names_its_mandate_in_the_schema_agent_field(self):
        # review_findings.json has no `mandate` field; `agent` is the schema's producer key, and an
        # extra `mandate` key neither admits a finding nor rejects one.
        both = "plc-instruction-quality: findings; plc-doctrine-consistency: findings"
        rows = [finding(agent="plc-instruction-quality"), finding(agent="plc-doctrine-consistency"),
                finding(agent="plc-pattern-fit")]
        result = review_result.ingest([seat(
            "merged", reply(*rows, basis=both),
            mandates=["plc-instruction-quality", "plc-doctrine-consistency"])])
        self.assertEqual(result["counts"]["valid"], 3)
        self.assertEqual(result["counts"]["rejected"], 0)
        self.assertEqual(result["coverage"][0]["uncoveredMandates"], [])
        self.assertEqual(result["coverage"][0]["status"], "completed")
        self.assertEqual(result["coverage"][0]["issues"], ["finding 2 names no mandate of its seat"])

    def test_a_stray_mandate_key_does_not_decide_a_finding(self):
        rows = [seat("merged", reply(finding(agent="plc-instruction-quality",
                                             mandate="plc-elsewhere"),
                                     basis="plc-instruction-quality: findings; "
                                            "plc-doctrine-consistency: findings"),
                     mandates=["plc-instruction-quality", "plc-doctrine-consistency"])]
        result = review_result.ingest(rows)
        self.assertEqual(result["counts"]["valid"], 1)

    def test_a_padded_file_location_is_read_the_same_way_as_a_bare_one(self):
        result = review_result.ingest([seat("padded", reply(finding(file="  .claude/plans/p.md:3  ")))])
        self.assertEqual(result["counts"]["valid"], 1)
        self.assertEqual(result["sources"][0]["finding"]["file"], "  .claude/plans/p.md:3  ")

    def test_single_mandate_seat_basis_needs_no_key_clause(self):
        # orchestration §2 asks a MERGED seat to name each mandate; a solo seat's basis is its mandate's.
        solo = review_result.ingest([seat("solo", reply(finding(), basis="read all 37 hunks"))])
        self.assertEqual(solo["coverage"][0]["status"], "completed")
        self.assertEqual(solo["coverage"][0]["uncoveredMandates"], [])
        blocked = review_result.ingest([seat("solo", reply(finding(), basis="not reached: timeout"))])
        self.assertEqual(blocked["coverage"][0]["uncoveredMandates"], ["plc-instruction-quality"])

    def test_a_typed_mandate_map_decides_each_mandate_before_the_basis_text(self):
        keys = ["plc-instruction-quality", "plc-doctrine-consistency"]
        body = reply(finding(), basis="read every changed file")
        checked = body["structured_output"]["checked"]
        checked["mandates"] = {keys[0]: "findings", keys[1]: "clean"}
        both = review_result.ingest([seat("merged", body, mandates=keys)])["coverage"][0]
        self.assertEqual((both["status"], both["uncoveredMandates"]), ("completed", []))
        checked["mandates"] = {keys[0]: "findings", keys[1]: "not-reached"}
        one = review_result.ingest([seat("merged", body, mandates=keys)])["coverage"][0]
        self.assertEqual(one["uncoveredMandates"], [keys[1]])
        checked["mandates"] = {keys[0]: "findings"}
        void = review_result.ingest([seat("merged", body, mandates=keys)])["coverage"][0]
        self.assertEqual(void["uncoveredMandates"], keys, "a map missing a key is void; the basis text decides")

    def test_explicit_not_reached_mandate_is_uncovered(self):
        result = review_result.ingest([seat(
            "merged", reply(finding(), basis=(
                "plc-instruction-quality: findings; plc-doctrine-consistency: not reached")),
            mandates=["plc-instruction-quality", "plc-doctrine-consistency"])])
        self.assertEqual(result["coverage"][0]["status"], "partial")
        self.assertEqual(result["coverage"][0]["uncoveredMandates"], ["plc-doctrine-consistency"])

    def test_failed_or_unengaged_record_cannot_launder_findings(self):
        rows = [seat("failed", reply(finding()), record={
                    "exitCode": 1, "schemaValid": False, "engagement": "proven"}),
                seat("no-schema", reply(finding()), record={"exitCode": 0}),
                seat("unengaged", reply(finding()), record={
                    "exitCode": 0, "schemaValid": True, "engagement": "unproven"})]
        result = review_result.ingest(rows)
        self.assertEqual(result["counts"]["uncovered"], 3)
        self.assertEqual(result["counts"]["received"], 3)
        self.assertEqual(result["counts"]["valid"], 0)
        self.assertEqual(result["counts"]["rejected"], 3)
        self.assertEqual(result["sources"], [])

    def test_jsonl_loader_uses_only_the_terminal_result(self):
        self.assertTrue(hasattr(review_result, "read_payload"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seat.json.out"
            path.write_text("\n".join(json.dumps(row) for row in (
                {"type": "system", "subtype": "hook_started"},
                {"type": "result", "is_error": False, "structured_output": reply(finding())["structured_output"]},
            )) + "\n", encoding="utf-8")
            payload = review_result.read_payload(path)
            self.assertEqual(payload["structured_output"]["findings"][0]["description"], "defect")

    def test_s0_non_reads_never_receive_read_or_review_engagement_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = Path(directory) / "progress.jsonl"
            target = Path(directory) / "context.md"
            target.write_text("source", encoding="utf-8")
            cases = [
                ("Write", {"file_path": str(target), "content": "x"}),
                ("Edit", {"file_path": str(target), "old_string": "source", "new_string": "x"}),
                ("Bash", {"command": f"echo x > '{target}'"}),
                ("Bash", {"command": f"printf '%s' '{target}'"}),
                ("Bash", {"command": f"test -f '{target}'"}),
                ("Bash", {"command": f"unknown-reader '{target}'"}),
                ("Bash", {"command": f"cat '{target}' > /dev/null"}),
                ("Bash", {"command": f"printf 'cat {target}'"}),
                ("Grep", {"path": str(target), "pattern": "source"}),
                ("Glob", {"path": str(target), "pattern": "*"}),
            ]
            for tool, given in cases:
                with self.subTest(tool=tool, given=given):
                    stream.write_text("".join(json.dumps(e) + "\n" for e in captured_events(tool, given)),
                                      encoding="utf-8")
                    self.assertEqual(review_result.called_reads(stream), [])
                    self.assertFalse(review_result.prove_engagement(stream, [str(target)]))
                    if "command" in given:
                        self.assertEqual(review_result.target_spans(f"{tool}:{given['command']}"), [])
            stream.write_text(json.dumps(captured_mention(str(target))) + "\n", encoding="utf-8")
            self.assertFalse(review_result.prove_engagement(stream, [str(target)]))

    def test_s0_reads_use_the_paired_result_not_nearby_errors_or_mentions(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = Path(directory) / "progress.jsonl"
            target = Path(directory) / "context.md"
            target.write_text("source", encoding="utf-8")
            for tool, given in (("Read", {"file_path": str(target)}),
                                ("Bash", {"command": f"cat '{target}'"}),
                                ("PowerShell", {"command": f"Get-Content '{target}'"})):
                with self.subTest(tool=tool):
                    failed = captured_events(tool, given, error=True, cid="failed")
                    ok = captured_events(tool, given, cid="ok")
                    unrelated = captured_events("Read", {"file_path": "elsewhere.md"}, error=True, cid="other")
                    events = failed + [captured_mention(str(target))]
                    stream.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
                    self.assertEqual(review_result.called_reads(stream), [])
                    self.assertFalse(review_result.prove_engagement(stream, [str(target)]))
                    for events in (ok + unrelated, failed + ok + unrelated):
                        stream.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
                        self.assertEqual([p for p, _ in review_result.called_reads(stream)], [str(target).replace('\\', '/')])
                        self.assertTrue(review_result.prove_engagement(stream, [str(target)]))

    def test_s0_external_msys_paths_are_the_same_required_file(self):
        top = 'C:/arms/t2-opus-r1'
        required = 'C:/vault/task_input.md'
        self.assertEqual(review_result.target_path('Read:/c/vault/task_input.md', top), required)

    def test_s0_shell_escaping_preserves_the_read_operand(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = Path(directory) / 'progress.jsonl'
            stream.write_text(''.join(json.dumps(e) + '\n' for e in captured_events(
                'Bash', {'command': r'cat src/file\ name.md'})), encoding='utf-8')
            self.assertEqual(review_result.called_reads(stream), [('src/file name.md', [review_result.WHOLE])])

    def test_s0_successful_and_chains_are_reads(self):
        top = 'C:/arms/t2-opus-r1'
        with tempfile.TemporaryDirectory() as directory:
            stream = Path(directory) / 'progress.jsonl'
            command = f'cd "{top}" && cat NPCs/Enemy.cs && echo "=== next ===" && cat NPCs/Entity.cs'
            stream.write_text(''.join(json.dumps(e) + '\n' for e in captured_events('Bash', {'command': command})),
                              encoding='utf-8')
            self.assertEqual(review_result.called_reads(stream, toplevel=top),
                             [('NPCs/Enemy.cs', [review_result.WHOLE]), ('NPCs/Entity.cs', [review_result.WHOLE])])
            self.assertEqual(review_result.target_path(f'Bash:{command}', top), 'NPCs/Entity.cs')
            self.assertEqual(review_result.target_spans(f'Bash:{command}'), [review_result.WHOLE])

    def test_sed_line_ranges_and_head_counts_are_reads_of_their_lines_only(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = Path(directory) / 'progress.jsonl'
            target = Path(directory) / 'context.md'
            target.write_text('source', encoding='utf-8')
            path = str(target).replace('\\', '/')
            reads = {
                f"sed -n '2,4p' '{target}'": [(2, 4)],
                f"sed -n 7p '{target}'": [(7, 7)],
                f"head -n 3 '{target}'": [(1, 3)],
                f"head -3 '{target}'": [(1, 3)],
            }
            for command, spans in reads.items():
                with self.subTest(command=command):
                    stream.write_text(''.join(json.dumps(e) + '\n' for e in captured_events(
                        'Bash', {'command': command})), encoding='utf-8')
                    self.assertEqual(review_result.called_reads(stream), [(path, spans)])
                    self.assertTrue(review_result.prove_engagement(stream, [str(target)]))
            refused = [f"sed -i 's/a/b/' '{target}'", f"sed 's/a/b/' '{target}'",
                       f"sed -n '1,3p' '{target}' | cat", f"sed -n '1,3p' '{target}' > out.md",
                       f"head -n 3 '{target}' other.md", f"head -c 3 '{target}'", f"head -n 0 '{target}'"]
            for command in refused:
                with self.subTest(command=command):
                    stream.write_text(''.join(json.dumps(e) + '\n' for e in captured_events(
                        'Bash', {'command': command})), encoding='utf-8')
                    self.assertEqual(review_result.called_reads(stream), [])
                    self.assertFalse(review_result.prove_engagement(stream, [str(target)]))

    def test_engagement_requires_successful_read_of_each_context_file(self):
        self.assertTrue(hasattr(review_result, "prove_engagement"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            target = Path(directory) / "context.md"
            target.write_text("source", encoding="utf-8")
            started, success = captured_events("Read", {"file_path": str(target)}, cid="call_1")
            path.write_text(json.dumps(started) + "\n" + json.dumps(success) + "\n",
                            encoding="utf-8")
            self.assertTrue(review_result.prove_engagement(path, [str(target)]))
            self.assertFalse(review_result.prove_engagement(path, [str(target), "other.md"]))
            failed = dict(success)
            failed["message"] = {"content": [{"type": "tool_result", "tool_use_id": "call_1",
                                              "content": "File does not exist", "is_error": True}]}
            path.write_text(json.dumps(started) + "\n" + json.dumps(failed) + "\n",
                            encoding="utf-8")
            self.assertFalse(review_result.prove_engagement(path, [str(target)]))
            self.assertFalse(review_result.prove_engagement(path, []))

    def test_engagement_counts_each_successful_call_the_ledger_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            target = Path(directory) / "context.md"
            target.write_text("source", encoding="utf-8")
            for tool, given, is_error, proven in (
                    ("Bash", {"command": f"cat '{target}'"}, False, True),
                    ("PowerShell", {"command": f"Get-Content '{target}'"}, False, True),
                    ("Bash", {"command": f"cat '{target}'"}, True, False),
                    ("Write", {"file_path": str(target)}, False, False)):
                with self.subTest(tool=tool, is_error=is_error):
                    events = captured_events(tool, given, error=is_error, cid="call_1")
                    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
                    self.assertIs(review_result.prove_engagement(path, [str(target)]), proven)

    def test_engagement_follows_a_bounded_stream_pointer_to_its_sibling(self):
        # sidecar_fanout.bound_finished_stream replaces a finished stream with a seeAlso pointer.
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "context.md"
            target.write_text("source", encoding="utf-8")
            events = captured_events("Read", {"file_path": str(target)}, cid="call_1")
            (Path(directory) / "seat.out.json").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
            stream = Path(directory) / "seat.stream"
            stream.write_text(json.dumps({"seeAlso": "seat.out.json", "note": "bounded"}),
                              encoding="utf-8")
            self.assertTrue(review_result.prove_engagement(stream, [str(target)]))
            stream.write_text(json.dumps({"seeAlso": "../outside.json"}), encoding="utf-8")
            self.assertFalse(review_result.prove_engagement(stream, [str(target)]))

    def test_engagement_needs_the_exact_repo_anchored_path_not_a_basename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            required = str(ROOT / ".claude/CLAUDE.md")

            def stream(read_path):
                started, success = captured_events("Read", {"file_path": read_path}, cid="call_1")
                path.write_text(json.dumps(started) + "\n" + json.dumps(success) + "\n",
                                encoding="utf-8")
                return review_result.prove_engagement(path, [required])

            self.assertFalse(stream("CLAUDE.md"))
            self.assertFalse(stream(str(ROOT / "CLAUDE.md")))
            self.assertFalse(stream("other/.claude/CLAUDE.md"))
            self.assertTrue(stream(".claude/CLAUDE.md"))
            self.assertTrue(stream("./.claude/../.claude/CLAUDE.md"))
            self.assertTrue(stream(required.replace("\\", "/")))

    def test_review_jobs_require_explicit_mandates(self):
        self.assertTrue(hasattr(review_result, "validate_review_jobs"))
        with self.assertRaises(ValueError):
            review_result.validate_review_jobs([{"label": "one", "shape": "review"}])
        with self.assertRaises(ValueError):
            review_result.validate_review_jobs([{"label": "one", "shape": "survey",
                                                "mandates": ["plc-instruction-quality"]}])
        with self.assertRaises(ValueError):
            review_result.validate_review_jobs([{"label": "one", "shape": "review",
                                                "mandates": ["plc-instruction-quality"]}])
        review_result.validate_review_jobs([{"label": "one", "shape": "review",
                                            "mandates": ["plc-instruction-quality"],
                                            "mustRead": [{"path": str(Path(__file__)), "lines": 1}],
                                            "contextFiles": [str(Path(__file__))]}])

    def test_consolidation_cannot_trust_self_reported_tools_without_read_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = ROOT / ".claude/CLAUDE.md"
            output = root / "one.out.json"
            output.write_text(json.dumps({"type": "result", "is_error": False,
                                          "structured_output": reply(finding())["structured_output"]}) + "\n",
                              encoding="utf-8")
            record = root / "one.record.json"
            record.write_text(json.dumps({"exitCode": 0, "schemaValid": True}), encoding="utf-8")
            stream = root / "one.stream"
            stream.write_text(json.dumps({"type": "result"}) + "\n", encoding="utf-8")
            jobs = [{"label": "one", "shape": "review", "mandates": ["plc-instruction-quality"],
                     "mustRead": [{"path": ".claude/plans/p.md", "lines": 3}],
                     "contextFiles": [str(context)]}]
            results = {"one": {"exit": 0, "stdout": str(output), "record": str(record),
                               "stream": str(stream)}}
            review = review_result.consolidate(jobs, results, root)
            self.assertEqual(review["status"], "partial")
            self.assertEqual(review["counts"]["uncovered"], 1)
            self.assertEqual(review["sources"], [])

    MUST = [{"path": "src/a.cs", "lines": 437}, {"path": "src/Core/ImpactPhysics.cs", "lines": 900}]

    def _listed(self, label, gaps=(), tools=("Read:src/a.cs:1-437",), must=None, **more):
        body = reply(finding(), tools=list(tools))
        body["structured_output"]["gaps"] = list(gaps)
        row = seat(label, body)
        row["mustRead"] = [dict(entry) for entry in (self.MUST if must is None else must)]
        row.update(more)
        return row

    def test_an_unread_must_read_entry_with_no_legal_gap_fails_the_seat(self):
        result = review_result.ingest([self._listed("skipped")])
        entry = result["coverage"][0]
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["unread"], ["src/Core/ImpactPhysics.cs"])
        self.assertEqual(entry["uncoveredMandates"], ["plc-instruction-quality"])
        self.assertTrue(any("ImpactPhysics.cs" in issue and "exhausted-leads" in issue for issue in entry["issues"]),
                        entry["issues"])
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["counts"]["valid"], 1, "the failed seat's findings stay sources")

    def test_a_must_read_entry_read_only_in_part_fails_the_seat(self):
        part = self._listed("part", tools=("Read:src/a.cs:1-437", "Read:src/Core/ImpactPhysics.cs:1-437"))
        entry = review_result.ingest([part])["coverage"][0]
        self.assertEqual((entry["status"], entry["unread"]), ("failed", ["src/Core/ImpactPhysics.cs"]))
        self.assertTrue(any("ImpactPhysics.cs" in issue and "1-437 of 900" in issue for issue in entry["issues"]),
                        entry["issues"])

    def test_ranges_that_together_cover_a_must_read_entry_pass_the_seat(self):
        big = [{"path": "src/a.cs", "lines": 437}, {"path": "src/Big.cs", "lines": 2600}]
        rows = [self._listed("halves", tools=("Read:src/a.cs:1-437", "Read:src/Core/ImpactPhysics.cs:438-900",
                                               "Read:src/Core/ImpactPhysics.cs:1-437")),
                self._listed("comma", tools=("Read:src/a.cs:1-437", "Read:src/Core/ImpactPhysics.cs:1-500,450-900")),
                self._listed("absolute", tools=("Read:C:/repo/src/a.cs:1-200", "Read:src/a.cs:201-437",
                                                 "Read:src/Core/ImpactPhysics.cs")),
                self._listed("paged", tools=("Read:src/a.cs", "Read:src/Big.cs:1-2000", "Read:src/Big.cs:2001-2600"),
                             must=big),
                self._listed("window", tools=("Read:src/a.cs", "Read:src/Big.cs"), must=big),
                self._listed("hole", tools=("Read:src/a.cs:1-437", "Read:src/Core/ImpactPhysics.cs:1-400",
                                             "Read:src/Core/ImpactPhysics.cs:402-900"))]
        by = {row["label"]: row["status"] for row in review_result.ingest(rows)["coverage"]}
        self.assertEqual(by, {"halves": "completed", "comma": "completed", "absolute": "completed",
                              "paged": "completed", "window": "failed", "hole": "failed"})

    def test_a_shell_read_covers_an_entry_and_a_search_does_not(self):
        rows = [self._listed("shell", tools=("Read:src/a.cs:1-437", "Bash:cat src/Core/ImpactPhysics.cs")),
                self._listed("diff", tools=("Read:src/a.cs:1-437", "Bash:git diff -- src/Core/ImpactPhysics.cs")),
                self._listed("search", tools=("Read:src/a.cs:1-437", "Grep:src/Core/ImpactPhysics.cs"))]
        by = {row["label"]: row["status"] for row in review_result.ingest(rows)["coverage"]}
        self.assertEqual(by, {"shell": "completed", "diff": "failed", "search": "failed"})

    def test_an_empty_or_absent_must_read_file_needs_one_read_or_a_gap(self):
        empty = [{"path": "src/a.cs", "lines": 437}, {"path": "src/empty.cs", "lines": 0}]
        rows = [self._listed("opened", tools=("Read:src/a.cs:1-437", "Read:src/empty.cs"), must=empty),
                self._listed("never", must=empty)]
        by = {row["label"]: row["status"] for row in review_result.ingest(rows)["coverage"]}
        self.assertEqual(by, {"opened": "completed", "never": "failed"})

    def test_observed_reads_decide_a_must_read_entry_over_the_seats_own_tool_list(self):
        claimed = self._listed("claimed", tools=("Read:src/a.cs", "Read:src/Core/ImpactPhysics.cs"),
                               reads=[("src/a.cs", [(1, 2000)]), ("src/Core/ImpactPhysics.cs", [(1, 437)])])
        observed = self._listed("observed", tools=("Read:src/a.cs:1-10",),
                                reads=[("src/a.cs", [(1, 2000)]), ("src/Core/ImpactPhysics.cs", [(1, 2000)])])
        by = {row["label"]: row for row in review_result.ingest([claimed, observed])["coverage"]}
        self.assertEqual((by["claimed"]["status"], by["claimed"]["unread"]), ("failed", ["src/Core/ImpactPhysics.cs"]))
        self.assertEqual(by["observed"]["status"], "completed")

    def test_outside_lens_missing_and_capability_gaps_keep_the_seat_ok(self):
        rows = [self._listed(reason, gaps=[{"item": "src/Core/ImpactPhysics.cs", "reason": reason}])
                for reason in ("outside-lens", "missing", "capability")]
        by = {row["label"]: row for row in review_result.ingest(rows)["coverage"]}
        self.assertEqual(by["outside-lens"]["status"], "completed")
        self.assertEqual(by["missing"]["status"], "completed")
        self.assertEqual(by["capability"]["status"], "partial")
        self.assertEqual(by["outside-lens"]["unread"], [])
        self.assertEqual(by["outside-lens"]["gaps"], [{"item": "src/Core/ImpactPhysics.cs", "reason": "outside-lens"}])

    def test_a_gap_that_excuses_no_listed_entry_is_a_note_on_a_seat_that_read_its_list(self):
        read_both = ("Read:src/a.cs", "Read:src/Core/ImpactPhysics.cs")
        rows = [self._listed("text", gaps=["could not trace callers"], tools=read_both),
                self._listed("unlisted", gaps=[{"item": "src/other.cs", "reason": "capability"}], tools=read_both),
                self._listed("reason", gaps=[{"item": "src/a.cs", "reason": "ran out of time"}], tools=read_both),
                self._listed("extra", gaps=[{"item": "src/a.cs", "reason": "capability", "note": "x"}], tools=read_both),
                self._listed("malformed", gaps="not a list", tools=read_both)]
        by = {row["label"]: row for row in review_result.ingest(rows)["coverage"]}
        self.assertEqual({label: row["status"] for label, row in by.items()}, {label: "completed" for label in by})
        self.assertEqual(by["unlisted"]["gapNotes"], [{"item": "src/other.cs", "reason": "capability"}])
        self.assertEqual(by["unlisted"]["gaps"], [])

    def test_a_gap_that_excuses_nothing_still_fails_an_unread_entry_or_a_seat_with_no_list(self):
        rows = [self._listed("unread", gaps=[{"item": "src/other.cs", "reason": "outside-lens"}],
                             tools=("Read:src/a.cs",))]
        bare = reply(finding())
        bare["structured_output"]["gaps"] = ["skimmed the rest"]
        rows.append(seat("bare", bare))
        by = {row["label"]: row["status"] for row in review_result.ingest(rows)["coverage"]}
        self.assertEqual(by, {"unread": "failed", "bare": "failed"})

    def _stream_review(self, root, reads):
        """`prepare_review` over one job whose stream shows `reads`: (file_path, offset, limit) Read calls, None
        for an omitted field."""
        context = ROOT / ".claude/CLAUDE.md"
        calls = [("c0", {"file_path": str(context)})]
        for n, (path, offset, limit) in enumerate(reads, 1):
            given = {"file_path": path}
            if offset is not None:
                given["offset"] = offset
            if limit is not None:
                given["limit"] = limit
            calls.append((f"c{n}", given))
        pairs = [captured_events("Read", given, cid=cid) for cid, given in calls]
        events = [pair[0] for pair in pairs] + [pair[1] for pair in pairs]
        stream = root / "one.stream"
        stream.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        body = reply(finding(), tools=["Read:src/a.cs", "Read:src/Core/ImpactPhysics.cs"])["structured_output"]
        output = root / "one.out.json"
        output.write_text(json.dumps({"type": "result", "is_error": False, "structured_output": body}) + "\n",
                          encoding="utf-8")
        record = root / "one.record.json"
        record.write_text(json.dumps({"exitCode": 0, "schemaValid": True}), encoding="utf-8")
        jobs = [{"label": "one", "shape": "review", "mandates": ["plc-instruction-quality"],
                 "contextFiles": [str(context)], "mustRead": [dict(entry) for entry in self.MUST]}]
        results = {"one": {"exit": 0, "stdout": str(output), "record": str(record), "stream": str(stream)}}
        return review_result.prepare_review(jobs, results)["coverage"][0]

    def test_prepare_review_judges_a_jobs_must_read_list_against_its_streams_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = self._stream_review(Path(directory), [("src/a.cs", None, None)])
            self.assertEqual((entry["status"], entry["unread"]), ("failed", ["src/Core/ImpactPhysics.cs"]))

    def test_prepare_review_judges_observed_read_offsets_against_the_entrys_line_count(self):
        cases = (("a read through line 437 only", [("src/a.cs", None, None), ("src/Core/ImpactPhysics.cs", 1, 437)],
                  "failed"),
                 ("two reads that together cover the file", [("src/a.cs", None, None),
                                                            ("src/Core/ImpactPhysics.cs", 1, 437),
                                                            ("src/Core/ImpactPhysics.cs", 438, 463)], "completed"),
                 ("one read with no limit takes the default window", [("src/a.cs", None, None),
                                                                     ("src/Core/ImpactPhysics.cs", None, None)],
                  "completed"),
                 ("an offset read with no limit runs the default window from its offset",
                  [("src/a.cs", None, None), ("src/Core/ImpactPhysics.cs", None, 437),
                   ("src/Core/ImpactPhysics.cs", 438, None)], "completed"))
        for name, reads, status in cases:
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(self._stream_review(Path(directory), reads)["status"], status)

    def test_review_jobs_refuse_a_malformed_must_read_list(self):
        job = {"label": "one", "shape": "review", "mandates": ["plc-instruction-quality"],
               "contextFiles": [str(Path(__file__))]}
        review_result.validate_review_jobs([dict(job, mustRead=[{"path": "src/a.cs", "lines": 3}])])
        review_result.validate_review_jobs([dict(job, mustRead=[{"path": "src/new.cs", "lines": 0}])])
        for bad in ("src/a.cs", [], ["src/a.cs"], [{"path": " ", "lines": 3}], [{"path": "src/a.cs"}],
                    [{"path": "src/a.cs", "lines": -1}], [{"path": "src/a.cs", "lines": True}],
                    [{"path": "src/a.cs", "lines": "3"}], [{"path": "src/a.cs", "lines": 3, "note": "x"}], [7]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                review_result.validate_review_jobs([dict(job, mustRead=bad)])

    def test_merge_groups_only_same_file_same_claim_and_keeps_originals(self):
        self.assertTrue(hasattr(review_result, "merge_view"))
        one = finding(description="same defect", file="src/shared.py:1",
                      evidence=[{"file": "src/a.py:1", "quote": "first quote"}])
        two = finding(description="same defect", file="src/shared.py:9",
                      evidence=[{"file": "src/b.py:2", "quote": "second quote"}])
        other = finding(description="different defect", file="src/shared.py:14")
        sources = [{"id": "F1", "seat": "one", "finding": one},
                   {"id": "F2", "seat": "two", "finding": two},
                   {"id": "F3", "seat": "two", "finding": other}]
        original = json.loads(json.dumps(sources))
        grouped = review_result.merge_view(sources, {"findings": [
            {"merged_from": ["F1", "F2"]}, {"merged_from": ["F3"]}]})
        self.assertEqual(sources, original)
        self.assertEqual(grouped["issues"], [])
        self.assertEqual([item["id"] for item in grouped["view"]], ["M1", "F3"])
        self.assertEqual(grouped["view"][0]["merged_from"], ["F1", "F2"])
        self.assertEqual(len(grouped["view"][0]["evidence"]), 2)
        self.assertEqual(grouped["view"][1]["description"], "different defect")

    def test_invalid_merge_partition_returns_every_source_unmerged(self):
        self.assertTrue(hasattr(review_result, "merge_view"))
        sources = [{"id": "F1", "seat": "one", "finding": finding(file="src/x.py:1",
                                                                      description="one")},
                   {"id": "F2", "seat": "two", "finding": finding(file="src/x.py:2",
                                                                      description="two")}]
        for bad in ({"findings": [{"merged_from": ["F1", "F2"]}]},
                    {"findings": [{"merged_from": ["F1"]}]},
                    {"findings": [{"merged_from": ["F1", "F1"]}, {"merged_from": ["F2"]}]}):
            with self.subTest(bad=bad):
                result = review_result.merge_view(sources, bad)
                self.assertTrue(result["issues"])
                self.assertEqual([row["id"] for row in result["view"]], ["F1", "F2"])

    def test_exact_groups_join_only_same_file_and_normalized_claim(self):
        self.assertTrue(hasattr(review_result, "exact_groups"))
        sources = [{"id": "F1", "seat": "one", "finding": finding(description="Same  defect",
                                                                      file="src/s.py:1")},
                   {"id": "F2", "seat": "two", "finding": finding(description="same defect",
                                                                      file="src/s.py:9")},
                   {"id": "F3", "seat": "two", "finding": finding(description="reworded defect",
                                                                      file="src/s.py:4")},
                   {"id": "F4", "seat": "two", "finding": finding(description="same defect",
                                                                      file="src/other.py:1")}]
        groups = review_result.exact_groups(sources)
        self.assertEqual(groups, {"findings": [{"merged_from": ["F1", "F2"]},
                                               {"merged_from": ["F3"]}]})
        merged = review_result.merge_view(sources, groups)
        self.assertEqual(merged["issues"], [])
        self.assertEqual(sorted(row["id"] for row in merged["view"]), ["F3", "F4", "M1"])

    def test_consolidation_without_supplied_groups_marks_exact_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs, results = [], {}
            for label, text in (("one", "same claim"), ("two", "Same claim")):
                output = root / f"{label}.out.json"
                output.write_text(json.dumps({"type": "result", "is_error": False,
                                              "structured_output": reply(finding(
                                                  description=text))["structured_output"]}) + "\n",
                                  encoding="utf-8")
                record = root / f"{label}.record.json"
                record.write_text(json.dumps({"exitCode": 0, "schemaValid": True,
                                              "engagement": "proven"}), encoding="utf-8")
                jobs.append({"label": label, "shape": "review",
                             "mandates": ["plc-instruction-quality"],
                             "mustRead": [{"path": ".claude/plans/p.md", "lines": 3}],
                             "contextFiles": [str(ROOT / ".claude/CLAUDE.md")]})
                results[label] = {"exit": 0, "stdout": str(output), "record": str(record)}
            review = review_result.consolidate(jobs, results, root)
            self.assertEqual(review["status"], "complete")
            self.assertEqual(review["merge"]["mode"], "exact")
            self.assertEqual([row["id"] for row in review["view"]], ["M1"])
            self.assertEqual([row["id"] for row in review["sources"]], ["F1", "F2"])

    def test_a_merged_group_takes_its_most_restrictive_decider(self):
        for actions, expected in ((("FIX", "ASK"), "ASK"), (("FIX", "PLAN"), "PLAN"),
                                  (("PLAN", "ASK"), "ASK"), (("FIX", "FIX"), "FIX")):
            with self.subTest(actions=actions):
                sources = [{"id": f"F{i}", "seat": seat_name, "finding": finding(
                    description="same defect", file=f"src/s.py:{i}", action=action)}
                           for i, (seat_name, action) in enumerate(zip(("one", "two"), actions), 1)]
                merged = review_result.merge_view(sources, {"findings": [{"merged_from": ["F1", "F2"]}]})
                self.assertEqual(merged["issues"], [])
                self.assertEqual(merged["view"][0]["action"], expected)

    def test_a_merged_ask_keeps_every_member_question_and_its_ask_options(self):
        rows = [("one", finding(action="FIX", old="a", new="b")),
                ("two", finding(action="ASK", question="Keep the file?",
                                options=["Keep it", "Retire it; wins when unused"])),
                ("two", finding(action="PLAN", question=" ", scope=["a.py"],
                                options=["Plan only; wins when never"])),
                ("one", finding(action="ASK", question="Who owns it?",
                                options=["Retire it; wins when unused", "Move it; wins when shared"])),
                ("two", finding(action="ASK", question="Keep the file?", options=["Keep it"]))]
        sources = [{"id": f"F{i}", "seat": seat_name,
                    "finding": dict(row, description="same defect", file=f"src/s.py:{i}")}
                   for i, (seat_name, row) in enumerate(rows, 1)]
        merged = review_result.merge_view(
            sources, {"findings": [{"merged_from": ["F1", "F2", "F3", "F4", "F5"]}]})
        self.assertEqual(merged["issues"], [])
        row = merged["view"][0]
        self.assertEqual(row["action"], "ASK")
        self.assertEqual(row["question"], "Keep the file? / Who owns it?")
        self.assertEqual(row["options"], ["Keep it", "Retire it; wins when unused",
                                          "Move it; wins when shared"])

    def test_a_merged_open_plan_outranks_a_resolved_plan_and_carries_its_decision(self):
        open_plan = finding(action="PLAN", question="Which design?",
                            options=["Design A", "Design B; wins when shared"], scope=["open.py"])
        resolved = finding(action="PLAN", question=None, options=["Resolved plan"], scope=["resolved.py"])
        for rows, expected in (
                ([("one", finding(action="FIX", old="a", new="b")), ("one", resolved), ("two", open_plan)],
                 ("PLAN", "Which design?", ["Design A", "Design B; wins when shared"], ["open.py"])),
                ([("one", finding(action="FIX", old="a", new="b")), ("two", resolved)],
                 ("PLAN", None, ["Resolved plan"], ["resolved.py"]))):
            with self.subTest(actions=[row["action"] for _, row in rows]):
                sources = [{"id": f"F{i}", "seat": seat_name,
                            "finding": dict(row, description="same defect", file=f"src/s.py:{i}")}
                           for i, (seat_name, row) in enumerate(rows, 1)]
                merged = review_result.merge_view(
                    sources, {"findings": [{"merged_from": [source["id"] for source in sources]}]})
                self.assertEqual(merged["issues"], [])
                row = merged["view"][0]
                self.assertEqual((row["action"], row.get("question"), row.get("options"), row.get("scope")),
                                 expected)

    def test_tier_shape_names_each_violated_predicate_with_its_ids(self):
        rows = [finding(action="ASK", question=None, options=["A"]),
                finding(action="ASK", question="Owner intent?", options=["A (Recommended)"]),
                finding(action="PLAN", options=["A", "B"], scope=["a.py"]),
                finding(action="PLAN", options=["A"], scope=None),
                finding(action="FIX", old=None, new=None)]
        sources = [{"id": f"F{i}", "seat": "one", "finding": row} for i, row in enumerate(rows, 1)]
        self.assertEqual(review_result.tier_shape(sources), {
            "ask-null-question": ["F1"], "ask-recommended": ["F2"],
            "alternative-without-wins-when": ["F3"], "plan-no-scope": ["F4"], "fix-null-edit": ["F5"]})

    def test_tier_shape_is_silent_on_compliant_findings(self):
        rows = [finding(action="FIX", old="before", new="after"),
                finding(action="ASK", question="Is this still the owner's file?",
                        options=["Keep it", "Retire it; wins when the owner stopped authoring it"]),
                finding(action="PLAN", question=None, options=["Do A", "Alternative: B; wins when C"],
                        scope=["a.py"])]
        sources = [{"id": f"F{i}", "seat": "one", "finding": row} for i, row in enumerate(rows, 1)]
        self.assertEqual(review_result.tier_shape(sources), {})

    def test_consolidation_reports_tier_shape_as_an_advisory_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "one.out.json"
            output.write_text(json.dumps({"type": "result", "is_error": False, "structured_output": reply(
                finding(old=None, new=None, evidence=[]))["structured_output"]}) + "\n", encoding="utf-8")
            record = root / "one.record.json"
            record.write_text(json.dumps({"exitCode": 0, "schemaValid": True, "engagement": "proven"}),
                              encoding="utf-8")
            jobs = [{"label": "one", "shape": "review", "mandates": ["plc-instruction-quality"],
                     "mustRead": [{"path": ".claude/plans/p.md", "lines": 3}],
                     "contextFiles": [str(ROOT / ".claude/CLAUDE.md")]}]
            review = review_result.consolidate(jobs, {"one": {"exit": 0, "stdout": str(output),
                                                              "record": str(record)}}, root)
            self.assertEqual(review["status"], "complete")
            self.assertEqual(review["errors"], [])
            self.assertEqual(review["flags"], [{"kind": "tier-shape",
                                                "detail": "fix-null-edit: 1 finding (F1)"}])

    def test_valid_two_seats_keep_distinct_source_ids_at_one_location(self):
        rows = [seat("one", reply(finding(description="A"))),
                seat("two", reply(finding(description="B", agent="plc-doctrine-consistency",
                                          mandate="plc-doctrine-consistency")),
                     mandates=["plc-doctrine-consistency"])]
        result = review_result.ingest(rows)
        self.assertEqual(result["counts"]["received"], 2)
        self.assertEqual(result["counts"]["valid"], 2)
        self.assertEqual([r["id"] for r in result["sources"]], ["F1", "F2"])
        self.assertEqual([r["finding"]["description"] for r in result["sources"]], ["A", "B"])


class EngagementPathTests(unittest.TestCase):
    """The launcher embeds a review job's contextFiles in its prompt, so a seat never Reads them; a job with a
    must-read list proves engagement by reading those entries instead."""

    def _stream(self, root, paths):
        stream = root / "s.stream"
        rows = []
        for n, path in enumerate(paths):
            rows.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t%d" % n, "name": "Read", "input": {"file_path": path}}]}})
            rows.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t%d" % n, "content": "1\tx"}]}})
        stream.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return stream

    def test_a_seat_that_read_its_must_read_diff_but_not_the_embedded_index_engaged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.md"
            index.write_text("x", encoding="utf-8")
            job = {"contextFiles": [str(index)], "mustRead": [{"path": "a.diff", "lines": 9}]}
            self.assertTrue(review_result.engaged(job, self._stream(root, ["a.diff"])))
            self.assertFalse(review_result.engaged(job, self._stream(root, [])))

    def test_without_a_must_read_list_every_context_file_must_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = root / "c.md"
            context.write_text("x", encoding="utf-8")
            job = {"contextFiles": [str(context)]}
            self.assertFalse(review_result.engaged(job, self._stream(root, ["other.md"])))


if __name__ == "__main__":
    unittest.main()
