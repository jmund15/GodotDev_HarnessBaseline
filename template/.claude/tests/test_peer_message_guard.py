"""Real hook-payload proofs: a message reaches a peer session or subagent only while its prompt cache is warm."""
import json
import os
import subprocess
import sys
import tempfile
import time
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

    def env(self, **extra):
        env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CONFIG_DIR"}
        return {**env, "HOME": str(self.home), "USERPROFILE": str(self.home), **extra}

    def decide(self, to, tool="SendMessage", session="this-sid", **extra_env):
        payload = {"tool_name": tool, "session_id": session, "tool_input": {"to": to, "message": "fyi"}}
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                                capture_output=True, env=self.env(**extra_env), timeout=60)
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

    def transcript(self, sid, age_sec):
        path = self.home / ".claude" / "projects" / "C--repo" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        stamp = time.time() - age_sec
        os.utime(path, (stamp, stamp))

    def test_idle_peer_with_a_warm_cache_is_allowed(self):
        self.peer(1, "peer-d7", "idle")
        self.transcript("726084aa-0000-0000-0000-000000000000", 10 * 60)
        self.assertEqual("allow", self.decide("peer-d7")[0])

    def test_idle_peer_with_an_expired_cache_is_denied(self):
        self.peer(1, "peer-d7", "idle")
        self.transcript("726084aa-0000-0000-0000-000000000000", 2 * 3600)
        decision, reason = self.decide("peer-d7")
        self.assertEqual("deny", decision)
        self.assertIn("cache", reason)

    def test_idle_peer_past_the_idle_compaction_point_is_denied(self):
        # Claude Code compacts an idle one-hour-cache session at about 54 min (_hook_state), so warmth ends at 50.
        self.peer(1, "peer-d7", "idle")
        self.transcript("726084aa-0000-0000-0000-000000000000", 52 * 60)
        self.assertEqual("deny", self.decide("peer-d7")[0])

    def test_peer_under_a_declared_login_outside_dot_claude_is_checked(self):
        login = self.home / "work-login"
        (login / "sessions").mkdir(parents=True)
        doc = {"pid": 7, "sessionId": "cccc0000-0000-0000-0000-000000000000", "name": "peer-work", "status": "idle",
               "kind": "interactive", "messagingSocketPath": "\\\\.\\pipe\\LOCAL\\cc-msg-peer-work"}
        (login / "sessions" / "7.json").write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual("deny", self.decide("peer-work", CLAUDE_CONFIG_DIR=str(login))[0])

    def test_ages_prints_each_local_peer_and_its_warmth(self):
        self.peer(1, "peer-warm", "idle", sid="aaaa0000-0000-0000-0000-000000000000")
        self.peer(2, "peer-cold", "idle", sid="bbbb0000-0000-0000-0000-000000000000")
        self.transcript("aaaa0000-0000-0000-0000-000000000000", 10 * 60)
        self.transcript("bbbb0000-0000-0000-0000-000000000000", 2 * 3600)
        result = subprocess.run([sys.executable, str(HOOK), "--ages"], text=True, capture_output=True,
                                env=self.env(), timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        lines = {line.split()[0]: line for line in result.stdout.splitlines() if line.strip()}
        self.assertIn("warm", lines["peer-warm"])
        self.assertIn("cold", lines["peer-cold"])

    def test_live_sessions_lists_the_registry_rows_ages_prints(self):
        self.peer(1, "peer-a", "idle", sid="aaaa0000-0000-0000-0000-000000000000")
        doc = {"pid": 2, "sessionId": "bbbb0000-0000-0000-0000-000000000000", "name": "peer-b", "status": "busy",
               "cwd": "C:\\work\\peer-b"}
        (self.sessions / "2.json").write_text(json.dumps(doc), encoding="utf-8")
        code = ("import json, peer_message_guard as p; print(json.dumps(p.live_sessions()))")
        result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, cwd=str(HOOK.parent),
                                env=self.env(), timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        rows = {row["name"]: row for row in json.loads(result.stdout)}
        self.assertEqual({"peer-a", "peer-b"}, set(rows))
        self.assertEqual({"sessionId": "bbbb0000-0000-0000-0000-000000000000", "name": "peer-b",
                          "cwd": "C:\\work\\peer-b"}, rows["peer-b"])
        self.assertEqual("", rows["peer-a"]["cwd"])

    def subagent(self, agent_id, age_sec, parent="parent-sid"):
        path = self.home / ".claude" / "projects" / "C--repo" / parent / "subagents" / f"agent-{agent_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        stamp = time.time() - age_sec
        os.utime(path, (stamp, stamp))

    def test_subagent_past_its_5m_cache_is_denied(self):
        self.subagent("a1d0bc9001d84f797", 10 * 60)
        decision, reason = self.decide("a1d0bc9001d84f797", session="parent-sid")
        self.assertEqual("deny", decision)
        self.assertIn("5m", reason)

    def test_parent_session_id_with_glob_characters_is_matched_literally(self):
        self.subagent("a1d0bc9001d84f797", 10 * 60, parent="par[ent]-sid")
        self.assertEqual("deny", self.decide("a1d0bc9001d84f797", session="par[ent]-sid")[0])

    def test_subagent_inside_its_cache_is_allowed(self):
        self.subagent("a1d0bc9001d84f797", 60)
        self.assertEqual("allow", self.decide("a1d0bc9001d84f797", session="parent-sid")[0])

    def test_peer_under_another_config_dir_is_checked(self):
        other = self.home / ".claude-secondary" / "sessions"
        other.mkdir(parents=True)
        sid = "bbbb0000-0000-0000-0000-000000000000"
        doc = {"pid": 9, "sessionId": sid, "name": "peer-sec", "status": "idle", "kind": "interactive",
               "messagingSocketPath": "\\\\.\\pipe\\LOCAL\\cc-msg-peer-sec"}
        (other / "9.json").write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual("deny", self.decide("peer-sec")[0])
        path = self.home / ".claude-secondary" / "projects" / "C--repo" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
        self.assertEqual("allow", self.decide("peer-sec")[0])

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
