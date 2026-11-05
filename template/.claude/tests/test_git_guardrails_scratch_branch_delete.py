"""Floor proof: `git branch -D` of a `scratch/<name>` fixture branch is allowed with or without `-q`, and
a non-scratch unapplied branch stays denied with or without `-q` until a preserve ref holds its tip.

The guard reads no reply, so a transcript
answer is not an unlock; preserving the tip is."""
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
SCRATCH_HEAD, SCRATCH_WORK, FEATURE = 'scratch/wl-q4-fix', 'scratch/wl-q4-work', 'feature/kept'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.email=t@t', '-c', 'user.name=t', *args],
                          check=True, capture_output=True, text=True)


def answer(question, label='Delete the branch (Recommended)'):
    row = copy.deepcopy(ROW)
    row['toolUseResult']['questions'][0]['question'] = question
    row['toolUseResult']['answers'] = {question: label}
    return row


class ScratchBranchDelete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='scratch_branch_')
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        git(self.root, 'init', '-q', '-b', 'main')
        (self.root / 'a.txt').write_text('a\n', encoding='utf-8')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'init')
        git(self.root, 'branch', SCRATCH_HEAD)
        git(self.root, 'checkout', '-q', '-b', SCRATCH_WORK, 'main')
        (self.root / 'a.txt').write_text('work\n', encoding='utf-8')
        git(self.root, 'commit', '-qam', 'unapplied scratch work')
        git(self.root, 'checkout', '-q', '-b', FEATURE, 'main')
        (self.root / 'a.txt').write_text('feature\n', encoding='utf-8')
        git(self.root, 'commit', '-qam', 'unapplied feature')
        git(self.root, 'checkout', '-q', 'main')

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, command, rows=()):
        transcript = Path(self.tmp.name) / 'transcript.jsonl'
        transcript.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        payload = {'tool_name': 'Bash', 'cwd': str(self.root), 'session_id': 'scratch-branch',
                   'transcript_path': str(transcript), 'tool_input': {'command': command}}
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env, timeout=90)
        self.assertIn(p.returncode, (0, 2), p.stderr)
        self.assertNotIn('Traceback', p.stderr)
        return p.returncode, p.stderr

    def test_scratch_branch_at_head_allows(self):
        rc, err = self.run_hook(f'git branch -D {SCRATCH_HEAD}')
        self.assertEqual(0, rc, err)

    def test_scratch_branch_at_head_allows_with_quiet_flag(self):
        rc, err = self.run_hook(f'git branch -q -D {SCRATCH_HEAD}')
        self.assertEqual(0, rc, err)

    def test_scratch_branch_with_unapplied_commits_allows_with_quiet_flag(self):
        rc, err = self.run_hook(f'git branch -q -D {SCRATCH_WORK}')
        self.assertEqual(0, rc, err)

    def test_unapplied_non_scratch_branch_denies_with_or_without_quiet(self):
        self.assertEqual(2, self.run_hook(f'git branch -D {FEATURE}')[0])
        self.assertEqual(2, self.run_hook(f'git branch -q -D {FEATURE}')[0])

    def test_mixed_scratch_and_unapplied_denies(self):
        self.assertEqual(2, self.run_hook(f'git branch -q -D {SCRATCH_HEAD} {FEATURE}')[0])

    def test_missing_scratch_branch_denies(self):
        self.assertEqual(2, self.run_hook('git branch -q -D scratch/missing')[0])

    def test_unknown_flag_still_fails_closed(self):
        self.assertEqual(2, self.run_hook(f'git branch -r -D {SCRATCH_WORK}')[0])

    def test_owner_answer_does_not_unlock_an_unapplied_branch(self):
        rows = [answer(f'Force-delete branch {FEATURE}?')]
        rc, err = self.run_hook(f'git branch -q -D {FEATURE}', rows)
        self.assertEqual(2, rc)
        self.assertIn('refs/preserve/', err)

    def test_preserve_ref_lets_the_same_unapplied_delete_pass_with_quiet_flag(self):
        tip = git(self.root, 'rev-parse', f'refs/heads/{FEATURE}').stdout.strip()
        git(self.root, 'update-ref', 'refs/preserve/kept', tip)
        rc, err = self.run_hook(f'git branch -q -D {FEATURE}')
        self.assertEqual(0, rc, err)


if __name__ == '__main__':
    unittest.main(verbosity=2)
