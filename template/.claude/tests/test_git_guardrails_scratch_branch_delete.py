"""Floor proof: `git branch -D` of a `scratch/<name>` fixture branch is allowed with or without `-q`, and
a non-scratch unapplied branch stays denied with or without `-q` until a preserve ref holds its tip.

The guard reads no reply, so a transcript
answer is not an unlock; preserving the tip is.

Decision cases call the hook in process (`_hook_call`); one case spawns it, proving it imports and runs from its
registered command. The repository is built once per class and each case resets it by copy, so a deleted branch or a
preserve ref reaches no other case.
"""
import atexit
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook_call  # noqa: E402

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


def build_repo(root):
    """A clean `main` at HEAD, a scratch branch at it, and two branches holding unapplied commits."""
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    (root / 'a.txt').write_text('a\n', encoding='utf-8')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'init')
    git(root, 'branch', SCRATCH_HEAD)
    git(root, 'checkout', '-q', '-b', SCRATCH_WORK, 'main')
    (root / 'a.txt').write_text('work\n', encoding='utf-8')
    git(root, 'commit', '-qam', 'unapplied scratch work')
    git(root, 'checkout', '-q', '-b', FEATURE, 'main')
    (root / 'a.txt').write_text('feature\n', encoding='utf-8')
    git(root, 'commit', '-qam', 'unapplied feature')
    git(root, 'checkout', '-q', 'main')


class ScratchBranchDelete(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = tempfile.mkdtemp(prefix='scratch_branch_tpl_')
        build_repo(Path(cls.template) / 'repo')
        atexit.register(shutil.rmtree, cls.template, True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='scratch_branch_')
        self.root = Path(self.tmp.name) / 'repo'
        _hook_call.copy_repo(str(Path(self.template) / 'repo'), str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, command, rows):
        transcript = Path(self.tmp.name) / 'transcript.jsonl'
        transcript.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        return {'tool_name': 'Bash', 'cwd': str(self.root), 'session_id': 'scratch-branch',
                'transcript_path': str(transcript), 'tool_input': {'command': command}}

    @staticmethod
    def env():
        return {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}

    def run_hook(self, command, rows=()):
        code, out, err, _ = _hook_call.call(str(HOOK), self.payload(command, rows), env=self.env(),
                                            cwd=str(self.root))
        self.assertIn(code, (0, 2), err)
        self.assertNotIn('Traceback', err)
        return code, err

    def test_spawned_hook_imports_and_runs_from_its_command(self):
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(self.payload('git status', [])),
                           capture_output=True, text=True, env=self.env(), cwd=str(self.root), timeout=90)
        self.assertEqual(0, p.returncode, p.stderr)

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
