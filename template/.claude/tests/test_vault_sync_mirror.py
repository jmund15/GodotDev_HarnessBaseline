"""Mirror extraction, read-only modes, age, own-folder replacement and unavailable failures."""
import importlib.util
import json
import shutil
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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
COMMITTED = '2026-10-20T00:00:00+00:00'
NOW = datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)


def git(repo, *args, env=None):
    result = subprocess.run(['git', '-C', str(repo), '-c', 'user.email=t@t', '-c', 'user.name=t', *args],
                            check=True, capture_output=True, text=True, env={**os.environ, **(env or {})})
    return result.stdout.strip()


def plant(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')


class Mirror(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.remote = self.base / 'remote.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(self.remote)], check=True, capture_output=True)
        self.author = self.base / 'author'
        self.author.mkdir()
        git(self.author, 'init', '-q')
        git(self.author, 'checkout', '-q', '--orphan', 'vault')
        git(self.author, 'remote', 'add', 'origin', str(self.remote))
        self.clone = self.base / 'clone'
        self.clone.mkdir()
        git(self.clone, 'init', '-q')
        git(self.clone, 'remote', 'add', 'origin', str(self.remote))
        self.target = self.base / 'home' / 'OneDrive' / 'DevProjects'

    def tearDown(self):
        for base, dirs, files in os.walk(self.base):
            for name in dirs + files:
                try:
                    os.chmod(Path(base) / name, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
                except OSError:
                    pass
        self.tmp.cleanup()

    def publish(self, files, remove=()):
        for rel in remove:
            (self.author / rel).unlink()
        plant(self.author, files)
        git(self.author, 'add', '-A')
        git(self.author, 'commit', '-q', '-m', 'sync', env={'GIT_COMMITTER_DATE': COMMITTED, 'GIT_AUTHOR_DATE': COMMITTED})
        git(self.author, 'push', '-q', 'origin', 'vault:vault')

    def tree(self):
        return sorted(p.relative_to(self.target).as_posix() for p in self.target.rglob('*') if p.is_file())

    def test_extract_equals_the_branch_tree(self):
        self.publish({'Proj/a.md': 'one', 'Proj/d/b.md': 'two'})
        line = vs.mirror(self.target, self.clone, now=NOW)
        self.assertEqual(self.tree(), ['Proj/a.md', 'Proj/d/b.md'])
        self.assertEqual((self.target / 'Proj/a.md').read_text(encoding='utf-8'), 'one')
        self.assertTrue(line.startswith('vault mirror: '), line)

    def test_extract_is_read_only(self):
        self.publish({'Proj/a.md': 'one'})
        vs.mirror(self.target, self.clone, now=NOW)
        mode = stat.S_IMODE((self.target / 'Proj/a.md').stat().st_mode)
        self.assertEqual(mode & 0o222, 0)

    def test_line_names_the_source_commit_and_age(self):
        self.publish({'Proj/a.md': 'one'})
        sha = git(self.author, 'rev-parse', '--short=7', 'HEAD')
        line = vs.mirror(self.target, self.clone, now=NOW)
        self.assertIn(sha, line)
        self.assertIn('2026-10-20', line)
        self.assertIn('96 h ago', line)

    def test_rerun_replaces_the_extract_exactly(self):
        self.publish({'Proj/a.md': 'one', 'Proj/gone.md': 'x'})
        vs.mirror(self.target, self.clone, now=NOW)
        self.publish({'Proj/a.md': 'changed', 'Proj/new.md': 'y'}, remove=['Proj/gone.md'])
        vs.mirror(self.target, self.clone, now=NOW)
        self.assertEqual(self.tree(), ['Proj/a.md', 'Proj/new.md'])
        self.assertEqual((self.target / 'Proj/a.md').read_text(encoding='utf-8'), 'changed')

    def test_replaces_only_own_folders_and_preserves_peer_modes(self):
        peers = {'PeerA/a.md': 'pp', 'PeerB/b.md': 'jm', 'PeerC/c.md': 'gg', 'other.md': 'x'}
        plant(self.target, peers)
        for rel in peers:
            (self.target / rel).chmod(0o444)
        before = {rel: (self.target / rel).stat().st_mode for rel in peers}
        self.publish({'Proj/a.md': 'one', 'Proj/gone.md': 'gone'})
        self.assertTrue(vs.mirror(self.target, self.clone).startswith('vault mirror: '))
        self.publish({'Proj/a.md': 'two'}, remove=['Proj/gone.md'])
        self.assertTrue(vs.mirror(self.target, self.clone).startswith('vault mirror: '))
        for rel, text in peers.items():
            self.assertTrue((self.target / rel).exists(), rel)
            self.assertEqual((self.target / rel).read_text(), text)
            self.assertEqual((self.target / rel).stat().st_mode, before[rel])
        self.assertFalse((self.target / 'Proj/gone.md').exists())

    def test_unlisted_archive_is_rejected_without_touching_previous_extract(self):
        plant(self.target, {'Proj/old.md': 'old', 'PeerA/a.md': 'peer'})
        self.publish({'Proj/a.md': 'one', 'PeerA/overwrite.md': 'bad'})
        line = vs.mirror(self.target, self.clone)
        self.assertTrue(line.startswith('vault mirror UNAVAILABLE: '), line)
        self.assertEqual(self.tree(), ['PeerA/a.md', 'Proj/old.md'])

    def test_a_writable_folder_at_the_target_is_a_live_vault_and_is_never_replaced(self):
        self.publish({'Proj/a.md': 'pushed'})
        plant(self.target, {'Proj/a.md': 'live edit', 'Proj/local-only.md': 'never pushed'})
        line = vs.mirror(self.target, self.clone, now=NOW)
        self.assertTrue(line.startswith('vault mirror UNAVAILABLE: '), line)
        self.assertIn('writable', line)
        self.assertEqual((self.target / 'Proj/a.md').read_text(encoding='utf-8'), 'live edit')
        self.assertEqual((self.target / 'Proj/local-only.md').read_text(encoding='utf-8'), 'never pushed')
        self.assertTrue(os.access(self.target / 'Proj/a.md', os.W_OK))

    def test_a_failing_removal_of_the_old_extract_keeps_the_new_one(self):
        self.publish({'Proj/a.md': 'one'})
        vs.mirror(self.target, self.clone, now=NOW)
        self.publish({'Proj/a.md': 'two'})
        real = vs.shutil.rmtree

        def strict(path, *args, **kwargs):
            if not kwargs.get('ignore_errors'):
                raise OSError('cannot remove')
            return real(path, *args, **kwargs)

        vs.shutil.rmtree = strict
        try:
            line = vs.mirror(self.target, self.clone, now=NOW)
        finally:
            vs.shutil.rmtree = real
        self.assertTrue(line.startswith('vault mirror: '), line)
        self.assertEqual((self.target / 'Proj/a.md').read_text(encoding='utf-8'), 'two')

    def test_missing_branch_reads_unavailable_and_creates_nothing(self):
        line = vs.mirror(self.target, self.clone, now=NOW)
        self.assertTrue(line.startswith('vault mirror UNAVAILABLE: '), line)
        self.assertFalse(self.target.exists())

    def test_unreachable_remote_reads_unavailable_and_keeps_the_previous_extract(self):
        self.publish({'Proj/a.md': 'one'})
        vs.mirror(self.target, self.clone, now=NOW)
        git(self.clone, 'remote', 'set-url', 'origin', str(self.base / 'no_such.git'))
        line = vs.mirror(self.target, self.clone, now=NOW)
        self.assertTrue(line.startswith('vault mirror UNAVAILABLE: '), line)
        self.assertEqual(self.tree(), ['Proj/a.md'])

    def test_a_stalled_fetch_reads_unavailable_and_keeps_the_previous_extract(self):
        self.publish({'Proj/a.md': 'one'})
        vs.mirror(self.target, self.clone, now=NOW)
        real = vs.subprocess.run

        def stalled(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get('timeout') or 0)
        vs.subprocess.run = stalled
        try:
            line = vs.mirror(self.target, self.clone, now=NOW)
        finally:
            vs.subprocess.run = real
        self.assertTrue(line.startswith('vault mirror UNAVAILABLE: '), line)
        self.assertEqual(self.tree(), ['Proj/a.md'])

    def test_cli_prints_the_line_and_exits_zero_even_when_unavailable(self):
        result = subprocess.run([sys.executable, str(STAGED), 'mirror', '--target', str(self.target)],
                                cwd=self.clone, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('vault mirror UNAVAILABLE: '), result.stdout)


if __name__ == '__main__':
    unittest.main()
