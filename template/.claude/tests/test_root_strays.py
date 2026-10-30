"""root_strays.py contracts on a planted git repo."""
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
TOOL = ROOT / '.claude/tools/root_strays.py'
spec = importlib.util.spec_from_file_location('root_strays', TOOL)
strays = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strays)

DAY = 86400


class RootStraysTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.repo = Path(self.temp)
        self.git('init', '-q')

    def git(self, *args, when=None):
        env = dict(os.environ)
        if when is not None:
            stamp = f'{int(time.time() - when * DAY)} +0000'
            env['GIT_AUTHOR_DATE'] = env['GIT_COMMITTER_DATE'] = stamp
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.email=t@t', '-c', 'user.name=t',
                        '-c', 'core.autocrlf=false', *args],
                       check=True, capture_output=True, text=True, env=env)

    def commit(self, files, days_old):
        for rel, text in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'c', when=days_old)

    def names(self, min_age=14):
        return {row['name']: row for row in strays.candidates(self.repo, min_age)}

    def test_unreferenced_old_dir_and_file_are_candidates(self):
        self.commit({'.claude/old_dir/a.txt': 'x', '.claude/old_note.md': 'x'}, 60)
        found = self.names()
        self.assertEqual(set(found), {'old_dir', 'old_note.md'})
        self.assertEqual(found['old_dir']['files'], 1)
        self.assertGreaterEqual(found['old_dir']['ageDays'], 59)

    def test_name_mentioned_by_another_tracked_file_is_not_a_candidate(self):
        self.commit({'.claude/data_dir/a.txt': 'x', '.claude/tools/use.py': "open('.claude/data_dir/a.txt')"}, 60)
        self.assertNotIn('data_dir', self.names())

    def test_a_mention_inside_the_entry_itself_does_not_count(self):
        self.commit({'.claude/self_dir/readme.md': 'self_dir describes itself'}, 60)
        self.assertIn('self_dir', self.names())

    def test_lock_file_mention_does_not_count(self):
        self.commit({'.claude/locked_dir/a.txt': 'x', '.claude/baseline.lock.json': '{"locked_dir": 1}'}, 60)
        self.assertIn('locked_dir', self.names())

    def test_recent_entry_is_not_a_candidate(self):
        self.commit({'.claude/fresh_dir/a.txt': 'x'}, 1)
        self.assertNotIn('fresh_dir', self.names())

    def test_age_is_the_last_commit_touching_the_entry(self):
        self.commit({'.claude/touched_dir/a.txt': 'x'}, 60)
        self.commit({'.claude/touched_dir/a.txt': 'y'}, 1)
        self.assertNotIn('touched_dir', self.names())

    def test_untracked_entry_is_ignored(self):
        self.commit({'.claude/tracked/a.txt': 'x'}, 60)
        (self.repo / '.claude/loose_dir').mkdir()
        (self.repo / '.claude/loose_dir/a.txt').write_text('x', encoding='utf-8')
        self.assertNotIn('loose_dir', self.names())

    def test_main_prints_json_and_exits_zero(self):
        self.commit({'.claude/old_dir/a.txt': 'x'}, 60)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = strays.main(['list', '--repo', str(self.repo)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf.getvalue())['candidates'][0]['name'], 'old_dir')


if __name__ == '__main__':
    unittest.main()
