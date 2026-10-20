"""Proof for hooks/workflow_provider_guard.py `loadable_coverage`: a covered shape is offered only when the
session can have loaded its generated agent type (the definition file is older than the session's first
transcript timestamp). An unknown start offers none.

    python3 .claude/tests/test_workflow_provider_guard_lean_loaded.py
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from test_workflow_provider_guard import SYN, agents, wpg

START = "2026-10-20T13:26:50.715Z"
START_EPOCH = 1792502810.715
COVERED = {"a": {"covered": ["author", "verify"], "needs": [], "unread": []},
           "b": {"covered": ["survey"], "needs": [], "unread": []}}
TYPES = {"author": "lane-author", "verify": "lane-verify", "survey": "lane-survey"}


class LoadableCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.agents = self.base / "agents"
        self.agents.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def agent_file(self, shape, age):
        path = self.agents / (TYPES[shape] + ".md")
        path.write_text("---\nname: %s\n---\n" % TYPES[shape], encoding="utf-8")
        os.utime(path, (START_EPOCH + age, START_EPOCH + age))

    def transcript(self, lines):
        path = self.base / "sess.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        return str(path)

    def payload(self, lines=None):
        lines = [{"type": "mode"}, {"type": "user", "timestamp": START}] if lines is None else lines
        return {"tool_name": "Workflow", "session_id": "sess", "transcript_path": self.transcript(lines)}

    def narrowed(self, payload):
        return wpg.loadable_coverage(payload, json.loads(json.dumps(COVERED)), agents_dir=str(self.agents))

    def test_file_older_than_session_start_is_offered(self):
        self.agent_file("author", -3600)
        self.agent_file("verify", -1)
        self.assertEqual(self.narrowed(self.payload())["a"]["covered"], ["author", "verify"])

    def test_file_newer_than_session_start_is_withheld(self):
        self.agent_file("author", -3600)
        self.agent_file("verify", +60)
        self.assertEqual(self.narrowed(self.payload())["a"]["covered"], ["author"])

    def test_missing_file_is_withheld(self):
        self.agent_file("author", -3600)
        got = self.narrowed(self.payload())
        self.assertEqual((got["a"]["covered"], got["b"]["covered"]), (["author"], []))

    def test_unknown_start_withholds_every_swap(self):
        for shape in TYPES:
            self.agent_file(shape, -3600)
        for lines in ([{"type": "mode"}], []):
            got = self.narrowed(self.payload(lines))
            self.assertEqual((got["a"]["covered"], got["b"]["covered"]), ([], []), lines)
        no_transcript = {"tool_name": "Workflow"}
        self.assertEqual(self.narrowed(no_transcript)["a"]["covered"], [])
        gone = {"tool_name": "Workflow", "transcript_path": str(self.base / "absent.jsonl")}
        self.assertEqual(self.narrowed(gone)["a"]["covered"], [])

    def test_other_entry_fields_survive(self):
        self.agent_file("author", +60)
        got = self.narrowed(self.payload())
        self.assertEqual((got["a"]["needs"], got["a"]["unread"]), ([], []))

    def test_injection_carries_the_narrowed_map(self):
        self.agent_file("author", -3600)
        self.agent_file("verify", +60)
        saved = (wpg.coverage_map, wpg.AGENTS_DIR)
        st = wpg._session_transport
        saved_t = (st.HOST_TRANSPORT, wpg.model_registry.role_tiers)
        try:
            wpg.coverage_map = lambda payload: json.loads(json.dumps(COVERED))
            wpg.AGENTS_DIR = str(self.agents)
            st.HOST_TRANSPORT = "host"
            call = agents([("off-fan-1", "fanout")])
            call.update(self.payload())
            upd = wpg.transport_injection(call, "off", SYN) or {}
        finally:
            wpg.coverage_map, wpg.AGENTS_DIR = saved
            st.HOST_TRANSPORT, wpg.model_registry.role_tiers = saved_t
        self.assertEqual((upd.get("args") or {}).get("__coverage", {}).get("a", {}).get("covered"), ["author"])


if __name__ == "__main__":
    unittest.main()
