"""scratch_prune.py contracts on planted scratch dirs, a planted git repo and a planted vault."""
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
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / '.claude/tools/scratch_prune.py'
spec = importlib.util.spec_from_file_location('scratch_prune', TOOL)
prune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prune)

OLD = 30


def _git(repo, *args):
    subprocess.run(['git', '-C', str(repo), '-c', 'user.email=t@t', '-c', 'user.name=t',
                    '-c', 'core.autocrlf=false', *args],
                   check=True, capture_output=True, text=True)


def _force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def age(path, days):
    stamp = time.time() - days * 86400
    path = Path(path)
    targets = [path]
    if path.is_dir():
        for base, dirs, files in os.walk(path):
            targets += [Path(base) / name for name in dirs + files]
    for target in targets:
        os.utime(target, (stamp, stamp))


class ScratchPruneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, onexc=_force_remove)
        base = Path(self.temp)
        self.repo = base / 'repo'
        self.scratch = self.repo / '.claude' / 'scratch'
        self.scratch.mkdir(parents=True)
        self.vault = base / 'vault'
        (self.vault / 'DevProjects/{{PROJECT_NAME}}/Claude').mkdir(parents=True)
        _git(self.repo, 'init', '-q')
        self.write(self.repo / '.gitignore', '.claude/scratch/\n')
        self.write(self.repo / 'readme.md', 'fixture\n')
        _git(self.repo, 'add', '-A')
        _git(self.repo, 'commit', '-q', '-m', 'base')

    def write(self, path, text):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return path

    def entry(self, name, files=None, days=OLD):
        root = self.scratch / name
        for rel, text in (files or {'log.txt': 'log\n'}).items():
            self.write(root / rel, text)
        age(root, days)
        return root

    def track(self, rel, text):
        self.write(self.repo / rel, text)
        _git(self.repo, 'add', '--', rel)

    def inventory(self, keep_days=14):
        return prune.build_inventory(repo=self.repo, scratch=self.scratch,
                                     vault=self.vault, keep_days=keep_days)

    def rows(self, inv):
        return {row['name']: row for row in inv['rows']}

    def apply(self, inv, verdicts):
        inv_path = Path(self.temp) / 'inv.json'
        ver_path = Path(self.temp) / 'verdicts.json'
        inv_path.write_text(json.dumps(inv), encoding='utf-8')
        ver_path.write_text(json.dumps(verdicts), encoding='utf-8')
        return subprocess.run([sys.executable, '-B', str(TOOL), 'apply', '--inventory', str(inv_path),
                               '--verdicts', str(ver_path), '--vault', str(self.vault)],
                              capture_output=True, text=True, timeout=120)

    # --- inventory -----------------------------------------------------------

    def test_each_citation_form_is_keep_referenced(self):
        forms = {
            'fwd': 'see .claude/scratch/fwd/log.txt\n',
            'back': 'see .claude\\scratch\\back\\dumps\\x.dmp\n',
            'barefwd': 'see `scratch/barefwd/notes.md`\n',
            'bareback': 'see scratch\\bareback\\a.txt\n',
        }
        for name, text in forms.items():
            self.entry(name)
            self.track('mem/' + name + '.md', text)
        rows = self.rows(self.inventory())
        for name in forms:
            with self.subTest(name=name):
                self.assertEqual('KEEP-REFERENCED', rows[name]['bucket'])
                self.assertIn('mem/' + name + '.md', rows[name]['referencedBy'])

    def test_citation_only_in_untracked_file_is_keep_referenced(self):
        self.entry('peerwork')
        self.write(self.repo / 'plans/new_plan.md', 'evidence in .claude/scratch/peerwork/\n')
        row = self.rows(self.inventory())['peerwork']
        self.assertEqual('KEEP-REFERENCED', row['bucket'])
        self.assertIn('plans/new_plan.md', row['referencedBy'])

    def test_citation_in_vault_is_keep_referenced(self):
        self.entry('vaultcited')
        self.write(self.vault / 'DevProjects/{{PROJECT_NAME}}/Claude/Notes/a.md',
                   'cited .claude/scratch/vaultcited/report.md\n')
        self.assertEqual('KEEP-REFERENCED', self.rows(self.inventory())['vaultcited']['bucket'])

    def test_recent_entry_is_keep_recent(self):
        self.entry('fresh', days=1)
        self.assertEqual('KEEP-RECENT', self.rows(self.inventory())['fresh']['bucket'])

    def test_one_recent_file_deep_inside_keeps_the_entry(self):
        root = self.entry('mostlyold', {'a/b/old.txt': 'x', 'a/c/new.txt': 'y'})
        age(root / 'a/c/new.txt', 1)
        self.assertEqual('KEEP-RECENT', self.rows(self.inventory())['mostlyold']['bucket'])

    def test_registered_worktree_is_keep_worktree(self):
        _git(self.repo, 'worktree', 'add', '-q', str(self.scratch / 'wt'))
        age(self.scratch / 'wt', OLD)
        row = self.rows(self.inventory())['wt']
        self.assertEqual('KEEP-WORKTREE', row['bucket'])
        self.assertEqual('worktree', row['git'])

    def test_nested_git_is_keep_git(self):
        root = self.entry('clone', {'deep/inner/file.txt': 'x'})
        (root / 'deep/inner/.git').mkdir()
        self.write(root / 'deep/inner/.git/HEAD', 'ref: refs/heads/main\n')
        age(root, OLD)
        row = self.rows(self.inventory())['clone']
        self.assertEqual('KEEP-GIT', row['bucket'])
        self.assertEqual('repo', row['git'])

    def test_real_clone_inside_scratch_is_keep_git(self):
        src = self.scratch / 'clone'
        src.mkdir()
        _git(src, 'init', '-q')
        self.write(src / 'f.txt', 'x')
        age(src, OLD)
        self.assertEqual('KEEP-GIT', self.rows(self.inventory())['clone']['bucket'])

    def test_copied_worktree_whose_backpointer_names_elsewhere_is_not_git(self):
        _git(self.repo, 'worktree', 'add', '-q', str(self.repo / 'live'))
        copy = self.entry('copy', {'inner/blob.txt': 'x' * 50})
        shutil.copyfile(self.repo / 'live' / '.git', copy / 'inner' / '.git')
        age(copy, OLD)
        row = self.rows(self.inventory())['copy']
        self.assertEqual('CANDIDATE', row['bucket'])
        self.assertIsNone(row['git'])

    def test_orphan_git_file_is_not_git(self):
        root = self.entry('orphan', {'inner/f.txt': 'x'})
        self.write(root / 'inner/.git', 'gitdir: ' + str(Path(self.temp) / 'missing-admin') + '\n')
        age(root, OLD)
        self.assertEqual('CANDIDATE', self.rows(self.inventory())['orphan']['bucket'])

    def test_nested_live_worktree_is_keep_worktree(self):
        root = self.entry('holder', {'x.txt': 'x'})
        _git(self.repo, 'worktree', 'add', '-q', str(root / 'inner'))
        age(root, OLD)
        self.assertEqual('KEEP-WORKTREE', self.rows(self.inventory())['holder']['bucket'])

    def test_git_directory_contents_still_count_toward_size(self):
        root = self.entry('clone', {'f.txt': 'x'})
        (root / '.git').mkdir()
        self.write(root / '.git/HEAD', 'ref: refs/heads/main\n')
        age(root, OLD)
        row = self.rows(self.inventory())['clone']
        self.assertEqual(('KEEP-GIT', 1 + len('ref: refs/heads/main\n')), (row['bucket'], row['bytes']))

    def test_old_unreferenced_entry_is_candidate(self):
        self.entry('junk', {'dump.json': '{}', 'b.md': 'bb', 'a.md': 'aaaa'})
        self.write(self.scratch / 'single.md', '# note\n')
        age(self.scratch / 'single.md', OLD)
        rows = self.rows(self.inventory())
        self.assertEqual('CANDIDATE', rows['junk']['bucket'])
        self.assertFalse(rows['junk']['unverified'])
        self.assertEqual(['junk/a.md', 'junk/b.md'], rows['junk']['proseFiles'])
        self.assertEqual('dir', rows['junk']['kind'])
        self.assertEqual(3, rows['junk']['files'])
        self.assertEqual('CANDIDATE', rows['single.md']['bucket'])
        self.assertEqual('file', rows['single.md']['kind'])
        self.assertEqual(['single.md'], rows['single.md']['proseFiles'])

    def test_mention_of_a_missing_entry_maps_to_nothing(self):
        self.entry('real')
        self.track('mem/x.md', 'see scratch/ghost/a.txt and scratch/real,\n')
        rows = self.rows(self.inventory())
        self.assertNotIn('ghost', rows)
        self.assertEqual('KEEP-REFERENCED', rows['real']['bucket'])

    def test_bucket_totals_reconcile_on_the_cli(self):
        self.entry('a')
        self.entry('b', days=1)
        self.entry('c')
        self.track('mem/c.md', 'scratch/c/x\n')
        out = Path(self.temp) / 'inv.json'
        run = subprocess.run([sys.executable, '-B', str(TOOL), 'inventory', '--repo', str(self.repo),
                              '--scratch', str(self.scratch), '--vault', str(self.vault),
                              '--out', str(out)], capture_output=True, text=True, timeout=120)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        inv = json.loads(out.read_text(encoding='utf-8'))
        counts = inv['summary']['buckets']
        self.assertEqual(3, sum(bucket['count'] for bucket in counts.values()))
        self.assertEqual(inv['summary']['entries'], len(inv['rows']))
        self.assertEqual(sum(r['bytes'] for r in inv['rows']),
                         sum(bucket['bytes'] for bucket in counts.values()))
        self.assertIn('TOTAL', run.stdout)
        self.assertIn('vault inputs', run.stdout)

    def test_unreadable_vault_file_marks_candidates_unverified_and_apply_refuses(self):
        self.entry('junk')
        broken = self.write(self.vault / 'DevProjects/{{PROJECT_NAME}}/Claude/broken.md', 'x\n')
        real_read = Path.read_bytes

        def read_bytes(path):
            if path == broken:
                raise PermissionError(13, 'Permission denied')
            return real_read(path)

        with mock.patch.object(Path, 'read_bytes', read_bytes):
            inv = self.inventory()
        row = self.rows(inv)['junk']
        self.assertEqual('CANDIDATE', row['bucket'])
        self.assertTrue(row['unverified'])
        self.assertEqual(1, inv['vault']['unreadable'])
        run = self.apply(inv, {'junk': {'verdict': 'DELETE', 'reason': 'no prose'}})
        self.assertEqual(2, run.returncode, run.stdout + run.stderr)
        self.assertTrue((self.scratch / 'junk').exists())

    def test_archived_entry_folder_named_like_a_file_is_not_a_vault_input(self):
        self.entry('junk')
        month = time.strftime('%Y-%m')
        self.write(self.vault / 'DevProjects/{{PROJECT_NAME}}/Claude/Archived/Scratch' / month
                   / 'notes.md' / 'notes.md', '# archived\n')
        inv = self.inventory()
        self.assertEqual(0, inv['vault']['unreadable'])
        self.assertFalse(self.rows(inv)['junk']['unverified'])

    def test_absent_vault_marks_candidates_unverified(self):
        self.entry('junk')
        shutil.rmtree(self.vault)
        inv = self.inventory()
        self.assertTrue(self.rows(inv)['junk']['unverified'])
        self.assertTrue(inv['vault']['absent'])

    # --- apply ---------------------------------------------------------------

    def test_delete_removes_entry_with_read_only_nested_file(self):
        root = self.entry('junk', {'sub/locked.txt': 'x'})
        os.chmod(root / 'sub/locked.txt', stat.S_IREAD)
        age(root, OLD)
        run = self.apply(self.inventory(), {'junk': {'verdict': 'DELETE', 'reason': 'no prose'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertFalse(root.exists())
        self.assertIn('deleted=1', run.stdout)

    def test_archive_copies_prose_to_vault_then_removes_entry(self):
        root = self.entry('study', {'findings.md': '# F1 result\n', 'sub/more.md': 'm\n', 'raw.json': '{}'})
        run = self.apply(self.inventory(), {'study': {'verdict': 'ARCHIVE', 'reason': 'F1 measurement'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        month = time.strftime('%Y-%m')
        dest = self.vault / 'DevProjects/{{PROJECT_NAME}}/Claude/Archived/Scratch' / month / 'study'
        self.assertEqual('# F1 result\n', (dest / 'findings.md').read_text(encoding='utf-8'))
        self.assertTrue((dest / 'sub/more.md').exists())
        self.assertFalse((dest / 'raw.json').exists())
        self.assertFalse(root.exists())
        self.assertIn('archived=1', run.stdout)

    def test_failed_archive_copy_leaves_entry(self):
        root = self.entry('study', {'findings.md': '# F1\n'})
        inv = self.inventory()
        month = time.strftime('%Y-%m')
        blocker = self.vault / 'DevProjects/{{PROJECT_NAME}}/Claude/Archived/Scratch' / month / 'study'
        self.write(blocker, 'a file where the archive dir must go\n')
        run = self.apply(inv, {'study': {'verdict': 'ARCHIVE', 'reason': 'F1'}})
        self.assertEqual(1, run.returncode, run.stdout + run.stderr)
        self.assertTrue((root / 'findings.md').exists())
        self.assertIn('failed=1', run.stdout)

    def test_drifted_row_is_skipped(self):
        root = self.entry('junk')
        inv = self.inventory()
        self.write(root / 'late.txt', 'touched after the snapshot\n')
        run = self.apply(inv, {'junk': {'verdict': 'DELETE', 'reason': 'no prose'}})
        self.assertTrue(root.exists())
        self.assertIn('skipped=1', run.stdout)

    def test_newly_referenced_row_is_skipped(self):
        root = self.entry('junk')
        inv = self.inventory()
        self.write(self.repo / 'plans/p.md', 'now cites scratch/junk/log.txt\n')
        run = self.apply(inv, {'junk': {'verdict': 'DELETE', 'reason': 'no prose'}})
        self.assertTrue(root.exists())
        self.assertIn('skipped=1', run.stdout)

    def test_verdict_naming_a_keep_row_is_refused(self):
        self.entry('fresh', days=1)
        root = self.entry('junk')
        run = self.apply(self.inventory(), {
            'fresh': {'verdict': 'DELETE', 'reason': 'x'},
            'junk': {'verdict': 'DELETE', 'reason': 'no prose'}})
        self.assertEqual(2, run.returncode, run.stdout + run.stderr)
        self.assertIn('fresh', run.stdout + run.stderr)
        self.assertTrue((self.scratch / 'fresh').exists())
        self.assertTrue(root.exists())

    def test_missing_verdict_aborts_before_any_action(self):
        first = self.entry('junk1')
        second = self.entry('junk2')
        run = self.apply(self.inventory(), {'junk1': {'verdict': 'DELETE', 'reason': 'no prose'}})
        self.assertEqual(2, run.returncode, run.stdout + run.stderr)
        self.assertIn('junk2', run.stdout + run.stderr)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_keep_verdict_keeps_entry(self):
        root = self.entry('junk')
        run = self.apply(self.inventory(), {'junk': {'verdict': 'KEEP', 'reason': 'owner asked'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertTrue(root.exists())
        self.assertIn('kept=1', run.stdout)


    # --- trim ----------------------------------------------------------------

    def trim(self, keep_days=14, bulk_bytes=100):
        return prune.trim_bulk(repo=self.repo, scratch=self.scratch, keep_days=keep_days, bulk_bytes=bulk_bytes)

    def test_trim_deletes_old_bulk_file_in_a_cited_entry_and_keeps_its_text(self):
        root = self.entry('bootcrash', {'dumps/a.dmp': 'x' * 200, 'notes.md': 'finding\n', 'small.log': 'ok\n'})
        self.track('docs/memory.md', 'evidence in .claude/scratch/bootcrash/notes.md\n')
        removed, failed = self.trim()
        self.assertEqual([root / 'dumps/a.dmp'], [path for path, _ in removed])
        self.assertEqual([], failed)
        self.assertFalse((root / 'dumps/a.dmp').exists())
        self.assertTrue((root / 'notes.md').exists())
        self.assertTrue((root / 'small.log').exists())
        self.assertEqual('KEEP-REFERENCED', self.rows(self.inventory())['bootcrash']['bucket'])

    def test_trim_keeps_a_bulk_file_younger_than_the_keep_window(self):
        root = self.entry('fresh', {'a.dmp': 'x' * 200}, days=2)
        removed, _ = self.trim()
        self.assertEqual([], removed)
        self.assertTrue((root / 'a.dmp').exists())

    def test_trim_keeps_markdown_whatever_its_size(self):
        root = self.entry('prose', {'big.md': 'x' * 200})
        removed, _ = self.trim()
        self.assertEqual([], removed)
        self.assertTrue((root / 'big.md').exists())

    def test_trim_skips_a_nested_git_checkout(self):
        root = self.entry('clone', {'deep/blob.bin': 'x' * 200})
        (root / 'deep/.git').mkdir()
        self.write(root / 'deep/.git/HEAD', 'ref: refs/heads/main\n')
        age(root, OLD)
        removed, _ = self.trim()
        self.assertEqual([], removed)
        self.assertTrue((root / 'deep/blob.bin').exists())

    def test_trim_skips_a_registered_worktree(self):
        _git(self.repo, 'worktree', 'add', '-q', str(self.scratch / 'wt'))
        self.write(self.scratch / 'wt/blob.bin', 'x' * 200)
        age(self.scratch / 'wt', OLD)
        removed, _ = self.trim()
        self.assertEqual([], removed)

    def test_trim_leaves_a_top_level_file_to_the_candidate_path(self):
        path = self.write(self.scratch / 'loose.bin', 'x' * 200)
        age(path, OLD)
        removed, _ = self.trim()
        self.assertEqual([], removed)
        self.assertTrue(path.exists())

    def test_trim_cli_reports_and_exits_zero(self):
        root = self.entry('dumpy', {'a.dmp': 'x' * 200})
        run = subprocess.run([sys.executable, '-B', str(TOOL), 'trim', '--repo', str(self.repo),
                              '--bulk-bytes', '100'], capture_output=True, text=True, timeout=120)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertIn('TRIMMED', run.stdout)
        self.assertFalse((root / 'a.dmp').exists())


if __name__ == '__main__':
    unittest.main()
