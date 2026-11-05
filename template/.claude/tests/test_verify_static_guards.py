"""verify.ps1 runs the gate's static guard set before its suites, so a guard red surfaces at a Part close."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUB = '''param([string] $Repo, [string] $Only, [switch] $IgnoreEditor, [switch] $RetryOnly)
Write-Output "STUB only=$Only retry=$($RetryOnly.IsPresent)"
exit 0
'''
MAP = json.dumps({'schema_version': 1, 'domains': {'Prototypes': {
    'logic_partition': None, 'integration_segments': ['Tests/Integration/Prototypes']}},
    'merges': {}, 'excluded_namespaces': {}})
PASSING = 'def main():\n    return 0\n'
FAILING = 'def main():\n    print("REFCOUNT_GUARD_FINDING")\n    return 1\n'


def generic_guard_files():
    text = (ROOT / '.claude/scripts/StaticGuards.ps1').read_text(encoding='utf-8')
    return re.findall(r"=\s*'(\w+\.py)'", text)


class VerifyStaticGuardsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='verify_guards_'))
        scripts = self.tmp / '.claude/scripts'
        scripts.mkdir(parents=True)
        (scripts / 'gate_domain_map.json').write_text(MAP, encoding='utf-8', newline='\n')
        for src in (ROOT / '.claude/scripts').glob('*.ps1'):
            shutil.copy(src, scripts / src.name)
        hooks = self.tmp / '.claude/hooks'
        hooks.mkdir()
        shutil.copy(ROOT / '.claude/hooks/wide_test_guard.py', hooks / 'wide_test_guard.py')
        self.guards = generic_guard_files()
        for name in self.guards:
            (hooks / name).write_text(PASSING, encoding='utf-8', newline='\n')
        (scripts / 'run_integration_batched.ps1').write_text(STUB, encoding='utf-8', newline='\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_verify(self, *extra):
        return subprocess.run(['pwsh', '-NoProfile', '-File', str(self.tmp / '.claude/scripts/verify.ps1'),
                               '-Repo', str(self.tmp), '-Scope', 'Prototypes', '-IntegrationOnly', *extra],
                              capture_output=True, text=True, timeout=120)

    def test_the_gate_guard_table_is_the_one_verify_runs(self):
        self.assertIn('refcounted_free_guard.py', self.guards)

    def test_green_guards_let_the_suites_run(self):
        run = self.run_verify()
        out = run.stdout + run.stderr
        self.assertEqual(run.returncode, 0, out)
        self.assertIn('GUARDS nullstrip=OK', out)
        self.assertIn('refcount_free=OK', out)
        self.assertIn('STUB only=Prototypes retry=False', out)

    def test_a_failing_guard_blocks_before_any_suite(self):
        (self.tmp / '.claude/hooks/refcounted_free_guard.py').write_text(FAILING, encoding='utf-8', newline='\n')
        run = self.run_verify()
        out = run.stdout + run.stderr
        self.assertEqual(run.returncode, 4, out)
        self.assertIn('refcount_free=FAIL', out)
        self.assertIn('REFCOUNT_GUARD_FINDING', out)
        self.assertIn('VERIFY=BLOCKED', out)
        self.assertNotIn('STUB', out)

    def test_a_missing_guard_blocks_as_unavailable(self):
        (self.tmp / '.claude/hooks/tool_cascade_audit.py').unlink()
        run = self.run_verify()
        out = run.stdout + run.stderr
        self.assertEqual(run.returncode, 4, out)
        self.assertIn('tool_cascade=UNAVAILABLE', out)
        self.assertNotIn('STUB', out)

    def test_retry_only_continues_a_run_whose_guards_already_passed(self):
        (self.tmp / '.claude/hooks/refcounted_free_guard.py').write_text(FAILING, encoding='utf-8', newline='\n')
        run = self.run_verify('-RetryOnly')
        out = run.stdout + run.stderr
        self.assertIn('STUB only=Prototypes retry=True', out)
        self.assertNotIn('GUARDS', out)


if __name__ == '__main__':
    unittest.main()
