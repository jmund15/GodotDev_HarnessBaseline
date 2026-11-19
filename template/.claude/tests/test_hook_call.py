"""Proof of tests/_hook_call.py: a hook's `main()` runs in this process with the verdict a spawned run gives,
and nothing a call touches crosses into the next case.

    python3 .claude/tests/test_hook_call.py
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook_call  # noqa: E402

PROBE = textwrap.dedent('''
    import json, os, sys
    def main():
        payload = json.load(sys.stdin)
        deny = os.environ.get("PROBE_DENY") == payload["tool_input"]["command"] and os.getcwd().endswith("here")
        if deny:
            print(json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}}))
            sys.stderr.write("denied")
            return 2
        print(json.dumps({"hookSpecificOutput": {"permissionDecision": "allow"}}))
        return 0
    if __name__ == "__main__":
        sys.exit(main())
''')


def payload(command):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


class HookCallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "here").mkdir()
        self.here = self.root / "here"

    def plant(self, name, text, sub="hooks"):
        path = self.root / sub / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_same_verdict_as_spawned_run(self):
        hook = self.plant("probe.py", PROBE)
        for command, want_code, want_decision in (("rm x", 2, "deny"), ("ls", 0, "allow")):
            env = dict(os.environ, PROBE_DENY="rm x")
            spawned = subprocess.run([sys.executable, hook], input=payload(command), capture_output=True,
                                     text=True, env=env, cwd=self.here)
            code, out, err, hso = _hook_call.call(hook, payload(command), env=env, cwd=str(self.here))
            self.assertEqual((spawned.returncode, code), (want_code, want_code))
            self.assertEqual(hso.get("permissionDecision"), want_decision)
            self.assertEqual(json.loads(spawned.stdout)["hookSpecificOutput"], hso)
            self.assertEqual(spawned.stderr, err)

    def test_raising_hook_is_exit_1_with_traceback(self):
        hook = self.plant("boom.py", "def main():\n    raise RuntimeError('planted boom')\n")
        code, _, err, hso = _hook_call.call(hook, payload("x"))
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError: planted boom", err)
        self.assertEqual(hso, {})

    def test_environment_and_cwd_restored_after_a_raise(self):
        hook = self.plant("boom.py", "def main():\n    raise RuntimeError('x')\n")
        before = (dict(os.environ), os.getcwd(), list(sys.path), sys.argv[:])
        _hook_call.call(hook, payload("x"), env={"ONLY_THIS": "1"}, cwd=str(self.here))
        self.assertEqual((dict(os.environ), os.getcwd(), list(sys.path), sys.argv[:]), before)

    def test_module_counter_is_fresh_in_the_next_call(self):
        hook = self.plant("count.py", "import sys\nN = 0\ndef main():\n    global N\n    N += 1\n    print(N)\n")
        first = _hook_call.call(hook, payload("x"))
        second = _hook_call.call(hook, payload("x"))
        self.assertEqual((first[1].strip(), second[1].strip()), ("1", "1"))

    def test_counter_in_an_imported_tools_module_is_fresh(self):
        self.plant("_counter_mod.py", "N = 0\n", sub="tools")
        hook = self.plant("count_tools.py", textwrap.dedent('''
            import os, sys
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
            import _counter_mod
            def main():
                _counter_mod.N += 1
                print(_counter_mod.N)
        '''))
        first = _hook_call.call(hook, payload("x"))
        second = _hook_call.call(hook, payload("x"))
        self.assertEqual((first[0], first[1].strip(), second[1].strip()), (0, "1", "1"))
        self.assertNotIn("_counter_mod", sys.modules)

    def test_imports_its_underscore_sibling(self):
        self.plant("_sibling.py", "VALUE = 'from sibling'\n")
        hook = self.plant("uses_sibling.py", "import _sibling\ndef main():\n    print(_sibling.VALUE)\n")
        code, out, _, _ = _hook_call.call(hook, payload("x"))
        self.assertEqual((code, out.strip()), (0, "from sibling"))

    def test_import_time_raise_is_exit_1_with_traceback(self):
        hook = self.plant("bad_import.py", "import module_that_is_not_there\ndef main():\n    pass\n")
        code, _, err, _ = _hook_call.call(hook, payload("x"))
        self.assertEqual(code, 1)
        self.assertIn("ModuleNotFoundError", err)

    def test_argv_reaches_the_hook(self):
        hook = self.plant("argv.py", "import sys\ndef main():\n    print(sys.argv[1:])\n")
        _, out, _, _ = _hook_call.call(hook, payload("x"), argv=("--flag",))
        self.assertEqual(out.strip(), "['--flag']")

    def test_planted_temp_dir_reaches_the_hook(self):
        """`tempfile` caches its first answer process-wide, so a swapped TMP/TEMP/TMPDIR must not be lost."""
        hook = self.plant("tempdir.py", "import tempfile\ndef main():\n    print(tempfile.gettempdir())\n")
        planted = self.root / "planted_tmp"
        planted.mkdir()
        env = dict(os.environ, TMP=str(planted), TEMP=str(planted), TMPDIR=str(planted))
        _, out, _, _ = _hook_call.call(hook, payload("x"), env=env)
        self.assertEqual(os.path.normcase(out.strip()), os.path.normcase(str(planted)))
        self.assertNotEqual(os.path.normcase(tempfile.gettempdir()), os.path.normcase(str(planted)))


class RepoTemplateTest(unittest.TestCase):
    def test_commit_in_a_copy_does_not_reach_the_next_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            template, work = tmp / "template", tmp / "work"
            template.mkdir()

            def git(cwd, *args):
                return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                                      check=True, capture_output=True, text=True).stdout.strip()
            git(template, "init", "-q")
            (template / "a.txt").write_text("base\n")
            git(template, "add", ".")
            git(template, "commit", "-q", "-m", "base")
            base = git(template, "rev-parse", "HEAD")

            _hook_call.copy_repo(template, work)
            (work / "a.txt").write_text("changed\n")
            git(work, "commit", "-q", "-am", "second")
            self.assertNotEqual(git(work, "rev-parse", "HEAD"), base)

            _hook_call.copy_repo(template, work)
            self.assertEqual(git(work, "rev-parse", "HEAD"), base)
            self.assertEqual((work / "a.txt").read_text(), "base\n")
            self.assertEqual(git(template, "rev-parse", "HEAD"), base)


if __name__ == "__main__":
    unittest.main()
