#!/usr/bin/env python3
"""A session's transcript store is found from any checkout of the repo, not only the one the tool runs in.

Claude Code keys a session's transcript store on the directory it was LAUNCHED in. A review launched from
the main checkout whose shell sits in a linked worktree runs the worktree's copy of each tool; a tool that
derives the store from its own checkout path or the cwd then looks in the worktree's empty store.

    python3 .claude/tests/test_session_stores.py
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import post_check  # noqa: E402
import session_digest  # noqa: E402
import session_end_check  # noqa: E402
import task_record  # noqa: E402

SESSION = 'abcd1234-launch-session'
LABEL = 'verify-civlsz'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout


class Stores(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.main, self.config = base / 'main', base / 'cfg'
        self.main.mkdir()
        git(self.main, 'init', '-q')
        git(self.main, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'init')
        self.worktree = self.main / '.claude' / 'worktrees' / 'wt'
        git(self.main, 'worktree', 'add', '-q', '-b', 'wt', str(self.worktree))
        patch = mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config)})
        patch.start()
        self.addCleanup(patch.stop)
        self.main_store = session_digest.projects_dir(str(self.main))
        self.worktree_store = session_digest.projects_dir(str(self.worktree))

    def transcript(self, store=None):
        path = (store or self.main_store) / (SESSION + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}\n', encoding='utf-8')
        return path

    def live_journal(self, store, workflow='wf_a'):
        directory = store / SESSION / 'subagents' / 'workflows' / workflow
        directory.mkdir(parents=True)
        (directory / 'journal.jsonl').write_text(
            json.dumps(dict(type='started', label=LABEL, agentId='v1')) + '\n', encoding='utf-8')
        return directory

    def test_every_checkout_sees_the_same_set_of_stores(self):
        from_main = session_digest.project_dirs(str(self.main))
        from_worktree = session_digest.project_dirs(str(self.worktree))
        self.assertEqual(set(from_main), {self.main_store, self.worktree_store})
        self.assertEqual(set(from_worktree), set(from_main))
        self.assertEqual(from_worktree[0], self.worktree_store, 'the asked checkout leads')

    def test_a_directory_outside_git_has_only_its_own_store(self):
        with tempfile.TemporaryDirectory() as plain:
            self.assertEqual(session_digest.project_dirs(plain), [session_digest.projects_dir(plain)])

    def test_run_label_finds_the_launch_dir_store_from_a_worktree_copy(self):
        directory = self.live_journal(self.main_store)
        with mock.patch.object(post_check, 'REPO', self.worktree):
            self.assertEqual(post_check.run_label_directory(LABEL), directory)

    def test_run_label_still_requires_exactly_one_live_journal(self):
        self.live_journal(self.main_store)
        self.live_journal(self.worktree_store, 'wf_b')
        with mock.patch.object(post_check, 'REPO', self.worktree):
            with self.assertRaises(post_check.Unusable) as raised:
                post_check.run_label_directory(LABEL)
        self.assertIn('matched 2 live workflow journals', str(raised.exception))
        self.assertIn('wf_a', str(raised.exception), 'a duplicate names each live journal')
        self.assertIn('wf_b', str(raised.exception))

    def test_run_label_skips_a_torn_journal_line_and_keeps_the_rest(self):
        directory = self.live_journal(self.main_store)
        with (directory / 'journal.jsonl').open('a', encoding='utf-8') as stream:
            stream.write('{"type": "started", "lab\n[1, 2]\n')
            stream.write(json.dumps(dict(type='started', label='other', agentId='v2')) + '\n')
        with mock.patch.object(post_check, 'REPO', self.worktree):
            self.assertEqual(post_check.run_label_directory(LABEL), directory)

    def test_run_label_keeps_a_journal_holding_an_undecodable_byte(self):
        directory = self.live_journal(self.main_store)
        with (directory / 'journal.jsonl').open('ab') as stream:
            stream.write(b'{"type": "started", "label": "caf\xc3"}\n')
        with mock.patch.object(post_check, 'REPO', self.worktree):
            self.assertEqual(post_check.run_label_directory(LABEL), directory)

    def test_run_label_with_no_live_journal_names_where_it_searched(self):
        # wf_2db575d3-bd9: a worktree copy that searched only its own store reported a bare "matched 0".
        # The real repo has ~300 checkouts, so only the stores holding a journal are listed.
        finished = self.live_journal(self.main_store)
        with (finished / 'journal.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(type='result', agentId='v1')) + '\n')
        with mock.patch.object(post_check, 'REPO', self.worktree):
            with self.assertRaises(post_check.Unusable) as raised:
                post_check.run_label_directory(LABEL)
        message = str(raised.exception)
        self.assertIn('matched 0 live workflow journals', message)
        self.assertIn(f'searched 2 stores under {self.config / "projects"}', message)
        self.assertIn(f'journals in: {self.main_store.name}', message)
        self.assertNotIn(self.worktree_store.name, message, 'a store with no journal is only counted')

    def test_session_store_is_the_one_holding_the_session(self):
        self.transcript()
        self.assertEqual(session_digest.session_store(str(self.worktree), SESSION), self.main_store)
        self.assertEqual(session_digest.session_store(str(self.worktree), 'absent'), self.worktree_store)

    def test_session_store_reads_the_current_session_from_the_environment(self):
        self.transcript()
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': SESSION}):
            self.assertEqual(session_digest.session_store(str(self.worktree)), self.main_store)

    def test_pick_transcript_from_a_worktree_store_finds_the_session(self):
        expected = self.transcript()
        self.worktree_store.mkdir(parents=True)
        with mock.patch('os.getcwd', return_value=str(self.worktree)):
            self.assertEqual(session_digest.pick_transcript(self.worktree_store, SESSION, None), expected)

    def test_pick_transcript_leaves_an_unrelated_store_alone(self):
        self.transcript()
        with tempfile.TemporaryDirectory() as plain:
            elsewhere = session_digest.projects_dir(plain)
            elsewhere.mkdir(parents=True)
            with self.assertRaises(SystemExit):
                session_digest.pick_transcript(elsewhere, SESSION, None, cwd=plain)

    def test_task_record_reads_the_session_the_launch_dir_wrote(self):
        expected = self.transcript()
        with mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.worktree)}):
            self.assertEqual(Path(task_record._session_transcript(SESSION)), expected)

    def test_session_end_check_finds_the_session_from_a_worktree(self):
        expected = self.transcript()
        self.assertEqual(Path(session_end_check.transcript_for(SESSION, str(self.worktree))), expected)


if __name__ == '__main__':
    unittest.main()
