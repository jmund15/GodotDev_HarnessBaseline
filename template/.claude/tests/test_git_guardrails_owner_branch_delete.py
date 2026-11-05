"""Floor proof: `git branch -D` passes only when the delete loses no commit.

The guard denies only a loss it observes and reads no reply (CLAUDE.core.md §Shell Discipline); whether the
owner asked is the auto-mode classifier's to judge. An owner answer changes nothing and the deny names the preserve step
(`git update-ref refs/preserve/<name> <branch>`) that makes the same delete pass.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(os.environ.get('GUARD_HOOK_UNDER_TEST') or Path(__file__).resolve().parents[1] / 'hooks/git_guardrails.py')
ROW = json.loads((Path(__file__).parent / 'fixtures/owner_discard_answer_row.json').read_text(encoding='utf-8'))
QUESTION = ROW['toolUseResult']['questions'][0]['question']
BRANCH, OTHER = 'worktree-wf_old-1', 'feature/kept'
PRESERVE_HINT = 'git update-ref refs/preserve/<name> <branch>'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.email=t@t', '-c', 'user.name=t', *args],
                          check=True, capture_output=True, text=True)


def answer(question, label='Delete the branch (Recommended)'):
    row = copy.deepcopy(ROW)
    row['toolUseResult']['questions'][0]['question'] = question
    row['toolUseResult']['answers'] = {question: label}
    return row


def prompt(text):
    return {'type': 'user', 'message': {'role': 'user', 'content': text}, 'uuid': 'later-prompt'}


class BranchDelete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='owner_branch_')
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        git(self.root, 'init', '-q', '-b', 'main')
        (self.root / 'a.txt').write_text('a\n', encoding='utf-8')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'init')
        for name in (BRANCH, OTHER, BRANCH + '-x'):
            git(self.root, 'checkout', '-q', '-b', name, 'main')
            (self.root / 'a.txt').write_text(name + '\n', encoding='utf-8')
            git(self.root, 'commit', '-qam', 'unapplied ' + name)
        git(self.root, 'checkout', '-q', 'main')

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, command, rows=()):
        transcript = Path(self.tmp.name) / 'transcript.jsonl'
        transcript.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        payload = {'tool_name': 'Bash', 'cwd': str(self.root), 'session_id': 'owner-branch',
                   'transcript_path': str(transcript), 'tool_input': {'command': command}}
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env, timeout=90)
        self.assertIn(p.returncode, (0, 2), p.stderr)
        self.assertNotIn('Traceback', p.stderr)
        return p.returncode, p.stderr

    def test_unreachable_branch_denies_and_names_the_preserve_step(self):
        rc, err = self.run_hook(f'git branch -D {BRANCH}')
        self.assertEqual(2, rc)
        self.assertIn(PRESERVE_HINT, err)
        self.assertIn('holds commits no other ref has', err)

    def test_preserve_ref_makes_the_same_delete_pass(self):
        tip = git(self.root, 'rev-parse', f'refs/heads/{BRANCH}').stdout.strip()
        git(self.root, 'update-ref', 'refs/preserve/kept', tip)
        rc, err = self.run_hook(f'git branch -D {BRANCH}')
        self.assertEqual(0, rc, err)

    def test_owner_answer_does_not_unlock(self):
        rows = [answer(f'Force-delete branch {BRANCH} (superseded)?'), prompt(f'delete {BRANCH}')]
        self.assertEqual(2, self.run_hook(f'git branch -D {BRANCH}', rows)[0])

    def test_scratch_branch_allows(self):
        git(self.root, 'branch', 'scratch/fx', 'main')
        self.assertEqual(0, self.run_hook('git branch -D scratch/fx')[0])

    def test_merged_branch_allows(self):
        git(self.root, 'checkout', '-q', '-b', 'merged', 'main')
        (self.root / 'a.txt').write_text('merged\n', encoding='utf-8')
        git(self.root, 'commit', '-qam', 'merged work')
        git(self.root, 'checkout', '-q', 'main')
        git(self.root, 'merge', '-q', '--no-ff', '-m', 'merge', 'merged')
        self.assertEqual(0, self.run_hook('git branch -D merged')[0])

    def test_tag_holding_the_tip_allows(self):
        git(self.root, 'tag', 'keep-tip', BRANCH)
        self.assertEqual(0, self.run_hook(f'git branch -D {BRANCH}')[0])

    def test_missing_branch_denies(self):
        self.assertEqual(2, self.run_hook('git branch -D feature/missing')[0])

    def test_unknown_flag_denies(self):
        self.assertEqual(2, self.run_hook(f'git branch -r -D {OTHER}')[0])

    def test_two_branches_where_one_is_unreachable_denies(self):
        rc, err = self.run_hook(f'git branch -D {BRANCH} {OTHER}')
        self.assertEqual(2, rc)
        self.assertIn(PRESERVE_HINT, err)
        self.assertIn(BRANCH, err)
        self.assertIn(OTHER, err)

    def test_two_branches_holding_only_each_others_tip_deny(self):
        # Neither branch's tip is held by any ref outside the deleted set.
        rc, err = self.run_hook(f'git branch -D {BRANCH} {OTHER}')
        self.assertEqual(2, rc)
        self.assertIn('holds commits no other ref has', err)


if __name__ == '__main__':
    unittest.main(verbosity=2)
