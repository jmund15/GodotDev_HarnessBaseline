"""verify.ps1 -RetryOnly reaches the integration batch runner, the step its budget-exceeded line names."""
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


class VerifyRetryOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='verify_retry_'))
        scripts = self.tmp / '.claude/scripts'
        scripts.mkdir(parents=True)
        shutil.copy(ROOT / '.claude/scripts/verify.ps1', scripts / 'verify.ps1')
        # A map that matches this fixture's Tests tree: the gate's static guards check the map against the tree.
        (scripts / 'gate_domain_map.json').write_text(json.dumps({
            'schema_version': 1, 'domains': {'Prototypes': {'logic_partition': None,
                                                           'integration_segments': ['Tests/Integration/Prototypes']}},
            'merges': {}, 'excluded_namespaces': {}}), encoding='utf-8', newline='\n')
        (self.tmp / 'Tests/Logic').mkdir(parents=True)
        (self.tmp / 'Tests/Integration/Prototypes').mkdir(parents=True)
        for src in (ROOT / '.claude/scripts').glob('*.ps1'):
            shutil.copy(src, scripts / src.name)
        (self.tmp / '.claude/hooks').mkdir()
        shutil.copy(ROOT / '.claude/hooks/wide_test_guard.py', self.tmp / '.claude/hooks/wide_test_guard.py')
        guards = re.findall(r"=\s*'(\w+\.py)'", (ROOT / '.claude/scripts/StaticGuards.ps1').read_text(encoding='utf-8'))
        for name in guards:
            (self.tmp / '.claude/hooks' / name).write_text('def main():\n    return 0\n', encoding='utf-8', newline='\n')
        (scripts / 'run_integration_batched.ps1').write_text(STUB, encoding='utf-8', newline='\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_verify(self, *extra):
        return subprocess.run(['pwsh', '-NoProfile', '-File', str(self.tmp / '.claude/scripts/verify.ps1'),
                               '-Repo', str(self.tmp), '-Scope', 'Prototypes', '-IntegrationOnly', *extra],
                              capture_output=True, text=True, timeout=120)

    def test_retry_only_is_forwarded(self):
        run = self.run_verify('-RetryOnly')
        self.assertIn('STUB only=Prototypes retry=True', run.stdout, run.stdout + run.stderr)

    def test_plain_run_does_not_retry(self):
        run = self.run_verify()
        self.assertIn('STUB only=Prototypes retry=False', run.stdout, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
