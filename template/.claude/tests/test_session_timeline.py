#!/usr/bin/env python3
"""Re-runnable proof: tools/session_timeline.py partitions a main transcript's wall time.

Every gap between consecutive rows lands in exactly one bucket, so the buckets sum to the
span; a gap over 30 minutes contributes its excess to `away`. Each case plants a transcript
and asserts the one bucket rule it names. Absent and unreadable inputs stay distinct states.

    python3 .claude/tests/test_session_timeline.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import session_timeline  # noqa: E402

T0 = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def iso(offset):
    return (T0 + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


def assistant(offset, mid, blocks=None, text="ok"):
    content = blocks if blocks is not None else [{"type": "text", "text": text}]
    return {"type": "assistant", "timestamp": iso(offset),
            "message": {"id": mid, "role": "assistant", "model": "model-a", "content": content}}


def tool_use(use_id, name, **inp):
    return {"type": "tool_use", "id": use_id, "name": name, "input": inp}


def result(offset, use_id, content="done"):
    return {"type": "user", "timestamp": iso(offset),
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": use_id, "content": content}]}}


def owner(offset, text="please continue"):
    return {"type": "user", "timestamp": iso(offset), "message": {"role": "user", "content": text}}


def notification(offset, use_id):
    text = ("<task-notification><tool-use-id>%s</tool-use-id><status>completed</status>"
            "</task-notification>" % use_id)
    return {"type": "user", "timestamp": iso(offset), "message": {"role": "user", "content": text}}


class PartitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def plant(self, rows, raw_tail=""):
        path = os.path.join(self.tmp.name, "t.jsonl")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
            fh.write(raw_tail)
        return path

    def part(self, rows, raw_tail=""):
        return session_timeline.partition(self.plant(rows, raw_tail))

    def test_buckets_sum_to_span_and_gaps_land_by_later_row_kind(self):
        rows = [owner(0), assistant(5, "m1", [tool_use("u1", "Read", file_path="x")]),
                result(8, "u1"), assistant(10, "m2", text="done"), owner(70)]
        got = self.part(rows)
        self.assertEqual(got["state"], "ok")
        self.assertEqual(got["buckets"]["gen"], 7.0)
        self.assertEqual(got["buckets"]["tool:Read"], 3.0)
        self.assertEqual(got["buckets"]["owner"], 60.0)
        self.assertEqual(got["span"], 70.0)
        self.assertEqual(sum(got["buckets"].values()), got["span"])

    def test_shell_classes_and_background_suffix(self):
        rows = [
            owner(0),
            assistant(1, "a", [tool_use("c1", "Bash", command="git -C x commit -m y")]), result(4, "c1"),
            assistant(5, "b", [tool_use("t1", "Bash", command="python3 .claude/tests/test_a.py")]), result(15, "t1"),
            assistant(16, "c", [tool_use("t2", "Bash", command="python3 .claude/scripts/harness_tests.py --close",
                                         run_in_background=True)]), result(17, "t2"),
            assistant(18, "d", [tool_use("o1", "PowerShell", command="Get-ChildItem")]), result(19, "o1"),
            assistant(20, "e", [tool_use("m1", "mcp__godot__run_project")]), result(26, "m1"),
        ]
        b = self.part(rows)["buckets"]
        self.assertEqual(b["tool:shell_commit"], 3.0)
        self.assertEqual(b["tool:shell_test"], 10.0)
        self.assertEqual(b["tool:shell_test_bg"], 1.0)
        self.assertEqual(b["tool:shell_other"], 1.0)
        self.assertEqual(b["tool:mcp"], 6.0)

    def test_notification_after_assistant_is_idle_on_the_launching_tool_class(self):
        rows = [owner(0),
                assistant(1, "a", [tool_use("bg1", "Bash", command="sleep 5", run_in_background=True)]),
                result(2, "bg1"), assistant(3, "b", text="waiting"), notification(63, "bg1")]
        got = self.part(rows)
        self.assertEqual(got["buckets"]["idle:shell_other_bg"], 60.0)
        self.assertEqual(sum(got["buckets"].values()), got["span"])

    def test_notification_right_after_a_tool_result_is_not_idle(self):
        rows = [owner(0),
                assistant(1, "a", [tool_use("bg1", "Bash", command="sleep 5", run_in_background=True)]),
                result(2, "bg1"), notification(12, "bg1")]
        got = self.part(rows)
        self.assertEqual(got["buckets"]["other:tool_result>notification"], 10.0)
        self.assertNotIn("idle:shell_other_bg", got["buckets"])

    def test_meta_rows_fall_to_other(self):
        rows = [owner(0), assistant(1, "a", text="x"),
                {"type": "user", "timestamp": iso(4), "isMeta": True,
                 "message": {"role": "user", "content": "<local-command-stdout>x</local-command-stdout>"}}]
        got = self.part(rows)
        self.assertEqual(got["buckets"]["other:assistant>meta"], 3.0)

    def test_gap_over_thirty_minutes_credits_excess_to_away(self):
        rows = [assistant(0, "a", text="x"), owner(2000), assistant(2005, "b", text="y")]
        got = self.part(rows)
        self.assertEqual(got["buckets"]["away"], 200.0)
        self.assertEqual(got["buckets"]["owner"], 1800.0)
        self.assertEqual(sum(got["buckets"].values()), got["span"])

    def test_compactions_foreground_tests_commits_and_big_results(self):
        compact = owner(1, "summary")
        compact["isCompactSummary"] = True
        rows = [compact,
                assistant(2, "a", [tool_use("t1", "Bash", command="bash .claude/tests/test_z.sh")]),
                result(52, "t1", content="x" * 20001),
                assistant(53, "b", [tool_use("c1", "Bash", command="git commit -F m.txt")]),
                result(93, "c1")]
        got = self.part(rows)
        self.assertEqual(got["compactions"], 1)
        self.assertEqual(got["fg_test_over_budget"], {"n": 1, "secs": 50.0})
        self.assertEqual(got["commit_secs"], [40.0])
        self.assertEqual(got["big_results"], {"n": 1, "chars": 20001})

    def test_foreground_test_budget_is_the_runners_constant(self):
        sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
        try:
            import harness_tests
        finally:
            sys.path.pop(0)
        rows = [owner(1, "go"),
                assistant(2, "a", [tool_use("t1", "Bash", command="bash .claude/tests/test_z.sh")]),
                result(52, "t1")]
        saved = harness_tests.COMMIT_BUDGET_S
        harness_tests.COMMIT_BUDGET_S = 60
        try:
            self.assertEqual(self.part(rows)["fg_test_over_budget"], {"n": 0, "secs": 0.0})
            harness_tests.COMMIT_BUDGET_S = 45
            self.assertEqual(self.part(rows)["fg_test_over_budget"], {"n": 1, "secs": 50.0})
        finally:
            harness_tests.COMMIT_BUDGET_S = saved

    def test_runner_import_leaves_scripts_off_the_search_path(self):
        probe = ("import os, sys; sys.path.insert(0, %r); import session_timeline; session_timeline.runner(); "
                 "print(any(os.path.basename(p) == 'scripts' for p in sys.path))" % os.path.join(HERE, "..", "tools"))
        got = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
        self.assertEqual(got.stdout.strip(), "False")

    def test_bookkeeping_messages_name_no_runner_and_tool_messages_count(self):
        rows = [owner(0),
                assistant(1, "book", [tool_use("b1", "Bash", command="python3 .claude/tools/task_record.py block x")]),
                result(2, "b1"),
                assistant(3, "mixed", [tool_use("b2", "Bash", command="git status"),
                                       tool_use("b3", "Read", file_path="f")]), result(4, "b2"),
                assistant(5, "runner", [tool_use("b4", "Bash",
                                                 command="git status && python3 .claude/tests/test_a.py")]),
                result(6, "b4"), assistant(7, "talk", text="no tools")]
        got = self.part(rows)
        self.assertEqual(got["bookkeeping_ids"], ["book"])
        self.assertEqual(got["tool_messages"], 3)

    def test_streamed_records_of_one_message_count_once(self):
        first = assistant(1, "same", [{"type": "text", "text": "a"}])
        second = assistant(2, "same", [tool_use("u1", "Read", file_path="f")])
        got = self.part([owner(0), first, second, result(3, "u1")])
        self.assertEqual(got["tool_messages"], 1)
        self.assertEqual(got["counts"]["gen"], 1)

    def test_absent_unreadable_and_bad_lines_are_distinct_states(self):
        self.assertEqual(session_timeline.partition(os.path.join(self.tmp.name, "none.jsonl"))["state"], "absent")
        self.assertEqual(session_timeline.partition(self.tmp.name)["state"], "unreadable")
        got = self.part([owner(0), assistant(1, "a", text="x")], raw_tail="{not json\n")
        self.assertEqual((got["state"], got["bad_lines"]), ("ok", 1))


if __name__ == "__main__":
    unittest.main()
