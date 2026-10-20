"""Real hook-payload proofs: a message reaches a peer session only while that peer is busy."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks/peer_message_guard.py"


class PeerMessageGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.sessions = self.home / ".claude" / "sessions"
        self.sessions.mkdir(parents=True)

    def peer(self, pid, name, status, sid="726084aa-0000-0000-0000-000000000000"):
        doc = {"pid": pid, "sessionId": sid, "name": name, "status": status, "kind": "interactive",
               "messagingSocketPath": "\\\\.\\pipe\\LOCAL\\cc-msg-" + name}
        (self.sessions / f"{pid}.json").write_text(json.dumps(doc), encoding="utf-8")

    def decide(self, to, tool="SendMessage"):
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}
        payload = {"tool_name": tool, "tool_input": {"to": to, "message": "fyi"}}
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                                capture_output=True, env=env, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        hso = out.get("hookSpecificOutput", {})
        return hso.get("permissionDecision", "allow"), hso.get("permissionDecisionReason", "")

    def test_idle_peer_is_denied_with_the_artifact_route(self):
        self.peer(1, "peer-d7", "idle")
        decision, reason = self.decide("peer-d7")
        self.assertEqual("deny", decision)
        self.assertIn("idle", reason)
        self.assertIn("CLAUDE.core.md", reason)

    def test_busy_peer_is_allowed(self):
        self.peer(1, "peer-d7", "busy")
        self.assertEqual("allow", self.decide("peer-d7")[0])

    def test_ref_and_socket_forms_resolve(self):
        self.peer(1, "peer-d7", "idle")
        self.assertEqual("deny", self.decide("peer-d7 [726084]")[0])
        self.assertEqual("deny", self.decide("uds:\\\\.\\pipe\\LOCAL\\cc-msg-peer-d7")[0])

    def test_unresolved_recipient_is_allowed(self):
        self.peer(1, "peer-d7", "idle")
        self.assertEqual("allow", self.decide("afefa363e5b93091a")[0])

    def test_ambiguous_name_is_allowed(self):
        self.peer(1, "twin", "idle", sid="aaaaaa00-0000-0000-0000-000000000000")
        self.peer(2, "twin", "busy", sid="bbbbbb00-0000-0000-0000-000000000000")
        self.assertEqual("allow", self.decide("twin")[0])
        self.assertEqual("deny", self.decide("twin [aaaaaa]")[0])

    def test_other_tools_and_bad_input_pass(self):
        self.peer(1, "peer-d7", "idle")
        self.assertEqual("allow", self.decide("peer-d7", tool="Bash")[0])
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}
        result = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True,
                                capture_output=True, env=env, timeout=60)
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
