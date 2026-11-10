#!/usr/bin/env python3
"""Proof for hooks/_transcript_scan.py and the readers built on it.

Every compaction-path transcript reader resumes from a cached byte offset over an append-only
transcript, and its result equals a cold full parse: owner prompts with their U-ids (line indices),
the ledger's prior summaries and facts block, the anchor's reply contexts and the metrics launch
scan. A truncated, rewritten or replaced transcript, or an edited reader, restarts at line 1. Caches
of a deleted transcript are swept. Both compaction hooks log duration and output bytes per run.
The rows are cut from a captured session transcript and trimmed, planted here: an owner prompt and a
short reply, a command's `ARGUMENTS:` companion, a question answer, two compaction summaries, a
background launch and a fan-out launch. tests/fixtures/ never ships, so nothing is read from it.

    python3 .claude/tests/test_transcript_scan.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
sys.path[:0] = [str(CLAUDE / "hooks"), str(CLAUDE / "tools")]
STATE = tempfile.mkdtemp(prefix="transcript_scan_state_")
os.environ["HARNESS_HOOK_STATE_DIR"] = STATE

import _transcript_scan as ts  # noqa: E402
import compact_directive_anchor as anchor  # noqa: E402
import compact_directive_ledger as ledger  # noqa: E402
import orchestration_metrics as om  # noqa: E402
import session_digest as sd  # noqa: E402

STAMP = "2026-10-17T17:%02d:00.000Z"


def _user(index, content, **extra):
    row = {"type": "user", "uuid": "u%d" % index, "timestamp": STAMP % index,
           "message": {"role": "user", "content": content}}
    row.update(extra)
    return row


def _assistant(index, blocks):
    return {"type": "assistant", "uuid": "a%d" % index, "timestamp": STAMP % index,
            "message": {"role": "assistant", "model": "a-model", "content": blocks}}


def _result(index, tool_use_id, text, **extra):
    row = _user(index, [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}], **extra)
    return row


ROWS = [
    _user(0, "Read and follow the brief in full, then report back."),
    _assistant(1, [{"type": "text", "text": "Lane A is running. Both worktrees built clean."}]),
    _user(2, "Yes please codify all Fs."),
    _user(3, [{"type": "text", "text": "# /feature_drive — One-Plan Drive\n\nARGUMENTS: plan.md --task t1"}],
          isMeta=True),
    _assistant(4, [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
                    "input": {"questions": [{"question": "Which rulings?"}]}}]),
    _result(5, "q1", "All of them"),
    _user(6, "Summary one.\n\nOwner directives:\nU1 — holds — do the thing.", isCompactSummary=True),
    _user(7, "Summary two.", isCompactSummary=True),
    _assistant(8, [{"type": "tool_use", "id": "b1", "name": "Bash",
                    "input": {"command": "python3 .claude/scripts/long_job.py", "run_in_background": True}}]),
    _result(9, "b1", "running", toolUseResult={"backgroundTaskId": "task-1"}),
    _assistant(10, [{"type": "tool_use", "id": "w1", "name": "Write",
                     "input": {"file_path": ".claude/scratch/jobs.json", "content": '{"jobs":[{"label":"j1"}]}'}}]),
    _result(11, "w1", "File created successfully"),
    _assistant(12, [{"type": "tool_use", "id": "s1", "name": "Bash", "input": {
        "command": "python3 .claude/tools/sidecar_fanout.py .claude/scratch/jobs.json "
                   "--out-dir .claude/scratch/out --detach"}}]),
    _result(13, "s1", "launched"),
    _user(14, "Also codify every finding from that run."),
]
LINES = [json.dumps(row) + "\n" for row in ROWS]
SPLIT = len(LINES) * 2 // 3


def cold_owner(path):
    """The cold full parse the cache must reproduce: the builder's user messages merged with both
    recover passes, each reading the whole file."""
    return sd.merge_recovered_prompts(sd._digest_dict(path)["user_messages"] or [],
                                      sd.recover_meta_command_prompts(path) + sd.recover_question_answers(path))


def last_stat(name):
    return [s for s in ts.STATS if s["scanner"] == name][-1]


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="transcript_scan_"))
        self.path = self.dir / "aaaabbbb-0000-4000-8000-transcriptscan.jsonl"
        os.environ["HARNESS_HOOK_STATE_DIR"] = str(self.dir / "state")

    def write(self, lines, mode="w"):
        with open(self.path, mode, encoding="utf-8", newline="") as fh:
            fh.write("".join(lines))

    def fresh_state(self):
        os.environ["HARNESS_HOOK_STATE_DIR"] = tempfile.mkdtemp(prefix="transcript_scan_fresh_")


class OwnerPrompts(Case):
    def test_warm_resume_after_append_equals_cold_parse_with_ids(self):
        self.write(LINES[:SPLIT])
        self.assertEqual(sd.owner_prompts(self.path), cold_owner(self.path))
        self.assertEqual(last_stat("owner_prompts")["resumed_at"], 0)
        offset = self.path.stat().st_size
        self.write(LINES[SPLIT:], "a")
        warm = sd.owner_prompts(self.path)
        self.assertEqual(last_stat("owner_prompts")["resumed_at"], offset)
        self.assertEqual(last_stat("owner_prompts")["read_bytes"], self.path.stat().st_size - offset)
        expected = cold_owner(self.path)
        self.assertEqual(warm, expected)
        self.assertEqual(json.dumps(warm, sort_keys=True), json.dumps(expected, sort_keys=True))
        kinds = {m.get("recovered") for m in expected}
        self.assertIn("command_arguments", kinds, "fixture must reach the meta-argument recovery")
        self.assertIn("question_answer", kinds, "fixture must reach the answer recovery")
        self.assertTrue(any(m["index"] > SPLIT for m in expected), "an owner row must sit in the appended part")

    def test_truncated_transcript_restarts_at_line_one(self):
        self.write(LINES)
        sd.owner_prompts(self.path)
        self.write(LINES[:SPLIT // 2])
        result = sd.owner_prompts(self.path)
        self.assertEqual(last_stat("owner_prompts")["resumed_at"], 0)
        self.assertEqual(result, cold_owner(self.path))

    def test_replaced_transcript_restarts_at_line_one(self):
        self.write(LINES[:SPLIT])
        sd.owner_prompts(self.path)
        replaced = [LINES[SPLIT]] + LINES[1:SPLIT] + LINES[SPLIT:]
        self.write(replaced)
        result = sd.owner_prompts(self.path)
        self.assertEqual(last_stat("owner_prompts")["resumed_at"], 0)
        self.assertEqual(result, cold_owner(self.path))

    def test_partial_last_line_counts_now_and_is_not_saved(self):
        self.write(LINES[:SPLIT])
        sd.owner_prompts(self.path)
        offset = self.path.stat().st_size
        half = LINES[SPLIT][:len(LINES[SPLIT]) // 2]
        self.write([half], "a")
        self.assertEqual(sd.owner_prompts(self.path), cold_owner(self.path))
        self.write([LINES[SPLIT][len(half):]] + LINES[SPLIT + 1:], "a")
        self.assertEqual(sd.owner_prompts(self.path), cold_owner(self.path))
        self.assertEqual(last_stat("owner_prompts")["resumed_at"], offset)


class LedgerAndAnchor(Case):
    def test_ledger_readers_warm_equal_cold(self):
        self.write(LINES[:SPLIT])
        ledger.prior_summaries(self.path), ledger.facts_lines(self.path)
        self.write(LINES[SPLIT:], "a")
        warm = (ledger.prior_summaries(self.path), ledger.facts_lines(self.path))
        self.assertGreater(last_stat("ledger_facts")["resumed_at"], 0)
        self.assertGreater(last_stat("ledger_summaries")["resumed_at"], 0)
        self.fresh_state()
        cold = (ledger.prior_summaries(self.path), ledger.facts_lines(self.path))
        self.assertEqual(last_stat("ledger_facts")["resumed_at"], 0)
        self.assertEqual(warm, cold)
        self.assertEqual(len(cold[0]), ledger.MIN_PRIOR_SUMMARIES, "fixture must hold two prior summaries")
        self.assertTrue(any("Background jobs" in line for line in cold[1]), "fixture must reach a running job")

    def test_reply_contexts_warm_equal_cold_and_follow_the_asked_set(self):
        self.write(LINES)
        messages = [m for m in cold_owner(self.path) if anchor.keep(m)]
        early = [m for m in messages if m["index"] <= SPLIT]
        self.write(LINES[:SPLIT])
        anchor.reply_contexts(self.path, early)
        self.write(LINES[SPLIT:], "a")
        warm = anchor.reply_contexts(self.path, messages)
        self.assertGreater(last_stat("reply_contexts")["resumed_at"], 0)
        self.fresh_state()
        cold = anchor.reply_contexts(self.path, messages)
        self.assertEqual(warm, cold)
        self.assertTrue(cold, "fixture must reach a reply that needs its context")
        fewer = messages[1:]
        self.assertEqual(anchor.reply_contexts(self.path, fewer), {k: v for k, v in cold.items()
                                                                   if k in {m["index"] for m in fewer}})


class MetricsLaunchScan(Case):
    def test_launch_readers_warm_equal_cold(self):
        session = str(self.path)[:-len(".jsonl")]
        self.write(LINES[:SPLIT])
        om._sidecar_record_launches(session)
        self.write(LINES[SPLIT:], "a")
        warm = (om._fanout_jobs_paths(str(self.path)), om._sidecar_record_launches(session),
                om.uncaptured_fanout_jobs(session))
        self.assertGreater(last_stat("metrics_launches")["resumed_at"], 0)
        self.fresh_state()
        before = len(ts.STATS)
        cold = (om._fanout_jobs_paths(str(self.path)), om._sidecar_record_launches(session),
                om.uncaptured_fanout_jobs(session))
        self.assertEqual([s for s in ts.STATS[before:] if s["scanner"] == "metrics_launches"][0]["resumed_at"], 0)
        self.assertEqual(warm, cold)
        self.assertTrue(cold[1], "fixture must reach a sidecar launch")


class ScannerMechanics(Case):
    def collector(self, source):
        return ts.Scanner("collect_probe", lambda: {"rows": []},
                          lambda state, index, line: state["rows"].append([index, line]), [source])

    def test_universal_newlines_match_text_mode_line_numbers(self):
        source = self.dir / "probe_source.py"
        source.write_text("# v1\n", encoding="utf-8")
        with open(self.path, "wb") as fh:
            fh.write(b'{"a": 1}\r\n{"b": 2}\r{"c": 3}\n')
        scanner = self.collector(source)
        ts.scan(self.path, scanner)
        with open(self.path, "ab") as fh:
            fh.write(b'{"d": 4}\r\n{"e"')
        rows = ts.scan(self.path, scanner)["rows"]
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(rows, [[i, line] for i, line in enumerate(fh, 1)])

    def test_edited_reader_source_restarts_at_line_one(self):
        source = self.dir / "probe_source.py"
        source.write_text("# v1\n", encoding="utf-8")
        self.write(LINES[:5])
        ts.scan(self.path, self.collector(source))
        self.write(LINES[5:8], "a")
        ts.scan(self.path, self.collector(source))
        self.assertGreater(last_stat("collect_probe")["resumed_at"], 0)
        source.write_text("# v2\n", encoding="utf-8")
        ts._code_prints.clear()
        self.write(LINES[8:9], "a")
        rows = ts.scan(self.path, self.collector(source))["rows"]
        self.assertEqual(last_stat("collect_probe")["resumed_at"], 0)
        self.assertEqual(len(rows), 9)

    def test_cache_of_a_deleted_transcript_is_swept(self):
        source = self.dir / "probe_source.py"
        source.write_text("# v1\n", encoding="utf-8")
        gone = self.dir / "gone.jsonl"
        gone.write_text(LINES[0], encoding="utf-8")
        ts.scan(gone, self.collector(source))
        stale = ts.cache_path(gone, self.collector(source))
        self.assertTrue(os.path.isfile(stale))
        gone.unlink()
        ts._swept = False
        self.write(LINES[:3])
        ts.scan(self.path, self.collector(source))
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.isfile(ts.cache_path(self.path, self.collector(source))))


class HookRunLog(Case):
    def run_hook(self, name, event):
        project = self.dir / "project"
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project), HARNESS_PENDING_COUNT="0",
                   HARNESS_TASK_RECORD_DIR=str(project / "tasks"))
        env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
        payload = {"hook_event_name": event, "source": "compact", "transcript_path": str(self.path),
                   "session_id": self.path.stem, "cwd": str(project)}
        proc = subprocess.run([sys.executable, str(CLAUDE / "hooks" / name)], input=json.dumps(payload).encode(),
                              capture_output=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))
        self.assertNotIn(b"Traceback", proc.stderr)
        return proc.stdout, project / "logs" / "compaction_hooks.jsonl"

    def test_both_compaction_hooks_log_duration_and_output_bytes(self):
        self.write(LINES)
        for name, event in (("compact_directive_ledger.py", "PreCompact"),
                            ("compact_directive_anchor.py", "SessionStart")):
            stdout, log = self.run_hook(name, event)
            self.assertTrue(stdout)
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            row = rows[-1]
            self.assertEqual(row["hook"], name[:-3])
            self.assertEqual(row["output_bytes"], len(stdout))
            self.assertIsInstance(row["duration_s"], float)
            self.assertEqual(row["transcript_bytes"], self.path.stat().st_size)
            self.assertTrue(row["scans"], "the run log names the scans it ran")


if __name__ == "__main__":
    unittest.main(verbosity=1)
