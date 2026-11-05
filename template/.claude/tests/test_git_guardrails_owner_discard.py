"""Floor proof: a checkout/restore discard passes only when git holds the bytes it overwrites.

The guard denies only a loss it observes and reads no reply (CLAUDE.core.md §Shell Discipline); whether the
owner asked is the auto-mode classifier's to judge. A path-scoped restore or a checkout from the index now qualifies when the
object store holds the worktree bytes, a `__pycache__` path is regenerable, and a checkout from another ref
still judges the index it overwrites.
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
A, B = 'Tests/regression_baseline.json', 'Tests/integration_batch_durations.json'
EXTRA = ('LICENSE', 'addons/foo.v2/x.txt', '.claude/x.txt')
PRESERVE_STEP = 'git update-ref refs/preserve/'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.email=t@t', '-c', 'user.name=t', *args],
                          check=True, capture_output=True, text=True)


def answer(label=None, question=None):
    row = copy.deepcopy(ROW)
    q = question or QUESTION
    if question is not None:
        row['toolUseResult']['questions'][0]['question'] = question
        row['toolUseResult']['answers'] = {question: row['toolUseResult']['answers'][QUESTION]}
    if label is not None:
        row['toolUseResult']['answers'][q] = label
    return row


def prompt(text):
    return {'type': 'user', 'message': {'role': 'user', 'content': text}, 'uuid': 'later-prompt'}


class DiscardLosesNothing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='owner_discard_')
        self.root = Path(self.tmp.name) / 'repo'
        (self.root / 'Tests').mkdir(parents=True)
        git(self.root, 'init', '-q', '-b', 'main')
        for path in EXTRA:
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        for path in (A, B, 'Tests/other.json', *EXTRA):
            (self.root / path).write_text('{}\n', encoding='utf-8')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'init')
        for path in (A, B, 'Tests/other.json', *EXTRA):
            (self.root / path).write_text('{"dirty": 1}\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def at(self, path, root=None):
        return str((root or self.root) / path)

    def approve(self, label=None, paths=(A, B)):
        """The fixture's question with each path spelled absolute in this test's checkout."""
        question = QUESTION
        for path in (A, B):
            question = question.replace(path, self.at(path) if path in paths else path)
        return answer(label, question)

    def run_hook(self, command, rows):
        transcript = Path(self.tmp.name) / 'transcript.jsonl'
        transcript.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        payload = {'tool_name': 'Bash', 'cwd': str(self.root), 'session_id': 'owner-discard',
                   'transcript_path': str(transcript), 'tool_input': {'command': command}}
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env, timeout=90)
        self.assertIn(p.returncode, (0, 2), p.stderr)
        self.assertNotIn('Traceback', p.stderr)
        return p.returncode, p.stderr

    def test_restore_of_stored_bytes_allows(self):
        git(self.root, 'hash-object', '-w', A)
        self.assertEqual(0, self.run_hook(f'git restore {A}', [])[0])

    def test_checkout_of_stored_bytes_allows(self):
        # A checkout from the index loses nothing when the store holds the worktree bytes.
        git(self.root, 'hash-object', '-w', A)
        self.assertEqual(0, self.run_hook(f'git checkout -- {A}', [])[0])

    def test_restore_of_unsaved_bytes_denies_and_names_the_preserve_step(self):
        rc, err = self.run_hook(f'git restore {A}', [])
        self.assertEqual(2, rc)
        self.assertIn('save it first', err)
        self.assertIn(PRESERVE_STEP, err)

    def test_checkout_of_unsaved_bytes_denies(self):
        self.assertEqual(2, self.run_hook(f'git checkout -- {A}', [])[0])

    def test_saving_the_bytes_makes_the_same_delete_pass(self):
        rc, err = self.run_hook(f'git restore {A}', [])
        self.assertEqual(2, rc)
        git(self.root, 'hash-object', '-w', A)
        self.assertEqual(0, self.run_hook(f'git restore {A}', [])[0])

    def test_checkout_from_head_deny_names_a_next_step_that_passes(self):
        # Rows F736, F741: `git checkout HEAD -- <a> <b>` over this session's own abandoned edits. The deny names
        # the save and the `restore --source=HEAD` retry; running both passes with no owner reply.
        command = f'git checkout HEAD -- {A} {B} 2>&1 | tail -3; git status --short | grep -v "^??"'
        rc, err = self.run_hook(command, [])
        self.assertEqual(2, rc)
        self.assertIn(PRESERVE_STEP, err)
        self.assertIn('git restore --source=HEAD -- <path>', err)
        for path in (A, B):
            git(self.root, 'hash-object', '-w', path)
        self.assertEqual(0, self.run_hook(f'git restore --source=HEAD -- {A} {B}', [])[0])

    def test_checkout_from_head_deny_still_holds_for_an_unsaved_path(self):
        (self.root / B).write_text('{"dirty": 2}\n', encoding='utf-8')   # bytes unlike A's, so saving A does not store B's
        git(self.root, 'hash-object', '-w', A)
        self.assertEqual(2, self.run_hook(f'git restore --source=HEAD -- {A} {B}', [])[0])

    def test_owner_answer_does_not_unlock(self):
        for rows in ([self.approve()], [prompt(f'reset {A}')]):
            with self.subTest(rows=len(rows)):
                self.assertEqual(2, self.run_hook(f'git checkout -- {A}', rows)[0])

    def test_sidechain_answer_does_not_unlock(self):
        side = self.approve()
        side['isSidechain'] = True
        self.assertEqual(2, self.run_hook(f'git restore {A}', [side])[0])

    def test_checkout_from_head_of_a_clean_path_allows(self):
        (self.root / A).write_text('{}\n', encoding='utf-8')       # back to the committed bytes: no edit to lose
        self.assertEqual(0, self.run_hook(f'git checkout HEAD -- {A}', [])[0])

    def test_checkout_from_another_ref_over_an_edit_denies(self):
        self.assertEqual(2, self.run_hook(f'git checkout HEAD -- {A}', [])[0])

    def test_pycache_path_allows(self):
        cache = self.root / 'Tools/Calc/__pycache__/calc.pyc'
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b'bytecode')
        self.assertEqual(0, self.run_hook('git checkout -- Tools/Calc/__pycache__/calc.pyc', [])[0])

    def test_broad_pathspec_denies(self):
        for command in ('git checkout -- .', 'git restore Tests/*.json', 'git checkout -- Tests', 'git restore .'):
            with self.subTest(command=command):
                self.assertEqual(2, self.run_hook(command, [self.approve()])[0])

    def test_rider_on_unnamed_path_denies(self):
        self.assertEqual(2, self.run_hook(f'git checkout -- {A}; git checkout -- Tests/other.json', [self.approve()])[0])

    def test_restore_staged_alone_allows(self):
        self.assertEqual(0, self.run_hook('git restore --staged ' + A, [])[0])

    def test_worktree_bytes_the_store_lacks_deny_without_an_answer(self):
        (self.root / A).write_text('{"unsaved": 2}\n', encoding='utf-8')
        self.assertEqual(2, self.run_hook(f'git restore {A}', [])[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
