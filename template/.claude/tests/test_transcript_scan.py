#!/usr/bin/env python3
"""Proof for hooks/_transcript_scan.py and the readers built on it.

The owner-prompt reader resumes from a cached byte offset over an append-only transcript, and its
result equals a cold full parse: owner prompts with their U-ids (line indices). A truncated, rewritten
or replaced transcript, or an edited reader, restarts at line 1. Caches of a deleted transcript are swept.
The rows are read from tests/fixtures/transcript_scan/rows.jsonl: lines cut from one captured session
transcript (each row's uuid and type match a line of it) and trimmed, with absolute paths moved under a
neutral root and prose neutral where no case checks it. They hold an owner prompt and a short reply, a command's `ARGUMENTS:` companion, a question
answer, compaction summaries, a background launch and a fan-out launch.

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
import session_digest as sd  # noqa: E402

FIXTURE = HERE / "fixtures" / "transcript_scan" / "rows.jsonl"
LINES = []
for line in FIXTURE.read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    row['cwd'] = Path(STATE).as_posix()
    content = row.get('message', {}).get('content', [])
    for item in content if isinstance(content, list) else []:
        if item.get('type') != 'tool_use':
            continue
        inputs = item.get('input', {})
        for key, value in inputs.items():
            if isinstance(value, str):
                value = value.replace(r'C:\work\sample', Path(STATE).as_posix())
                value = value.replace('C:/work/sample', Path(STATE).as_posix())
                inputs[key] = value.replace('\\', '/') if key == 'file_path' else value
    LINES.append(json.dumps(row) + '\n')
SPLIT = len(LINES) * 2 // 3
def cold_owner(path):
    """The cold full parse the cache must reproduce: the builder's user messages merged with both
    recover passes, each reading the whole file."""
    builder = sd.TranscriptSummaryBuilder(path.stem, str(path), full_evidence=True)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            builder.process_line(line)
    messages = builder.finalize(backup_limits=False)["user_messages"] or []
    return sd.merge_recovered_prompts(messages, sd.recover_meta_command_prompts(path) + sd.recover_question_answers(path))


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


if __name__ == "__main__":
    unittest.main(verbosity=1)
