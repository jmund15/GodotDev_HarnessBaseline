#!/usr/bin/env python3
"""Contract proof for .claude/hooks/prompt_dispatch.py.

A bad argument exits 2 with usage (subprocess). In-process, a chain of planted members runs over one
payload: a member that returns a JSON additionalContext reaches the output, a member that returns 3 is
named as exit3 on stderr and its output is dropped, and a chain entry whose file is absent is skipped
with no fault while its siblings still deliver. Members are planted modules, never project hooks.
Writes only under its temp root.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OK_MEMBER = "import json\n\ndef main():\n    print(json.dumps({'hookSpecificOutput': {'additionalContext': 'member ok'}}))\n    return 0\n"
EXIT_MEMBER = "def main():\n    print('member exit')\n    return 3\n"


class PromptDispatchContract(unittest.TestCase):
    def setUp(self):
        for name in ("prompt_dispatch", "_optional_hooks", "_hook_state"):
            sys.modules.pop(name, None)
        self.temp = Path(tempfile.mkdtemp(prefix="prompt-dispatch-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.hooks = self.temp / ".claude" / "hooks"
        self.hooks.mkdir(parents=True)
        for name in ("prompt_dispatch.py", "_hook_state.py", "_optional_hooks.py"):
            shutil.copy(ROOT / ".claude" / "hooks" / name, self.hooks / name)
        (self.hooks / "contract_ok_member.py").write_text(OK_MEMBER, encoding="utf-8")
        (self.hooks / "contract_exit_member.py").write_text(EXIT_MEMBER, encoding="utf-8")
        self.timing = self.temp / "logs" / "prompt_dispatch_timing.jsonl"

    def load_dispatcher(self):
        sys.path.insert(0, str(self.hooks))
        self.addCleanup(sys.path.remove, str(self.hooks))
        import prompt_dispatch

        return prompt_dispatch

    def test_bad_argument_exits_2_with_usage(self):
        done = subprocess.run([sys.executable, str(self.hooks / "prompt_dispatch.py"), "--bogus"],
                              cwd=self.temp, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("usage: prompt_dispatch.py", done.stderr)

    def test_chain_delivers_ok_member_and_drops_exit_member(self):
        prompt_dispatch = self.load_dispatcher()

        chain = (("contract_ok_member", 20), ("contract_exit_member", 20))
        code, stdout, stderr = prompt_dispatch.dispatch("{}", chain=chain, timing_path=self.timing)
        self.assertEqual(code, 0)
        self.assertIn("member ok", stdout)
        self.assertNotIn("member exit", stdout)
        self.assertIn("exit3", stderr)
        record = json.loads(self.timing.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["hooks"]["contract_exit_member"]["status"], "exit3")
        self.assertEqual(record["hooks"]["contract_ok_member"]["status"], "ok")

    def test_absent_chain_member_is_skipped_and_siblings_deliver(self):
        prompt_dispatch = self.load_dispatcher()

        chain = (("contract_absent_member", 20), ("contract_ok_member", 20))
        code, stdout, stderr = prompt_dispatch.dispatch("{}", chain=chain, timing_path=self.timing)
        self.assertEqual(code, 0)
        self.assertIn("member ok", stdout)
        self.assertNotIn("contract_absent_member", stderr)
        record = json.loads(self.timing.read_text(encoding="utf-8").splitlines()[-1])
        self.assertNotIn("contract_absent_member", record["hooks"])
        self.assertEqual(record["hooks"]["contract_ok_member"]["status"], "ok")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
