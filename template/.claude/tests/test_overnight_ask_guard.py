"""Real hook-payload proofs for session-owned overnight state."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks/overnight_ask_guard.py"


class OvernightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def call(self, *args, sid="one", payload=None):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root),
               "CLAUDE_CODE_SESSION_ID": sid, "CLAUDE_SESSION_ID": ""}
        result = subprocess.run([sys.executable, str(HOOK), *args],
                                input=json.dumps(payload or {}), text=True, capture_output=True,
                                env=env, timeout=60)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def decision(self, sid="one", tool="AskUserQuestion", env_sid="one"):
        result = self.call(sid=env_sid, payload={"tool_name": tool, "session_id": sid, "cwd": str(self.root)})
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        return data.get("hookSpecificOutput", {}).get("permissionDecision", "allow")

    def marker(self, sid="one"):
        return self.root / ".claude/scratch/overnight" / f"active-{sid}.json"

    def arm(self, sid="one", goal="finish"):
        result = self.call("--arm", goal, sid=sid)
        self.assertEqual(0, result.returncode, result.stderr)

    def close_doc(self, sid="one", q_line=None):
        path = self.root / "Overnight" / f"2026-09-20-{sid}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = ["## Outcome", "", "- done — proof", "", "## Decisions for you", ""]
        if q_line:
            rows.append(q_line)
        rows.extend(["", "## Left undone", "", "- None.", ""])
        path.write_text("\n".join(rows), encoding="utf-8")
        return path

    def test_no_marker_allows(self):
        self.assertEqual("allow", self.decision())

    def test_arm_uses_current_cli_session_id(self):
        self.arm()
        self.assertTrue(self.marker().exists())
        doc = json.loads(self.marker().read_text(encoding="utf-8"))
        self.assertEqual("one", doc["session_id"])
        self.assertEqual("finish", doc["goal"])
        self.assertEqual("deny", self.decision())
        self.assertEqual("allow", self.decision(tool="Bash"))

    def test_payload_identity_is_authoritative_and_peer_is_not_blocked(self):
        self.arm()
        self.assertEqual("allow", self.decision(sid="two", env_sid="one"))
        self.assertEqual("deny", self.decision(sid="one", env_sid="two"))

    def test_arming_and_disarming_peer_never_overwrites_own_state(self):
        self.arm(goal="first")
        self.arm(sid="two", goal="second")
        self.assertEqual("first", json.loads(self.marker().read_text())["goal"])
        peer_close = self.close_doc("two")
        self.assertEqual(0, self.call("--validate-close", str(peer_close), sid="two").returncode)
        self.assertEqual(0, self.call("--disarm", sid="two").returncode)
        self.assertEqual("deny", self.decision())
        self.assertEqual("allow", self.decision(sid="two"))
        self.assertTrue(self.marker().exists())

    def test_expired_owned_state_is_removed_but_peer_is_preserved(self):
        self.arm()
        self.arm(sid="two")
        doc = json.loads(self.marker().read_text())
        doc["armed_at"] = time.time() - 17 * 3600
        self.marker().write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual("allow", self.decision())
        self.assertFalse(self.marker().exists())
        self.assertTrue(self.marker("two").exists())

    def test_legacy_anonymous_marker_cannot_block_or_be_deleted(self):
        legacy = self.marker().with_name("active.json")
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"goal": "unknown owner", "session_id": "", "armed_at": time.time()}))
        self.assertEqual("allow", self.decision())
        self.assertEqual(0, self.call("--disarm").returncode)
        self.assertTrue(legacy.exists())

    def test_missing_or_unsafe_identity_cannot_arm(self):
        for sid in ("", "../peer", "a/b", "a\\b"):
            with self.subTest(sid=sid):
                self.assertNotEqual(0, self.call("--arm", "finish", sid=sid).returncode)
        self.assertFalse((self.root / ".claude/scratch/overnight/active.json").exists())

    def test_invalid_or_mismatched_state_is_not_claimed(self):
        self.arm()
        for doc in ([], {"session_id": "two", "armed_at": time.time()},
                    {"session_id": "one", "armed_at": float("nan")},
                    {"session_id": "one", "armed_at": time.time() + 3600}):
            with self.subTest(doc=doc):
                self.marker().write_text(json.dumps(doc))
                self.assertEqual("allow", self.decision())
                self.call("--disarm")
                self.assertTrue(self.marker().exists())

    def test_status_names_only_this_session(self):
        self.arm()
        result = self.call("--status")
        self.assertEqual(0, result.returncode, result.stderr)
        doc = json.loads(result.stdout)
        self.assertTrue(doc["armed"])
        self.assertEqual("one", doc["session_id"])
        self.assertEqual("finish", doc["goal"])
        self.assertFalse(json.loads(self.call("--status", sid="two").stdout)["armed"])

    def test_validate_close_binds_hash_before_disarm(self):
        self.arm()
        close = self.close_doc(q_line=(
            "**Q1 — Remove stale worktree? Options: remove | keep. "
            "Park: irreversible — removal needs owner confirmation. Recommendation: keep."
        ))
        result = self.call("--validate-close", str(close))
        self.assertEqual(0, result.returncode, result.stderr)
        state = json.loads(self.marker().read_text(encoding="utf-8"))
        self.assertEqual(str(close.resolve()), state["validated_close"]["path"])
        self.assertRegex(state["validated_close"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(0, self.call("--disarm").returncode)
        self.assertFalse(self.marker().exists())

    def test_disarm_refuses_missing_or_stale_close_receipt(self):
        self.arm()
        self.assertNotEqual(0, self.call("--disarm").returncode)
        self.assertTrue(self.marker().exists())
        close = self.close_doc()
        self.assertEqual(0, self.call("--validate-close", str(close)).returncode)
        close.write_text(close.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        self.assertNotEqual(0, self.call("--disarm").returncode)
        self.assertTrue(self.marker().exists())

    def test_validate_close_rejects_wrong_identity_headings_and_q_shape(self):
        self.arm()
        wrong = self.close_doc("two")
        self.assertNotEqual(0, self.call("--validate-close", str(wrong)).returncode)
        bad_heading = self.close_doc()
        bad_heading.write_text(bad_heading.read_text().replace("## Left undone", "## Timeline"))
        self.assertNotEqual(0, self.call("--validate-close", str(bad_heading)).returncode)
        bad_q = self.close_doc(q_line="**Q1 — choose later. Recommendation: A.")
        self.assertNotEqual(0, self.call("--validate-close", str(bad_q)).returncode)
        self.assertTrue(self.marker().exists())

    def test_malformed_hook_input_fails_open(self):
        result = self.call(payload=["not an object"])
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
