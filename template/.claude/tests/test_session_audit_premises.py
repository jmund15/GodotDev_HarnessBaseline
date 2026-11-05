"""The harness-quality lens stays registered: its mandate key resolves and `review_ledger.py keys --check` reports no problem against it."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class HarnessQualityMandateTests(unittest.TestCase):
    def test_accessor_emits_the_lens(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / ".claude/tools/lens.py"), "get", "--shared", "sa-harness-quality"],
            cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.strip())

    def test_existing_mandate_stays_registered(self):
        source = (ROOT / ".claude/commands/agents/session_audit_agents.md").read_text(encoding="utf-8")
        section = source.split("### sa-harness-quality", 1)[1].split("\n---", 1)[0]
        self.assertIn("mandates: harness-quality\n", section)
        result = subprocess.run(
            [sys.executable, str(ROOT / ".claude/tools/review_ledger.py"), "keys", "--check"],
            cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        # The check judges every review registry; a consumer's forked review registry is its own concern
        # (test_lens_catalog_contract), so this proof reads only the problems that name its mandate.
        self.assertNotIn("Traceback", result.stderr)
        own = [line for line in result.stderr.splitlines() if "harness-quality" in line]
        self.assertEqual(own, [], result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
