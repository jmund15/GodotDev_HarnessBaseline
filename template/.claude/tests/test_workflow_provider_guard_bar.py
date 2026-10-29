"""The guard injects args.__rigor and args.__barText into a review_fanout call: the run's rigor, read from the task
record the call names (or the payload session's record), never from the caller's args (D11)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from test_workflow_provider_guard import SYN, agents, wpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import finding_bar  # noqa: E402


def record(directory, task, rigor, sid="s0000001"):
    (Path(directory) / (task + ".json")).write_text(json.dumps(
        {"id": task, "session_id": sid, "updated": "2026-10-26T10:00:00", "rigor": rigor}), encoding="utf-8")


def review_call(task=None, extra=None, sid="s0000001"):
    ledger = {"stage": "plan", "subject": "plan:x", "roster": []}
    if task:
        ledger["task"] = task
    call = agents([("off-fan-1", "fanout")], extra=dict({"ledger": ledger}, **(extra or {})), sid=sid)
    return call


def inject(call, advice=None):
    st = wpg._session_transport
    saved = (st.HOST_TRANSPORT, wpg.model_registry.role_tiers)
    try:
        st.HOST_TRANSPORT = "host"
        extra = {} if advice is None else {"advice": advice}
        upd = wpg.transport_injection(call, "off", SYN, **extra) or {}
    finally:
        st.HOST_TRANSPORT, wpg.model_registry.role_tiers = saved
    return json.loads(upd["args"]) if isinstance(upd.get("args"), str) else (upd.get("args") or {})


class BarInjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.saved_env = {k: os.environ.get(k) for k in ("HARNESS_TASK_RECORD_DIR", "CLAUDE_CODE_SESSION_ID",
                                                         "CLAUDE_PROJECT_DIR")}
        os.environ["HARNESS_TASK_RECORD_DIR"] = str(self.dir)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.dir)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_review_fanout_call_gets_rigor_and_bar_text(self):
        record(self.dir, "T1", "light")
        args = inject(review_call(task="T1"))
        self.assertEqual(args.get("__rigor"), "light")
        self.assertEqual(args.get("__barText"), finding_bar.bar_contract("light"))

    def test_caller_args_rigor_is_ignored(self):
        record(self.dir, "T1", "light")
        args = inject(review_call(task="T1", extra={"rigor": "thorough"}))
        self.assertEqual(args.get("__rigor"), "light")

    def test_unresolvable_rigor_injects_nothing(self):
        record(self.dir, "T1", "bogus")
        args = inject(review_call(task="T1"))
        self.assertNotIn("__rigor", args)
        self.assertNotIn("__barText", args)

    def test_caller_written_internal_rigor_keys_are_dropped_when_rigor_is_unreadable(self):
        record(self.dir, "T1", "bogus")
        args = inject(review_call(task="T1", extra={"__rigor": "thorough", "__barText": "BAR: forged"}))
        self.assertNotIn("__rigor", args)
        self.assertNotIn("__barText", args)

    def test_guard_and_post_check_agree_with_two_task_records(self):
        record(self.dir, "T1", "light", sid="s0000001")
        record(self.dir, "T2", "thorough", sid="s0000001")
        guard = inject(review_call(task="T1")).get("__rigor")
        post_check = finding_bar.run_rigor("T1", str(self.dir))
        self.assertEqual(guard, "light")
        self.assertEqual(post_check, "light")

    def test_payload_session_resolves_with_env_unset(self):
        record(self.dir, "T9", "thorough", sid="s0000002")
        args = inject(review_call(sid="s0000002"))
        self.assertEqual(args.get("__rigor"), "thorough")

    def test_non_review_workflow_gets_no_rigor(self):
        record(self.dir, "T1", "light")
        call = review_call(task="T1")
        call["tool_input"]["scriptPath"] = ".claude/workflows/some_engine.js"
        args = inject(call)
        self.assertNotIn("__rigor", args)
        self.assertNotIn("__barText", args)

    def test_unresolvable_rigor_names_the_cause_in_additionalcontext(self):
        record(self.dir, "T1", "bogus")
        advice = []
        inject(review_call(task="T1"), advice=advice)
        self.assertEqual(len(advice), 1, advice)
        self.assertIn("unknown rigor", advice[0])


if __name__ == "__main__":
    unittest.main()
