"""Project config for the vault tool: absent and invalid states, CLI exits, and configured values."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / '.claude/tools'
CONFIG = 'vault_sync.project.json'


def stage_tool(base, config=None):
    """Copy the tool beside an optional config text into base; return the staged tool path."""
    tools = Path(base) / 'tools'
    tools.mkdir()
    for name in ('vault_sync.py', 'vault_read.py'):
        shutil.copy(TOOLS / name, tools / name)
    if config is not None:
        (tools / CONFIG).write_text(config, encoding='utf-8')
    return tools / 'vault_sync.py'


def load(tool):
    spec = importlib.util.spec_from_file_location('vault_sync_staged', tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(tool, *args):
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True, text=True)


class Absent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tool = stage_tool(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_records_absent_without_raising(self):
        vs = load(self.tool)
        self.assertEqual(vs.CONFIG_STATE, 'absent')
        self.assertIn('absent', vs.CONFIG_REASON)

    def test_every_subcommand_exits_two_with_the_not_configured_line(self):
        line = 'vault sync NOT-CONFIGURED: .claude/tools/vault_sync.project.json is absent'
        for args in (['scan', '--tree'], ['precommit'], ['init'], ['sync'], ['install-task', '--dry-run'],
                     ['install-task', '--dry-run', '--pythonw', 'x']):
            result = run(self.tool, *args)
            self.assertEqual(result.returncode, 2, (args, result.stdout, result.stderr))
            self.assertEqual(result.stderr.strip(), line, args)

    def test_mirror_prints_the_unavailable_line_on_stdout_and_exits_two(self):
        result = run(self.tool, 'mirror')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         'vault mirror UNAVAILABLE: .claude/tools/vault_sync.project.json is absent')


class Invalid(unittest.TestCase):
    def state(self, config):
        with tempfile.TemporaryDirectory() as tmp:
            return load(stage_tool(tmp, config))

    def check(self, config, needle):
        vs = self.state(config)
        self.assertEqual(vs.CONFIG_STATE, 'invalid', vs.CONFIG_REASON)
        self.assertIn(needle, vs.CONFIG_REASON)

    def good(self, **over):
        return json.dumps({**{'folders': ['Proj'], 'gitDirName': 'g', 'taskName': 't'}, **over})

    def test_the_ai_folder(self):
        self.check(self.good(folders=['AI']), 'AI')

    def test_a_folder_with_a_separator(self):
        self.check(self.good(folders=['a/b']), 'folders')
        self.check(self.good(folders=['a\\b']), 'folders')

    def test_dot_segments_and_empty_names(self):
        for bad in ('.', '..', ''):
            self.check(self.good(folders=[bad]), 'folders')

    def test_an_empty_list(self):
        self.check(self.good(folders=[]), 'folders')

    def test_a_missing_key(self):
        self.check(json.dumps({'folders': ['Proj'], 'gitDirName': 'g'}), 'taskName')

    def test_bad_json(self):
        self.check('{not json', 'JSON')

    def test_names_with_a_separator_or_empty(self):
        self.check(self.good(gitDirName='a/b'), 'gitDirName')
        self.check(self.good(taskName=''), 'taskName')

    def test_invalid_config_makes_the_cli_name_the_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = stage_tool(tmp, self.good(folders=['AI']))
            result = run(tool, 'precommit')
            self.assertEqual(result.returncode, 2)
            self.assertTrue(result.stderr.startswith('vault sync NOT-CONFIGURED: '), result.stderr)
            self.assertIn('AI', result.stderr)
            mirror = run(tool, 'mirror')
            self.assertEqual(mirror.returncode, 2)
            self.assertTrue(mirror.stdout.startswith('vault mirror UNAVAILABLE: '), mirror.stdout)


class Configured(unittest.TestCase):
    def test_the_file_values_drive_exclude_and_task_arguments(self):
        config = json.dumps({'folders': ['Proj', 'Other'], 'gitDirName': 'vault-git-x', 'taskName': 'VaultSyncX'})
        with tempfile.TemporaryDirectory() as tmp:
            vs = load(stage_tool(tmp, config))
            self.assertEqual(vs.CONFIG_STATE, 'configured')
            self.assertIn('!/Proj/', vs.EXCLUDE.splitlines())
            self.assertIn('!/Other/', vs.EXCLUDE.splitlines())
            self.assertEqual([line for line in vs.EXCLUDE.splitlines() if line.startswith('!/')],
                             ['!/Proj/', '!/Other/'])
            args = vs.task_args('pythonw.exe', 'tool.py')
            self.assertEqual(args[args.index('/TN') + 1], 'VaultSyncX')
            self.assertEqual(vs.default_git_dir().name, 'vault-git-x')

    def test_configure_rederives_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            vs = load(stage_tool(tmp))
            vs.configure(['Proj'], 'g', 't')
            self.assertEqual(vs.CONFIG_STATE, 'configured')
            self.assertIn('!/Proj/', vs.EXCLUDE.splitlines())
            with self.assertRaises(ValueError):
                vs.configure(['AI'], 'g', 't')


if __name__ == '__main__':
    unittest.main()
