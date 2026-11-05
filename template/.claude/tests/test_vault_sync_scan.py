"""Scan key shapes, exclusions, unreadable files and secret-free output on planted trees."""
import importlib.util
import shutil
import json
import os
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

SAMPLES = {
    'sk-ant': 'sk-ant-' + 'a1B2' * 6,
    'sk': 'sk-' + 'Zx9Q' * 6,
    'ghp': 'ghp_' + 'a1B2c3' * 6,
    'github_pat': 'github_pat_' + 'A1b2C3' * 5,
    'AKIA': 'AKIA' + 'ABCD1234EFGH5678',
    'AIza': 'AIza' + 'Sy' + 'a1B2c3D4e5' * 3 + 'xyz',
    'xox': 'xoxb-' + '1234567890-abcdef',
    'hf': 'hf_' + 'aB3dE5' * 6,
    'private-key': '-----BEGIN RSA PRIVATE ' + 'KEY-----',
}


def plant(root, files):
    for rel, data in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))


class ScanBytes(unittest.TestCase):
    def test_each_key_shape_is_a_hit_under_its_own_name(self):
        for name, token in SAMPLES.items():
            with self.subTest(name):
                self.assertEqual(vs.scan_bytes(f'note\nthe key is {token} end\n'.encode()), [name])

    def test_prose_that_only_names_a_prefix_is_clean(self):
        prose = ('ghp tokens, the sk- prefix, risk-free, task-based, hf_ scheme and AKIA keys are discussed here. '
                 'ANTHROPIC_API_KEY=sk-ant-placeholder is a documented placeholder.')
        self.assertEqual(vs.scan_bytes(prose.encode()), [])

    def test_binary_content_is_scanned_without_a_decode_error(self):
        self.assertEqual(vs.scan_bytes(b'\xff\xfe\x00\x01' + SAMPLES['ghp'].encode() + b'\x80\x81'), ['ghp'])


class CheckPath(unittest.TestCase):
    def test_ai_folder_and_unlisted_folders_are_refused(self):
        for rel in ('AI/API Keys/note.md', 'PeerA/x.md', 'PeerB/x.md', 'PeerC/x.md',
                    'x.md', 'Proj/.obsidian/app.json', 'Proj/Claude/cache.db'):
            self.assertIsNotNone(vs.forbidden_path(rel), rel)

    def test_allowlisted_folders_pass(self):
        for rel in ('Proj/Claude/a.md', 'Proj/b.md', 'Proj/c/d.md',
                    'Proj/Claude/Archived/Scratch/2026-10/e/f.md'):
            self.assertIsNone(vs.forbidden_path(rel), rel)


class ScanTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_tree_reports_nothing_and_counts_files(self):
        plant(self.root, {'Proj/a.md': 'plain', 'Proj/b.md': 'plain too'})
        report = vs.scan_tree(self.root)
        self.assertEqual((report['hits'], report['unreadable'], report['forbidden']), ([], [], []))
        self.assertEqual(report['scanned'], 2)

    def test_hit_names_path_and_pattern(self):
        plant(self.root, {'Proj/n/k.md': 'x ' + SAMPLES['hf']})
        self.assertEqual(vs.scan_tree(self.root)['hits'], [{'path': 'Proj/n/k.md', 'pattern': 'hf'}])

    def test_ai_folder_is_never_walked_or_read(self):
        plant(self.root, {'AI/API Keys/k.md': SAMPLES['ghp'], 'Proj/a.md': 'ok',
                          'PeerA/k.md': SAMPLES['ghp'], 'PeerB/k.md': SAMPLES['ghp'],
                          'PeerC/k.md': SAMPLES['ghp']})
        report = vs.scan_tree(self.root)
        self.assertEqual((report['hits'], report['forbidden']), ([], []))
        self.assertEqual(report['scanned'], 1)

    def test_excluded_names_inside_allowlisted_folders_are_skipped(self):
        plant(self.root, {'Proj/.obsidian/app.json': SAMPLES['ghp'],
                          'Proj/.search-index/x': SAMPLES['ghp'],
                          'Proj/x.db': SAMPLES['ghp'], 'Proj/ok.md': 'ok'})
        report = vs.scan_tree(self.root)
        self.assertEqual((report['hits'], report['scanned']), ([], 1))

    @unittest.skipIf(os.name == 'nt', 'a directory-permission read failure is planted with chmod')
    def test_unreadable_file_is_reported_not_skipped(self):
        plant(self.root, {'Proj/locked.md': 'x'})
        target = self.root / 'Proj/locked.md'
        target.chmod(0)
        try:
            if os.access(target, os.R_OK):
                self.skipTest('running as a user that ignores file modes')
            report = vs.scan_tree(self.root)
        finally:
            target.chmod(0o644)
        self.assertEqual(report['unreadable'], ['Proj/locked.md'])

    def test_listed_file_that_cannot_be_opened_is_unreadable(self):
        plant(self.root, {'Proj/a.md': 'x'})
        report = vs.scan_paths(self.root, ['Proj/a.md', 'Proj/gone.md'])
        self.assertEqual(report['unreadable'], ['Proj/gone.md'])


class Cli(unittest.TestCase):
    def run_scan(self, root):
        return subprocess.run([sys.executable, str(STAGED), 'scan', '--tree', '--root', str(root)],
                              capture_output=True, text=True)

    def test_exit_codes_and_output_never_carry_the_matched_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            plant(tmp, {'Proj/a.md': 'fine'})
            clean = self.run_scan(tmp)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            plant(tmp, {'Proj/b.md': SAMPLES['sk-ant']})
            hit = self.run_scan(tmp)
            self.assertEqual(hit.returncode, 1, hit.stderr)
            self.assertNotIn(SAMPLES['sk-ant'], hit.stdout + hit.stderr)
            self.assertEqual(json.loads(hit.stdout)['hits'], [{'path': 'Proj/b.md', 'pattern': 'sk-ant'}])

    def test_missing_root_is_an_error_not_a_clean_scan(self):
        result = self.run_scan(Path(tempfile.gettempdir()) / 'vault_sync_no_such_root_xyz')
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
