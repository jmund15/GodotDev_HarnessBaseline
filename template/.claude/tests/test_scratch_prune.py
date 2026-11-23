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
        try:
            os.utime(target, (stamp, stamp))
        except FileNotFoundError:
            continue  # git's background maintenance retires a transient lock between the walk and the touch


class ScratchPruneTests(unittest.TestCase):
    """The base repo and vault are built once for the class (one git init and commit) and copied per test."""

    @classmethod
    def setUpClass(cls):
        cls.template = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.template, onexc=_force_remove)
        base = Path(cls.template)
        repo = base / 'repo'
        (repo / '.claude' / 'scratch').mkdir(parents=True)
        (base / 'vault' / 'DevProjects/Proj/Claude').mkdir(parents=True)
        cls.write(None, repo / '.claude/skills/project_subsystems/adaptation.json', '{"project_name": "Proj"}\n')
        _git(repo, 'init', '-q')
        cls.write(None, repo / '.gitignore', '.claude/scratch/\n')
        cls.write(None, repo / 'readme.md', 'fixture\n')
        _git(repo, 'add', '-A')
        _git(repo, 'commit', '-q', '-m', 'base')

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, onexc=_force_remove)
        base = Path(self.temp)
        shutil.copytree(self.template, base, dirs_exist_ok=True)
        self.repo = base / 'repo'
        self.scratch = self.repo / '.claude' / 'scratch'
        self.vault = base / 'vault'

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

    def test_unset_project_name_refuses_rather_than_read_another_folder(self):
        (self.repo / '.claude/skills/project_subsystems/adaptation.json').write_bytes(b'{}\n')
        self.entry('any')
        with self.assertRaisesRegex(ValueError, 'sets no project_name'):
            self.inventory()

    def test_each_citation_form_is_keep_referenced(self):
        forms = {
            'fwd': 'see .claude/scratch/fwd/log.txt\n',
            'back': 'see .claude\\scratch\\back\\dumps\\x.dmp\n',
            'barefwd': 'see `scratch/barefwd/notes.md`\n',
            'bareback': 'see scratch\\bareback\\a.txt\n',
        }
        for name, text in forms.items():
            self.entry(name)
            self.track('mem/' + name + '.py', '# ' + text)
        rows = self.rows(self.inventory())
        for name in forms:
            with self.subTest(name=name):
                self.assertEqual('KEEP-REFERENCED', rows[name]['bucket'])
                self.assertIn('mem/' + name + '.py', rows[name]['referrers']['code'])

    def test_citation_only_in_untracked_file_is_keep_referenced(self):
        self.entry('peerwork')
        self.write(self.repo / 'plans/new_plan.py', '# evidence in .claude/scratch/peerwork/\n')
        row = self.rows(self.inventory())['peerwork']
        self.assertEqual('KEEP-REFERENCED', row['bucket'])
        self.assertIn('plans/new_plan.py', row['referencedBy'])

    def test_citation_in_vault_note_is_recorded_as_prose_and_keeps_nothing(self):
        self.entry('vaultcited')
        self.write(self.vault / 'DevProjects/Proj/Claude/Notes/a.md',
                   'cited .claude/scratch/vaultcited/report.md\n')
        row = self.rows(self.inventory())['vaultcited']
        self.assertEqual('CANDIDATE', row['bucket'])
        self.assertEqual([], row['referrers']['code'])
        self.assertEqual(1, len(row['referrers']['prose']))

    def test_prose_and_log_referrers_do_not_keep_an_entry(self):
        self.entry('plain')
        self.track('plans/p.md', 'see scratch/plain/a.txt\n')
        self.track('metrics.jsonl', '{"path": "scratch/plain/a.txt"}\n')
        self.track('notes.txt', 'scratch/plain/a.txt\n')
        row = self.rows(self.inventory())['plain']
        self.assertEqual('CANDIDATE', row['bucket'])
        self.assertEqual(['metrics.jsonl', 'notes.txt', 'plans/p.md'], row['referrers']['prose'])

    def test_script_and_proof_referrers_keep_an_entry(self):
        for name, rel, text in (('byps', 'tools/t.ps1', '$x = ".claude/scratch/byps/a"\n'),
                                ('bysh', 'tools/t.sh', 'cat .claude/scratch/bysh/a\n'),
                                ('bycs', 'Tests/T.cs', '// scratch/bycs/a\n'),
                                ('bycfg', 'cfg.json', '{"p": "scratch/bycfg/a"}\n')):
            self.entry(name)
            self.track(rel, text)
        rows = self.rows(self.inventory())
        for name in ('byps', 'bysh', 'bycs', 'bycfg'):
            with self.subTest(name=name):
                self.assertEqual('KEEP-REFERENCED', rows[name]['bucket'])

    def test_recent_entry_is_keep_recent(self):
        self.entry('fresh', days=1)
        self.assertEqual('KEEP-RECENT', self.rows(self.inventory())['fresh']['bucket'])

    def test_a_folder_adaptation_names_in_scratch_keep_is_keep_tool(self):
        self.write(self.repo / '.claude/skills/project_subsystems/adaptation.json',
                   '{"project_name": "Proj", "scratch_keep": ["boot"]}\n')
        self.entry('boot')
        self.entry('other')
        rows = self.rows(self.inventory())
        self.assertEqual('KEEP-TOOL', rows['boot']['bucket'])
        self.assertEqual('CANDIDATE', rows['other']['bucket'])

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

    def real_repo(self, root, text='unique work', commit=True):
        root.mkdir(parents=True, exist_ok=True)
        _git(root, 'init', '-q')
        self.write(root / 'f.txt', text)
        if commit:
            _git(root, 'add', '-A')
            _git(root, 'commit', '-q', '-m', text)
        age(root, OLD)

    def test_nested_repo_with_a_commit_found_nowhere_else_is_keep_git(self):
        self.real_repo(self.scratch / 'clone' / 'deep' / 'inner')
        age(self.scratch / 'clone', OLD)
        row = self.rows(self.inventory())['clone']
        self.assertEqual('KEEP-GIT', row['bucket'])
        self.assertEqual('repo', row['git'])
        self.assertEqual(1, len(row['gitWork']))
        self.assertIn('1 commit', row['gitWork'][0])
        self.assertIn('inner', row['gitWork'][0])

    def test_repo_with_a_modified_tracked_file_is_keep_git(self):
        self.real_repo(self.scratch / 'dirty')
        self.write(self.scratch / 'dirty/f.txt', 'edited after the commit')
        age(self.scratch / 'dirty', OLD)
        row = self.rows(self.inventory())['dirty']
        self.assertEqual('KEEP-GIT', row['bucket'])
        self.assertTrue(any('modified' in item for item in row['gitWork']), row['gitWork'])

    def test_clean_copy_whose_commits_are_in_the_checkout_is_a_candidate(self):
        subprocess.run(['git', 'clone', '-q', str(self.repo), str(self.scratch / 'copy')],
                       check=True, capture_output=True)
        age(self.scratch / 'copy', OLD)
        row = self.rows(self.inventory())['copy']
        self.assertEqual('CANDIDATE', row['bucket'])
        self.assertEqual('repo', row['git'])
        self.assertEqual([], row['gitWork'])

    def test_empty_git_shell_is_judged_like_any_row(self):
        root = self.entry('shell', {'a/f.txt': 'x'})
        (root / 'a' / '.git').mkdir()
        (root / 'a' / '.git' / 'objects').mkdir()
        age(root, OLD)
        row = self.rows(self.inventory())['shell']
        self.assertEqual('CANDIDATE', row['bucket'])
        self.assertIsNone(row['git'])
        self.assertEqual(1, row['gitShells'])

    def test_repo_with_no_commit_and_no_files_is_a_candidate(self):
        self.real_repo(self.scratch / 'bare', commit=False)
        (self.scratch / 'bare/f.txt').unlink()
        age(self.scratch / 'bare', OLD)
        self.assertEqual('CANDIDATE', self.rows(self.inventory())['bare']['bucket'])

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
        root = self.scratch / 'clone'
        self.real_repo(root)
        row = self.rows(self.inventory())['clone']
        self.assertEqual('KEEP-GIT', row['bucket'])
        self.assertGreater(row['bytes'], len('unique work') + 100)

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
        self.track('mem/x.py', '# see scratch/ghost/a.txt and scratch/real,\n')
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
        broken = self.write(self.vault / 'DevProjects/Proj/Claude/broken.md', 'x\n')
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
        self.write(self.vault / 'DevProjects/Proj/Claude/Archived/Scratch' / month
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
        dest = self.vault / 'DevProjects/Proj/Claude/Archived/Scratch' / month / 'study'
        self.assertEqual('# F1 result\n', (dest / 'findings.md').read_text(encoding='utf-8'))
        self.assertTrue((dest / 'sub/more.md').exists())
        self.assertFalse((dest / 'raw.json').exists())
        self.assertFalse(root.exists())
        self.assertIn('archived=1', run.stdout)

    def test_archive_masks_a_key_shape_and_reports_the_count_per_file(self):
        token = 'ghp_' + 'a1B2c3' * 6
        self.entry('leaky', {'notes.md': 'before ' + token + ' after\n', 'clean.md': 'fine\n'})
        run = self.apply(self.inventory(), {'leaky': {'verdict': 'ARCHIVE', 'reason': 'x'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        dest = (self.vault / 'DevProjects/Proj/Claude/Archived/Scratch' / time.strftime('%Y-%m') / 'leaky')
        archived = (dest / 'notes.md').read_bytes()
        self.assertNotIn(token.encode(), archived)
        self.assertIn(b'before ', archived)
        self.assertEqual(b'fine\n', (dest / 'clean.md').read_bytes())
        self.assertNotIn(token, run.stdout + run.stderr)
        self.assertIn('notes.md', run.stdout)
        self.assertFalse((self.scratch / 'leaky').exists())

    def test_failed_archive_copy_leaves_entry(self):
        root = self.entry('study', {'findings.md': '# F1\n'})
        inv = self.inventory()
        month = time.strftime('%Y-%m')
        blocker = self.vault / 'DevProjects/Proj/Claude/Archived/Scratch' / month / 'study'
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
        self.write(self.repo / 'tools/p.py', '# now reads scratch/junk/log.txt\n')
        run = self.apply(inv, {'junk': {'verdict': 'DELETE', 'reason': 'no prose'}})
        self.assertTrue(root.exists())
        self.assertIn('skipped=1', run.stdout)

    def test_verdict_naming_a_row_not_in_the_inventory_is_refused(self):
        root = self.entry('junk')
        run = self.apply(self.inventory(), {
            'ghost': {'verdict': 'DELETE', 'reason': 'x'},
            'junk': {'verdict': 'DELETE', 'reason': 'no prose'}})
        self.assertEqual(2, run.returncode, run.stdout + run.stderr)
        self.assertIn('ghost', run.stdout + run.stderr)
        self.assertTrue(root.exists())

    def test_delete_on_a_prose_cited_row_is_carried_out(self):
        root = self.entry('cited')
        self.track('plans/p.md', 'old plan: scratch/cited/log.txt\n')
        run = self.apply(self.inventory(), {'cited': {'verdict': 'DELETE', 'reason': 'only an old plan'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertFalse(root.exists())

    def test_delete_on_an_empty_shell_row_is_carried_out(self):
        root = self.entry('shell', {'a/f.txt': 'x'})
        (root / 'a' / '.git').mkdir()
        age(root, OLD)
        run = self.apply(self.inventory(), {'shell': {'verdict': 'DELETE', 'reason': 'shell'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertFalse(root.exists())

    def test_delete_on_a_referenced_or_recent_row_is_carried_out_when_the_judge_saw_its_facts(self):
        root = self.entry('read')
        self.track('tools/r.py', '# reads scratch/read/a\n')
        recent = self.entry('recent', days=1)
        inv = self.inventory()
        self.assertEqual(['KEEP-REFERENCED', 'KEEP-RECENT'],
                         [self.rows(inv)[n]['bucket'] for n in ('read', 'recent')])
        run = self.apply(inv, {'read': {'verdict': 'DELETE', 'reason': 'reader is dead code'},
                               'recent': {'verdict': 'DELETE', 'reason': 'scratch note'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertFalse(root.exists(), run.stdout)
        self.assertFalse(recent.exists(), run.stdout)
        self.assertIn('deleted=2', run.stdout)

    def test_a_verdict_never_removes_a_row_that_holds_git_work(self):
        self.real_repo(self.scratch / 'work')
        self.write(self.scratch / 'work/notes.md', '# findings\n')
        inv = self.inventory()
        self.assertEqual('KEEP-GIT', self.rows(inv)['work']['bucket'])
        for verdict in ('DELETE', 'ARCHIVE'):
            run = self.apply(inv, {'work': {'verdict': verdict, 'reason': 'fixture commit, nothing needed'}})
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            self.assertTrue((self.scratch / 'work').exists(), verdict + ': ' + run.stdout)
            self.assertIn('skipped=1', run.stdout)
            self.assertIn('git work', run.stdout)
        self.assertFalse((self.vault / 'DevProjects/Proj/Claude/Archived/Scratch').exists())

    def test_a_keep_verdict_on_a_git_work_row_is_kept(self):
        self.real_repo(self.scratch / 'work')
        run = self.apply(self.inventory(), {'work': {'verdict': 'KEEP', 'reason': 'the owner decides'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertTrue((self.scratch / 'work').exists())
        self.assertIn('kept=1', run.stdout)

    def test_new_git_work_since_the_inventory_skips_the_row(self):
        self.real_repo(self.scratch / 'work')
        inv = self.inventory()
        self.write(self.scratch / 'work/g.txt', 'new')
        _git(self.scratch / 'work', 'add', '-A')
        _git(self.scratch / 'work', 'commit', '-q', '-m', 'later')
        age(self.scratch / 'work', OLD)
        run = self.apply(inv, {'work': {'verdict': 'DELETE', 'reason': 'x'}})
        self.assertTrue((self.scratch / 'work').exists())
        self.assertIn('skipped=1', run.stdout)

    def test_a_code_referrer_added_since_the_inventory_skips_the_row(self):
        root = self.entry('read')
        self.track('tools/old.py', '# reads scratch/read/a\n')
        inv = self.inventory()
        self.track('tools/new.py', '# also reads scratch/read/b\n')
        run = self.apply(inv, {'read': {'verdict': 'DELETE', 'reason': 'x'}})
        self.assertTrue(root.exists())
        self.assertIn('skipped=1', run.stdout)

    def test_commits_on_a_remote_of_the_nested_repo_are_not_work(self):
        origin = Path(self.temp) / 'origin_store'
        self.real_repo(origin)
        subprocess.run(['git', 'clone', '-q', str(origin), str(self.scratch / 'pushed')],
                       check=True, capture_output=True)
        age(self.scratch / 'pushed', OLD)
        row = self.rows(self.inventory())['pushed']
        self.assertEqual([], row['gitWork'], row['gitWork'])
        self.assertEqual('CANDIDATE', row['bucket'])

    def test_inventory_leaves_every_file_mtime_of_a_nested_repo_alone(self):
        self.real_repo(self.scratch / 'quiet')
        # Settle git's own stat cache first: the first `git status` after the fixture is aged rewrites
        # `.git/index` because the cached stats are stale, and that is git's write, not the inventory's.
        _git(self.scratch / 'quiet', 'status', '--porcelain')

        def stamps():
            return {str(p): p.stat().st_mtime for p in (self.scratch / 'quiet').rglob('*')}

        before = stamps()
        self.inventory()
        self.assertEqual(before, stamps())

    def test_line_ending_only_changes_are_not_work(self):
        self.real_repo(self.scratch / 'eol', text='one\ntwo\n')
        subprocess.run(['git', 'clone', '-q', str(self.scratch / 'eol'), str(self.scratch / 'eolcopy')],
                       check=True, capture_output=True)
        self.write(self.scratch / 'eolcopy/f.txt', 'one\r\ntwo\r\n')
        _git(self.scratch / 'eolcopy', 'config', 'core.autocrlf', 'false')
        age(self.scratch / 'eolcopy', OLD)
        rows = self.rows(self.inventory())
        self.assertEqual([], rows['eolcopy']['gitWork'], rows['eolcopy']['gitWork'])
        self.assertEqual('KEEP-GIT', rows['eol']['bucket'])

    def test_registered_worktree_row_is_removed_through_git(self):
        wt = self.scratch / 'wt'
        _git(self.repo, 'worktree', 'add', '-q', str(wt))
        self.write(wt / 'untracked.txt', 'x')
        age(wt, OLD)
        run = self.apply(self.inventory(), {'wt': {'verdict': 'DELETE', 'reason': 'done'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertFalse(wt.exists())
        self.assertEqual(1, self.worktree_count())

    def test_nested_registered_worktree_is_removed_through_git(self):
        root = self.entry('holder', {'x.txt': 'x'})
        _git(self.repo, 'worktree', 'add', '-q', str(root / 'inner'))
        age(root, OLD)
        run = self.apply(self.inventory(), {'holder': {'verdict': 'DELETE', 'reason': 'done'}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertFalse(root.exists())
        self.assertEqual(1, self.worktree_count())

    def worktree_count(self):
        listing = subprocess.run(['git', '-C', str(self.repo), 'worktree', 'list', '--porcelain'],
                                 capture_output=True, text=True).stdout
        return sum(1 for line in listing.splitlines() if line.startswith('worktree '))

    def test_archive_with_named_files_copies_only_those(self):
        root = self.entry('big', {'keep/oracle.cs': 'class A{}', 'keep/n.md': '# n', 'bulk/x.bin': 'b' * 50,
                                  'other.md': '# other'})
        run = self.apply(self.inventory(), {'big': {'verdict': 'ARCHIVE', 'reason': 'oracles',
                                                    'archiveFiles': ['keep/oracle.cs', 'keep/n.md']}})
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        dest = self.vault / 'DevProjects/Proj/Claude/Archived/Scratch' / time.strftime('%Y-%m') / 'big'
        self.assertEqual('class A{}', (dest / 'keep/oracle.cs').read_text(encoding='utf-8'))
        self.assertTrue((dest / 'keep/n.md').exists())
        self.assertFalse((dest / 'other.md').exists())
        self.assertFalse((dest / 'bulk/x.bin').exists())
        self.assertFalse(root.exists())

    def test_archive_naming_a_missing_file_leaves_the_entry(self):
        root = self.entry('big', {'a.md': '# a'})
        run = self.apply(self.inventory(), {'big': {'verdict': 'ARCHIVE', 'reason': 'x',
                                                    'archiveFiles': ['a.md', 'gone.md']}})
        self.assertEqual(1, run.returncode, run.stdout + run.stderr)
        self.assertTrue((root / 'a.md').exists())

    def test_archive_file_list_escaping_the_entry_is_refused_before_any_action(self):
        root = self.entry('big', {'a.md': '# a'})
        other = self.entry('other')
        self.write(self.scratch / 'secret.md', 's')
        run = self.apply(self.inventory(), {
            'big': {'verdict': 'ARCHIVE', 'reason': 'x', 'archiveFiles': ['../secret.md']},
            'other': {'verdict': 'DELETE', 'reason': 'x'}})
        self.assertEqual(2, run.returncode, run.stdout + run.stderr)
        self.assertTrue(root.exists())
        self.assertTrue(other.exists())

    def test_archive_file_list_on_a_delete_verdict_is_refused(self):
        root = self.entry('big', {'a.md': '# a'})
        run = self.apply(self.inventory(), {'big': {'verdict': 'DELETE', 'reason': 'x',
                                                    'archiveFiles': ['a.md']}})
        self.assertEqual(2, run.returncode, run.stdout + run.stderr)
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
        self.track('tools/reader.py', '# reads .claude/scratch/bootcrash/notes.md\n')
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
