#!/usr/bin/env python3
"""Contract proof for .claude/hooks/wide_test_guard.py (PreToolUse on Bash).

A delegate transcript (under `subagents/`) running an unfiltered `dotnet test` is denied when its brief does
not name the run. The same run is allowed when the brief names it, and the main session is never refused.
Payloads are planted on stdin; no test runs. Writes only under its temp root.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIDE = "dotnet test"
ENV_KEYS = ("CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_PROMPT_FILE", "HARNESS_WIDE_RUN_ADMITTED")


class WideTestGuardContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="wide-test-guard-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        hooks = self.temp / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        self.guard = hooks / "wide_test_guard.py"
        for name in ("wide_test_guard.py", "_command_text.py"):
            shutil.copy(ROOT / ".claude" / "hooks" / name, hooks / name)
        tools = self.temp / ".claude" / "tools"
        tools.mkdir(parents=True)
        shutil.copy(ROOT / ".claude" / "tools" / "sidecar_argv.py", tools / "sidecar_argv.py")
        self.env = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}

    def transcript(self, brief):
        folder = self.temp / "session" / "subagents"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "agent-contract.jsonl"
        line = {"message": {"role": "user", "content": brief}}
        path.write_text(json.dumps(line) + "\n", encoding="utf-8")
        return path

    def run_hook(self, transcript, command=WIDE):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.temp.as_posix()}
        if transcript is not None:
            payload["transcript_path"] = transcript.as_posix()
        done = subprocess.run([sys.executable, str(self.guard)], input=json.dumps(payload), cwd=self.temp,
                              env=self.env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(done.stdout or "{}")

    def test_delegate_unfiltered_run_is_denied_when_brief_does_not_name_it(self):
        out = self.run_hook(self.transcript("Fix the parser. Run the parser filter only."))
        decision = out.get("hookSpecificOutput", {})
        self.assertEqual(decision.get("permissionDecision"), "deny", out)
        self.assertIn("Wide test run refused", decision.get("permissionDecisionReason", ""))

    def test_delegate_run_named_in_brief_is_allowed(self):
        out = self.run_hook(self.transcript("Fix the parser. Run exactly: " + WIDE))
        self.assertNotIn("permissionDecision", out.get("hookSpecificOutput", {}), out)

    def test_main_session_is_never_refused(self):
        out = self.run_hook(None)
        self.assertNotIn("permissionDecision", out.get("hookSpecificOutput", {}), out)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
