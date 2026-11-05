"""Scheduled task command, windowless interpreter lookup, dry-run and error contracts."""
import importlib.util
import shutil
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / '.claude/tools/vault_sync.py'
spec = importlib.util.spec_from_file_location('vault_sync', TOOL)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)
vs.configure(['Proj'], 'vault-git-test', 'VaultSyncTest')

_STAGE = tempfile.TemporaryDirectory()
for _name in ('vault_sync.py', 'vault_read.py'):
    shutil.copy(ROOT / '.claude/tools' / _name, Path(_STAGE.name) / _name)
(Path(_STAGE.name) / 'vault_sync.project.json').write_text(
    json.dumps({'folders': ['Proj'], 'gitDirName': 'vault-git-test', 'taskName': 'VaultSyncTest'}), encoding='utf-8')
STAGED = Path(_STAGE.name) / 'vault_sync.py'


class TaskArgs(unittest.TestCase):
    def test_command_registers_a_repeating_windowless_sync(self):
        args = vs.task_args('C:/Py/pythonw.exe', 'C:/repo/.claude/tools/vault_sync.py')
        self.assertEqual(args[:3], ['schtasks', '/Create', '/F'])
        pairs = dict(zip(args[3::2], args[4::2]))
        self.assertEqual((pairs['/SC'], pairs['/MO'], pairs['/TN']), ('MINUTE', '15', 'VaultSyncTest'))
        self.assertEqual(pairs['/TR'], '"C:/Py/pythonw.exe" "C:/repo/.claude/tools/vault_sync.py" sync')

    def test_interval_is_the_argument_when_given(self):
        args = vs.task_args('pythonw.exe', 'tool.py', minutes=5)
        self.assertEqual(args[args.index('/MO') + 1], '5')

    def test_git_dir_takes_the_configured_name(self):
        with mock.patch.dict(vs.os.environ, {'LOCALAPPDATA': 'C:/Local'}):
            self.assertEqual(vs.default_git_dir(), Path('C:/Local/vault-git-test'))


class Pythonw(unittest.TestCase):
    def test_sibling_of_the_running_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'python.exe').write_text('', encoding='utf-8')
            (Path(tmp) / 'pythonw.exe').write_text('', encoding='utf-8')
            with mock.patch.object(sys, 'executable', str(Path(tmp) / 'python.exe')):
                self.assertEqual(vs.pythonw_path(), Path(tmp) / 'pythonw.exe')

    def test_absent_sibling_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'python3').write_text('', encoding='utf-8')
            with mock.patch.object(sys, 'executable', str(Path(tmp) / 'python3')):
                self.assertIsNone(vs.pythonw_path())


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(STAGED), 'install-task', *args], capture_output=True, text=True)

    def test_dry_run_prints_the_command_and_registers_nothing(self):
        result = self.run_cli('--dry-run', '--pythonw', 'C:/Py/pythonw.exe')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        self.assertEqual(args[:3], ['schtasks', '/Create', '/F'])
        self.assertIn('C:/Py/pythonw.exe', args[args.index('/TR') + 1])

    def test_no_windowless_interpreter_is_an_error_and_registers_nothing(self):
        args = vs.argparse.Namespace(pythonw=None, dry_run=False)
        with mock.patch.object(vs, 'pythonw_path', lambda: None), \
                mock.patch.object(vs.subprocess, 'run', side_effect=AssertionError('registered a task')):
            self.assertEqual(vs._install_task(args), 2)

    def test_registration_runs_the_built_command_and_returns_its_exit_code(self):
        args = vs.argparse.Namespace(pythonw='C:/Py/pythonw.exe', dry_run=False)
        done = subprocess.CompletedProcess([], 5, stdout='', stderr='denied')
        with mock.patch.object(vs.subprocess, 'run', return_value=done) as run:
            self.assertEqual(vs._install_task(args), 5)
        self.assertEqual(run.call_args.args[0][:3], ['schtasks', '/Create', '/F'])


if __name__ == '__main__':
    unittest.main()
