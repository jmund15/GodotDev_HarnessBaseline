"""Init and sync on planted vaults and bare origins: isolation, history, refusals, locks and errors."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_STAGE = tempfile.TemporaryDirectory()
for _name in ('vault_sync.py', 'vault_read.py'):
    shutil.copy(ROOT / '.claude/tools' / _name, Path(_STAGE.name) / _name)
TOOL = Path(_STAGE.name) / 'vault_sync.py'
(TOOL.parent / 'vault_sync.project.json').write_text(
    json.dumps({'folders': ['Proj'], 'gitDirName': 'vault-git-test', 'taskName': 'VaultSyncTest'}), encoding='utf-8')
spec =importlib.util.spec_from_file_location('vault_sync', TOOL)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)
vs.configure(['Proj'], 'vault-git-test', 'VaultSyncTest')
TOKEN = 'ghp_' + 'a1B2c3' * 6


def git(*args, git_dir=None):
    base = ['git'] + (['--git-dir', str(git_dir)] if git_dir else [])
    return subprocess.run(base + list(args), check=True, capture_output=True, text=True).stdout.strip()


def plant(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')


class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.remote = self.base / 'remote.git'
        git('init', '-q', '--bare', str(self.remote))

    def tearDown(self):
        self.tmp.cleanup()

    def machine(self, name):
        worktree, git_dir = self.base / name / 'DevProjects', self.base / name / 'gitdir'
        worktree.mkdir(parents=True)
        vs.init(worktree, git_dir, str(self.remote))
        return worktree, git_dir

    def tip(self):
        try:
            return git('rev-parse', '--verify', '-q', 'refs/heads/vault', git_dir=self.remote)
        except subprocess.CalledProcessError:
            return None

    def remote_files(self):
        return git('ls-tree', '-r', '--name-only', 'refs/heads/vault', git_dir=self.remote).splitlines()


class Init(Vault):
    def test_vault_gets_no_git_file_and_no_gitignore(self):
        worktree, git_dir = self.machine('a')
        self.assertEqual(sorted(p.name for p in worktree.iterdir()), [])
        self.assertTrue((git_dir / 'hooks' / 'pre-commit').is_file())
        self.assertEqual(git('config', 'core.worktree', git_dir=git_dir), worktree.as_posix())

    def test_init_twice_is_a_no_op_and_pushes_nothing(self):
        worktree, git_dir = self.machine('a')
        vs.init(worktree, git_dir, str(self.remote))
        self.assertIsNone(self.tip())


class Sync(Vault):
    def first(self, worktree, git_dir):
        plant(worktree, {'Proj/a.md': 'one', 'Proj/b.md': 'two', 'Proj/c.md': 'three',
                         'AI/API Keys/k.md': 'private', 'Proj/.obsidian/app.json': '{}',
                         'Proj/x.db': 'bin', '.obsidian/w.json': '{}', 'PeerA/m.md': 'other',
                         'PeerB/b.md': 'other', 'PeerC/c.md': 'other'})

    def test_first_push_needs_the_flag(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        code, report = vs.sync(worktree, git_dir)
        self.assertEqual((code, [r['kind'] for r in report['refusals']], self.tip()), (1, ['first-push'], None))

    def test_first_push_publishes_the_allowlist_only(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        code, report = vs.sync(worktree, git_dir, first_push=True)
        self.assertEqual((code, report['pushed']), (0, True), report)
        self.assertEqual(sorted(self.remote_files()), ['Proj/a.md', 'Proj/b.md', 'Proj/c.md'])
        self.assertEqual([p.name for p in worktree.iterdir() if p.name.startswith('.git')], [])

    def test_a_later_change_fast_forwards(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        vs.sync(worktree, git_dir, first_push=True)
        before = self.tip()
        plant(worktree, {'Proj/new.md': 'fresh'})
        code, report = vs.sync(worktree, git_dir)
        self.assertEqual((code, report['pushed']), (0, True), report)
        self.assertEqual(git('rev-parse', 'refs/heads/vault~1', git_dir=self.remote), before)
        self.assertIn('Proj/new.md', self.remote_files())

    def test_a_conflict_copy_name_reaches_the_status_file(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        vs.sync(worktree, git_dir, first_push=True)
        plant(worktree, {'Proj/note-DESKTOP-ABC123.md': 'copy'})
        code, report = vs.sync(worktree, git_dir)
        self.assertEqual(code, 0, report)
        status = json.loads((git_dir / 'sync_status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['conflictCopies'], ['Proj/note-DESKTOP-ABC123.md'])

    def test_every_git_child_opens_no_console_window(self):
        from unittest import mock
        with mock.patch.object(vs.subprocess, 'run') as run:
            vs._git_run(self.base, None, 'status')
        self.assertEqual(run.call_args.kwargs['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def test_no_change_makes_no_commit(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        vs.sync(worktree, git_dir, first_push=True)
        before = self.tip()
        code, report = vs.sync(worktree, git_dir)
        self.assertEqual((code, report['pushed'], self.tip()), (0, False, before))

    def test_a_second_machine_continues_the_history_without_a_merge(self):
        a_tree, a_git = self.machine('a')
        self.first(a_tree, a_git)
        vs.sync(a_tree, a_git, first_push=True)
        a_tip = self.tip()
        b_tree, b_git = self.machine('b')
        self.first(b_tree, b_git)
        plant(b_tree, {'Proj/from_b.md': 'b'})
        code, report = vs.sync(b_tree, b_git)
        self.assertEqual((code, report['pushed']), (0, True), report)
        self.assertEqual(git('rev-list', '--merges', '--count', 'refs/heads/vault', git_dir=self.remote), '0')
        self.assertEqual(git('rev-parse', 'refs/heads/vault~1', git_dir=self.remote), a_tip)

    def test_refusal_leaves_origin_alone_and_never_records_the_secret(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        vs.sync(worktree, git_dir, first_push=True)
        before = self.tip()
        plant(worktree, {'Proj/leak.md': TOKEN})
        code, report = vs.sync(worktree, git_dir)
        self.assertEqual((code, self.tip()), (1, before))
        self.assertEqual([r['kind'] for r in report['refusals']], ['secret'])
        status = (git_dir / 'sync_status.json').read_text(encoding='utf-8')
        self.assertFalse(json.loads(status)['ok'])
        self.assertNotIn(TOKEN, status + (git_dir / 'sync_log.jsonl').read_text(encoding='utf-8'))
        (worktree / 'Proj/leak.md').unlink()
        code, report = vs.sync(worktree, git_dir)
        self.assertEqual((code, report['pushed']), (0, False), report)

    def test_a_vanished_folder_is_not_pushed(self):
        worktree, git_dir = self.machine('a')
        plant(worktree, {f'Proj/d/{n}.md': f'note {n}' for n in range(vs.DELETION_CAP + 1)})
        vs.sync(worktree, git_dir, first_push=True)
        before = self.tip()
        for path in (worktree / 'Proj/d').iterdir():
            path.unlink()
        code, report = vs.sync(worktree, git_dir)
        self.assertEqual((code, self.tip(), [r['kind'] for r in report['refusals']]), (1, before, ['deletion-cap']))

    def test_a_fresh_lock_means_busy_and_nothing_runs(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        (git_dir / 'sync.lock').write_text('12345', encoding='utf-8')
        code, report = vs.sync(worktree, git_dir, first_push=True)
        self.assertEqual((code, report['busy'], self.tip()), (0, True, None))

    def test_a_stale_lock_is_taken_over(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        lock = git_dir / 'sync.lock'
        lock.write_text('12345', encoding='utf-8')
        old = time.time() - vs.LOCK_STALE_S - 60
        os.utime(lock, (old, old))
        code, _ = vs.sync(worktree, git_dir, first_push=True)
        self.assertEqual(code, 0)
        self.assertFalse(lock.exists())

    def test_an_unreachable_remote_is_an_error_row_not_a_crash(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        git('remote', 'set-url', 'origin', str(self.base / 'no_such_remote.git'), git_dir=git_dir)
        code, report = vs.sync(worktree, git_dir, first_push=True)
        self.assertEqual(code, 3)
        self.assertTrue(report['error'])
        self.assertFalse(json.loads((git_dir / 'sync_status.json').read_text(encoding='utf-8'))['ok'])

    def test_an_oserror_in_a_run_is_an_error_row_not_a_crash(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        original = vs._sync
        vs._sync = lambda *args: (_ for _ in ()).throw(FileNotFoundError('git'))
        try:
            code, report = vs.sync(worktree, git_dir, first_push=True)
        finally:
            vs._sync = original
        self.assertEqual(code, 3)
        self.assertIn('FileNotFoundError', report['error'])
        self.assertFalse(json.loads((git_dir / 'sync_status.json').read_text(encoding='utf-8'))['ok'])

    def test_a_stalled_git_call_ends_inside_the_lock_takeover_window(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        seen = []
        original = vs.subprocess.run

        def stalled(cmd, **kwargs):
            seen.append(kwargs.get('timeout'))
            raise subprocess.TimeoutExpired(cmd, kwargs.get('timeout') or 0)
        vs.subprocess.run = stalled
        try:
            code, report = vs.sync(worktree, git_dir, first_push=True)
        finally:
            vs.subprocess.run = original
        self.assertEqual(code, 3)
        self.assertIn('timed out', report['error'])
        self.assertEqual(set(seen), {vs.LOCK_STALE_S})
        self.assertFalse((git_dir / 'sync.lock').exists())

    def test_the_lock_is_held_until_the_status_row_is_written(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        held = []
        original = vs._record
        vs._record = lambda gd, code, report: (held.append((gd / 'sync.lock').exists()), original(gd, code, report))
        try:
            vs.sync(worktree, git_dir, first_push=True)
        finally:
            vs._record = original
        self.assertEqual(held, [True])

    def test_log_keeps_the_newest_rows_only(self):
        worktree, git_dir = self.machine('a')
        self.first(worktree, git_dir)
        (git_dir / 'sync_log.jsonl').write_text('{"old": 1}\n' * (vs.LOG_ROWS + 20), encoding='utf-8')
        vs.sync(worktree, git_dir, first_push=True)
        rows = (git_dir / 'sync_log.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(rows), vs.LOG_ROWS)
        self.assertIn('"ok"', rows[-1])


if __name__ == '__main__':
    unittest.main()
