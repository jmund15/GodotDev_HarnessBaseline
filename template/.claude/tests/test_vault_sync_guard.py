"""Deletion, forbidden-path and staged-secret verdicts on planted git repos and change lists."""
import importlib.util
import shutil
import json
import subprocess
import sys
import tempfile
import unittest
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
TOKEN = 'ghp_' + 'a1B2c3' * 6


def git(repo, *args):
    subprocess.run(['git', '-C', str(repo), '-c', 'core.autocrlf=false', *args], check=True, capture_output=True)


def plant(repo, files):
    for rel, text in files.items():
        path = Path(repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')


class Classifier(unittest.TestCase):
    def test_deletion_of_a_path_that_still_exists_is_refused(self):
        out = vs.check_changes([{'status': 'D', 'path': 'Proj/a.md'}], exists=lambda rel: True)
        self.assertEqual([(r['kind'], r['path']) for r in out['refusals']], [('deletion-exists', 'Proj/a.md')])

    def test_deletion_of_a_gone_path_passes(self):
        out = vs.check_changes([{'status': 'D', 'path': 'Proj/a.md'}], exists=lambda rel: False)
        self.assertEqual((out['refusals'], out['deletions']), ([], 1))

    def test_case_only_rename_is_not_a_deletion(self):
        changes = [{'status': 'D', 'path': 'Proj/Note.md'}, {'status': 'A', 'path': 'Proj/note.md'}]
        out = vs.check_changes(changes, exists=lambda rel: True)
        self.assertEqual((out['refusals'], out['deletions']), ([], 0))
        self.assertEqual(out['caseRenames'], [{'from': 'Proj/Note.md', 'to': 'Proj/note.md'}])

    def test_rename_is_not_a_deletion(self):
        out = vs.check_changes([{'status': 'R', 'path': 'Proj/b.md', 'old': 'Proj/a.md'}],
                               exists=lambda rel: True)
        self.assertEqual((out['refusals'], out['deletions']), ([], 0))

    def test_cap_allows_exactly_the_limit(self):
        gone = [{'status': 'D', 'path': f'Proj/{n}.md'} for n in range(vs.DELETION_CAP)]
        self.assertEqual(vs.check_changes(gone, exists=lambda rel: False)['refusals'], [])
        over = gone + [{'status': 'D', 'path': 'Proj/extra.md'}]
        self.assertEqual([r['kind'] for r in vs.check_changes(over, exists=lambda rel: False)['refusals']], ['deletion-cap'])
        self.assertEqual(vs.check_changes(over, exists=lambda rel: False, cap=len(over))['refusals'], [])

    def test_forbidden_paths_are_refused_for_any_status(self):
        out = vs.check_changes([{'status': 'A', 'path': 'AI/API Keys/k.md'}, {'status': 'M', 'path': 'x.md'}],
                               exists=lambda rel: True)
        self.assertEqual([r['kind'] for r in out['refusals']], ['forbidden', 'forbidden'])

    def test_onedrive_conflict_copy_names_are_reported_not_refused(self):
        changes = [{'status': 'A', 'path': 'Proj/Plan-DESKTOP-4K2LQ8P.md'},
                   {'status': 'A', 'path': 'Proj/Plan.md'}]
        out = vs.check_changes(changes, exists=lambda rel: True)
        self.assertEqual((out['refusals'], out['conflictCopies']), ([], ['Proj/Plan-DESKTOP-4K2LQ8P.md']))


class Precommit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, 'init', '-q')

    def tearDown(self):
        self.tmp.cleanup()

    def committed(self, files):
        plant(self.repo, files)
        git(self.repo, 'add', '-A')
        git(self.repo, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', 'base')

    def kinds(self):
        code, report = vs.precommit(self.repo)
        return code, sorted({r['kind'] for r in report['refusals']})

    def test_clean_staged_change_passes(self):
        plant(self.repo, {'Proj/a.md': 'plain'})
        git(self.repo, 'add', '-A')
        self.assertEqual(self.kinds(), (0, []))

    def test_staged_deletion_of_a_file_still_on_disk_is_refused(self):
        self.committed({'Proj/a.md': 'one'})
        git(self.repo, 'rm', '-q', '--cached', 'Proj/a.md')
        self.assertEqual(self.kinds(), (1, ['deletion-exists']))

    def test_deletion_of_a_gone_file_passes(self):
        self.committed({'Proj/a.md': 'one'})
        git(self.repo, 'rm', '-q', 'Proj/a.md')
        self.assertEqual(self.kinds(), (0, []))

    def test_vanished_folder_hits_the_cap_and_the_flag_releases_it(self):
        count = vs.DELETION_CAP + 1
        self.committed({f'Proj/d/{n}.md': f'note {n}' for n in range(count)})
        git(self.repo, 'rm', '-q', '-r', 'Proj/d')
        self.assertEqual(self.kinds(), (1, ['deletion-cap']))
        code, _ = vs.precommit(self.repo, allow_deletions=count)
        self.assertEqual(code, 0)

    def test_a_moved_folder_is_not_a_mass_deletion(self):
        count = vs.DELETION_CAP + 5
        self.committed({f'Proj/old/{n}.md': f'distinct note body number {n} ' * 8 for n in range(count)})
        git(self.repo, 'mv', 'Proj/old', 'Proj/new')
        self.assertEqual(self.kinds(), (0, []))

    def test_staged_ai_path_is_refused(self):
        plant(self.repo, {'AI/API Keys/k.md': 'x'})
        git(self.repo, 'add', '-A')
        self.assertEqual(self.kinds(), (1, ['forbidden']))

    def test_staged_blob_is_scanned_not_the_working_file(self):
        plant(self.repo, {'Proj/n.md': 'key ' + TOKEN})
        git(self.repo, 'add', '-A')
        plant(self.repo, {'Proj/n.md': 'cleaned afterwards'})
        code, report = vs.precommit(self.repo)
        self.assertEqual((code, [(r['kind'], r['path'], r['pattern']) for r in report['refusals']]),
                         (1, [('secret', 'Proj/n.md', 'ghp')]))
        self.assertNotIn(TOKEN, json.dumps(report))

    def test_modified_file_with_a_secret_is_refused(self):
        self.committed({'Proj/n.md': 'fine'})
        plant(self.repo, {'Proj/n.md': TOKEN})
        git(self.repo, 'add', '-A')
        self.assertEqual(self.kinds(), (1, ['secret']))

    def test_all_reasons_are_named_in_one_report(self):
        self.committed({'Proj/a.md': 'one'})
        git(self.repo, 'rm', '-q', '--cached', 'Proj/a.md')
        plant(self.repo, {'AI/k.md': 'x', 'Proj/s.md': TOKEN})
        git(self.repo, 'add', 'AI/k.md', 'Proj/s.md')
        self.assertEqual(self.kinds()[1], ['deletion-exists', 'forbidden', 'secret'])


class HookCli(unittest.TestCase):
    def test_refusal_exits_one_and_names_the_reason_without_the_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            git(tmp, 'init', '-q')
            plant(tmp, {'Proj/n.md': TOKEN})
            git(tmp, 'add', '-A')
            result = subprocess.run([sys.executable, str(STAGED), 'precommit'], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('secret', result.stdout + result.stderr)
            self.assertNotIn(TOKEN, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
