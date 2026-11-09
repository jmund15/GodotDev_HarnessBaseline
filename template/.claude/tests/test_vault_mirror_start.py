"""Cloud mirror line, local status, timeout failures and the real tool on a planted origin."""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.dumps({'folders': ['Proj'], 'gitDirName': 'vault-git-test', 'taskName': 'VaultSyncTest'})


def stage_shared(base, config=CONFIG):
    """The hook and the tool it imports, copied beside an optional config so the real project file is never read."""
    hooks, tools = base / '.claude/hooks', base / '.claude/tools'
    hooks.mkdir(parents=True)
    tools.mkdir(parents=True)
    for name in ('vault_mirror_start.py', '_bounded_run.py', '_claude_scope.py'):
        shutil.copy(ROOT / '.claude/hooks' / name, hooks / name)
    for name in ('vault_sync.py', 'vault_read.py'):
        shutil.copy(ROOT / '.claude/tools' / name, tools / name)
    if config is not None:
        (tools / 'vault_sync.project.json').write_text(config, encoding='utf-8')
    return hooks / 'vault_mirror_start.py'


def git(repo, *args):
    subprocess.run(['git', '-C', str(repo), '-c', 'user.email=t@t', '-c', 'user.name=t', *args],
                   check=True, capture_output=True)


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.project = self.base / 'project'
        (self.project / '.claude/tools').mkdir(parents=True)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.shared = self.base / 'shared'
        self.hook = stage_shared(self.shared)
        self.tool = self.shared / '.claude/tools/vault_sync.py'

    def tearDown(self):
        for base, dirs, files in os.walk(self.base):
            for name in dirs + files:
                try:
                    os.chmod(Path(base) / name, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
                except OSError:
                    pass
        self.tmp.cleanup()

    def run_hook(self, cloud=True, timeout=120, hook=None):
        env = {**os.environ, 'CLAUDE_PROJECT_DIR': str(self.project), 'HOME': str(self.home),
               'USERPROFILE': str(self.home), 'LOCALAPPDATA': str(self.base / 'local')}
        env.pop('CLAUDE_CODE_REMOTE', None)
        if cloud:
            env['CLAUDE_CODE_REMOTE'] = 'true'
        return subprocess.run([sys.executable, str(hook or self.hook)], env=env, capture_output=True, text=True, timeout=timeout)

    def stub_tool(self, body):
        (self.project / '.claude/tools/vault_sync.py').write_text(body, encoding='utf-8')

    def test_silent_off_the_cloud(self):
        self.stub_tool('print("vault mirror: should not run")\n')
        result = self.run_hook(cloud=False)
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    def test_cloud_prints_the_tool_line_once(self):
        self.stub_tool('print("vault mirror: abc1234, last vault change 2026-10-20 (4 h ago)")\n')
        result = self.run_hook()
        self.assertEqual((result.returncode, result.stdout.strip()),
                         (0, '[vault] vault mirror: abc1234, last vault change 2026-10-20 (4 h ago)'))

    def test_cloud_receives_the_path_scoped_rule_but_local_does_not(self):
        rule = self.project / '.claude/rules/vault_mirror.md'
        rule.parent.mkdir(parents=True)
        rule.write_text('---\npaths: ["**/vault_sync.py"]\n---\n# Design vault mirror\nRead-only planted contract.\n', encoding='utf-8')
        self.stub_tool('print("vault mirror: abc1234")\n')
        cloud = self.run_hook()
        self.assertEqual(cloud.returncode, 0, cloud.stderr)
        self.assertIn('<cloud-session>\n# Design vault mirror\nRead-only planted contract.\n</cloud-session>', cloud.stdout)
        self.assertNotIn('paths:', cloud.stdout)
        self.assertEqual(self.run_hook(cloud=False).stdout, '')

    def plant_status(self, ok, age_minutes, error=None, refusals=()):
        git_dir = self.base / 'local' / 'vault-git-test'
        git_dir.mkdir(parents=True, exist_ok=True)
        at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - age_minutes * 60))
        (git_dir / 'sync_status.json').write_text(json.dumps(
            {'at': at, 'ok': ok, 'code': 0 if ok else 1, 'error': error, 'refusals': list(refusals)}), encoding='utf-8')

    def test_off_the_cloud_a_refused_sync_prints_its_reason(self):
        self.plant_status(False, 5, refusals=[{'kind': 'secret', 'path': 'a.md', 'pattern': 'ghp'}])
        result = self.run_hook(cloud=False)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith('[vault] sync REFUSED'), result.stdout)
        self.assertIn('secret', result.stdout)

    def test_off_the_cloud_a_stale_sync_prints_its_age(self):
        self.plant_status(True, 90)
        result = self.run_hook(cloud=False)
        self.assertTrue(result.stdout.startswith('[vault] sync STALE'), result.stdout)

    def test_off_the_cloud_a_healthy_sync_and_a_machine_without_one_stay_silent(self):
        self.assertEqual(self.run_hook(cloud=False).stdout, '')
        self.plant_status(True, 5)
        self.assertEqual(self.run_hook(cloud=False).stdout, '')

    def test_off_the_cloud_a_present_but_unreadable_status_reads_unknown(self):
        git_dir = self.base / 'local' / 'vault-git-test'
        git_dir.mkdir(parents=True, exist_ok=True)
        (git_dir / 'sync_status.json').write_text('{not json', encoding='utf-8')
        result = self.run_hook(cloud=False)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith('[vault] sync UNKNOWN: status unreadable (JSONDecodeError)'), result.stdout)
        (git_dir / 'sync_status.json').write_text('{"ok": true}', encoding='utf-8')
        result = self.run_hook(cloud=False)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith('[vault] sync UNKNOWN: status unreadable (KeyError)'), result.stdout)

    def test_a_tool_that_prints_nothing_reads_unavailable(self):
        self.stub_tool('import sys\nsys.exit(3)\n')
        result = self.run_hook()
        self.assertEqual(result.returncode, 0)
        self.assertIn('vault mirror UNAVAILABLE', result.stdout)

    def test_a_missing_tool_reads_unavailable(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0)
        self.assertIn('vault mirror UNAVAILABLE', result.stdout)

    def test_a_grandchild_holding_the_pipe_does_not_outlast_the_timeout(self):
        self.stub_tool('import subprocess, sys, time\n'
                       'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(25)"])\n'
                       'time.sleep(25)\n')
        spec = importlib.util.spec_from_file_location('vault_mirror_start', self.hook)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        started = time.monotonic()
        line = hook.mirror_line(self.project, timeout=1)
        self.assertIn('UNAVAILABLE', line)
        self.assertLess(time.monotonic() - started, 15)

    def test_a_hung_tool_times_out_to_unavailable(self):
        self.stub_tool('import time\ntime.sleep(30)\n')
        spec = importlib.util.spec_from_file_location('vault_mirror_start', self.hook)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        self.assertIn('UNAVAILABLE', hook.mirror_line(self.project, timeout=1))

    def test_real_tool_mirrors_a_planted_vault_branch(self):
        for tool in self.tool.parent.iterdir():
            shutil.copy(tool, self.project / '.claude/tools' / tool.name)
        remote, author = self.base / 'remote.git', self.base / 'author'
        subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True, capture_output=True)
        author.mkdir()
        git(author, 'init', '-q')
        git(author, 'checkout', '-q', '--orphan', 'vault')
        (author / 'Proj').mkdir()
        (author / 'Proj/a.md').write_text('design', encoding='utf-8')
        git(author, 'add', '-A')
        git(author, 'commit', '-q', '-m', 'sync')
        git(author, 'push', '-q', str(remote), 'vault:vault')
        git(self.project, 'init', '-q')
        git(self.project, 'remote', 'add', 'origin', str(remote))
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('[vault] vault mirror: '), result.stdout)
        mirrored = self.home / 'OneDrive/Documents/ObsidianVault/DevProjects/Proj/a.md'
        self.assertEqual(mirrored.read_text(encoding='utf-8'), 'design')

    def test_absent_config_prints_nothing_on_or_off_the_cloud(self):
        hook = stage_shared(self.base / 'bare', config=None)
        rule = self.project / '.claude/rules/vault_mirror.md'
        rule.parent.mkdir(parents=True)
        rule.write_text('# Design vault mirror\n', encoding='utf-8')
        self.stub_tool('print("vault mirror: should not run")\n')
        for cloud in (True, False):
            result = self.run_hook(cloud=cloud, hook=hook)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''), cloud)

    def test_invalid_config_is_named_on_and_off_the_cloud(self):
        hook = stage_shared(self.base / 'bad', config='{not json')
        off = self.run_hook(cloud=False, hook=hook)
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertTrue(off.stdout.startswith('[vault] sync NOT-CONFIGURED: '), off.stdout)
        on = self.run_hook(cloud=True, hook=hook)
        self.assertEqual(on.returncode, 0, on.stderr)
        self.assertTrue(on.stdout.startswith('[vault] vault mirror UNAVAILABLE: '), on.stdout)
        self.assertNotIn('<cloud-session>', on.stdout)


if __name__ == '__main__':
    unittest.main()
