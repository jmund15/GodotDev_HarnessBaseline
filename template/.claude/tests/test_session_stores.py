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
import model_registry  # noqa: E402
import orchestration_metrics  # noqa: E402
import extract_subagent_tools  # noqa: E402
import wake_digest  # noqa: E402
import review_ledger  # noqa: E402
import reap  # noqa: E402
import dispatch_table  # noqa: E402
import peer_message_guard  # noqa: E402

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
        for module in (model_registry,):
            patch = mock.patch.object(module, 'login_dirs', return_value=[str(self.config)])
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

    def test_injected_checkout_paths_remain_the_lookup_paths(self):
        main_store = self.config.parent / 'injected-main' / 'main-store'
        worktree_store = self.config.parent / 'injected-worktree' / 'worktree-store'
        expected = self.transcript(main_store)
        def injected(cwd):
            return worktree_store if Path(cwd) == self.worktree else main_store
        with mock.patch.object(session_digest, 'projects_dir', side_effect=injected):
            self.assertEqual(session_digest.project_dirs(str(self.worktree))[:2],
                             [worktree_store, main_store])
            self.assertEqual(session_digest.session_store(str(self.worktree), SESSION), main_store)
            self.assertEqual(session_digest.pick_transcript(worktree_store, SESSION, None,
                                                           cwd=str(self.worktree)), expected)
            self.assertEqual(Path(session_end_check.transcript_for(SESSION, str(self.worktree))), expected)
            with mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.worktree)}):
                self.assertEqual(Path(task_record._session_transcript(SESSION)), expected)
            directory = self.live_journal(main_store)
            with mock.patch.object(post_check, 'REPO', self.worktree):
                self.assertEqual(post_check.run_label_directory(LABEL), directory)

    def test_task_record_reads_the_session_the_launch_dir_wrote(self):
        expected = self.transcript()
        with mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.worktree)}):
            self.assertEqual(Path(task_record._session_transcript(SESSION)), expected)

    def test_session_end_check_finds_the_session_from_a_worktree(self):
        expected = self.transcript()
        self.assertEqual(Path(session_end_check.transcript_for(SESSION, str(self.worktree))), expected)


class CrossLoginStores(Stores):
    def setUp(self):
        super().setUp()
        self.other = self.config.parent / 'other-login'
        self.foreign_store = self.other / 'projects' / self.main_store.name
        for module in (model_registry,):
            patch = mock.patch.object(module, 'login_dirs', return_value=[str(self.other), str(self.config), str(self.other)])
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(self.worktree),
                                           'CLAUDE_CODE_SESSION_ID': SESSION})
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch('os.getcwd', return_value=str(self.worktree))
        patch.start()
        self.addCleanup(patch.stop)
        for module in (extract_subagent_tools, reap):
            patch = mock.patch.object(module, 'PROJECTS_ROOT', self.config / 'projects')
            patch.start()
            self.addCleanup(patch.stop)

    def test_every_checkout_sees_the_same_set_of_stores(self):
        stores = session_digest.project_dirs(str(self.worktree))
        self.assertEqual(stores, [self.worktree_store, self.main_store,
                                 self.other / 'projects' / self.worktree_store.name, self.foreign_store])

    def test_a_directory_outside_git_has_only_its_own_store(self):
        with tempfile.TemporaryDirectory() as plain:
            current = session_digest.projects_dir(plain)
            self.assertEqual(session_digest.project_dirs(plain),
                             [current, self.other / 'projects' / current.name])

    def test_run_label_with_no_live_journal_names_where_it_searched(self):
        with mock.patch.object(post_check, 'REPO', self.worktree):
            with self.assertRaises(post_check.Unusable) as raised:
                post_check.run_label_directory(LABEL)
        self.assertIn('searched 4 stores', str(raised.exception))

    def lookups(self):
        return {
            'session_store': lambda: session_digest.session_store(str(self.worktree), SESSION) / (SESSION + '.jsonl'),
            'task_record': lambda: task_record._session_transcript(SESSION),
            'session_end_check': lambda: session_end_check.transcript_for(SESSION, str(self.worktree)),
            'pick_transcript': lambda: session_digest.pick_transcript(self.worktree_store, SESSION, None),
            'orchestration_metrics': lambda: (Path(orchestration_metrics.find_session_dir(SESSION) + '.jsonl')
                                              if orchestration_metrics.find_session_dir(SESSION) else None),
            'extract_subagent_tools': lambda: extract_subagent_tools.find_session_jsonl(SESSION),
            'wake_digest': lambda: wake_digest.transcript_path(SESSION),
            'reap': lambda: reap.session_transcript(SESSION),
            'dispatch_table': lambda: dispatch_table._transcript(SESSION),
            'session_end_prefix': lambda: session_end_check.transcript_for(SESSION[:8], str(self.worktree)),
            'pick_prefix': lambda: session_digest.pick_transcript(self.worktree_store, SESSION[:8], None),
        }

    def test_identity_lookups_find_the_parent_in_another_login(self):
        expected = self.transcript(self.foreign_store)
        with mock.patch.object(orchestration_metrics, '_projects_root', return_value=str(self.config / 'projects')):
            for name, lookup in self.lookups().items():
                with self.subTest(lookup=name):
                    found = lookup()
                    self.assertIsNotNone(found)
                    self.assertEqual(Path(found), expected)

    def test_current_login_wins_without_reading_a_foreign_store(self):
        expected = self.transcript(self.main_store)
        self.transcript(self.foreign_store)
        original_stat = Path.stat
        def guarded_stat(path, *args, **kwargs):
            if path == self.other or self.other in path.parents:
                self.fail('read another login despite a current-login match: ' + str(path))
            return original_stat(path, *args, **kwargs)
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(Path, 'stat', guarded_stat))
            for operation in ('stat', 'listdir', 'scandir'):
                original = getattr(os, operation)
                def guarded(path, *args, _original=original, **kwargs):
                    if isinstance(path, (str, os.PathLike)):
                        checked = Path(path)
                        if checked == self.other or self.other in checked.parents:
                            self.fail('read a foreign store: ' + str(path))
                    return _original(path, *args, **kwargs)
                stack.enter_context(mock.patch.object(os, operation, guarded))
            stack.enter_context(mock.patch.object(orchestration_metrics, '_projects_root', return_value=str(self.config / 'projects')))
            for name, lookup in self.lookups().items():
                with self.subTest(lookup=name):
                    found = lookup()
                    self.assertIsNotNone(found)
                    self.assertEqual(Path(found), expected)

    def test_current_root_is_yielded_without_loading_other_logins(self):
        with mock.patch.object(model_registry, 'login_dirs', side_effect=AssertionError('loaded registry')):
            self.assertEqual(Path(next(model_registry.session_project_roots())), self.config / 'projects')

    def test_default_login_is_first_when_config_dir_is_unset(self):
        with mock.patch.dict(os.environ), mock.patch.object(model_registry, 'DEFAULT_LOGIN_DIR', str(self.config)):
            os.environ.pop('CLAUDE_CONFIG_DIR', None)
            self.assertEqual([Path(root) for root in model_registry.session_project_roots()],
                             [self.config / 'projects', self.other / 'projects'])

    def test_explicit_transport_lookup_does_not_borrow_another_login(self):
        self.transcript(self.foreign_store)
        with mock.patch.object(model_registry, 'transport_projects_root', return_value=str(self.config / 'projects')):
            self.assertIsNone(model_registry.session_transcript(SESSION, 'paying-plan'))

    def test_explicit_dispatch_root_does_not_borrow_another_login(self):
        self.transcript(self.foreign_store)
        with mock.patch.dict(os.environ, {'CLAUDE_PROJECTS_ROOT': str(self.config / 'projects')}):
            self.assertIsNone(dispatch_table._transcript(SESSION))

    def test_parent_in_current_login_stops_subagent_fallback(self):
        self.transcript(self.main_store)
        foreign = self.foreign_store / SESSION / 'subagents' / 'agent-a12345678.jsonl'
        foreign.parent.mkdir(parents=True)
        foreign.write_text('{}\n', encoding='utf-8')
        self.assertIsNone(extract_subagent_tools.find_subagent_jsonl('a12345678', SESSION))
        self.assertIsNone(peer_message_guard.subagent_idle_seconds(SESSION, 'a12345678'))

    def test_peer_age_uses_current_login_without_reading_the_foreign_copy(self):
        current = self.transcript(self.main_store)
        foreign = self.transcript(self.foreign_store)
        with mock.patch.object(peer_message_guard, '_last_entry_time', side_effect=lambda p: 100 if p == current else self.fail(str(foreign))), mock.patch.object(peer_message_guard.time, 'time', return_value=110):
            self.assertEqual(peer_message_guard.idle_seconds(SESSION), 10)

    def test_prefix_lookups_find_the_foreign_session(self):
        expected = self.transcript(self.foreign_store)
        self.assertEqual(Path(session_end_check.transcript_for(SESSION[:8], str(self.worktree))), expected)
        self.assertEqual(session_digest.pick_transcript(self.worktree_store, SESSION[:8], None), expected)
        self.assertEqual(Path(orchestration_metrics.find_session_dir(SESSION[:8])), expected.with_suffix(''))

    def test_served_model_reads_the_foreign_parent_transcript(self):
        path = self.transcript(self.foreign_store)
        path.write_text(json.dumps({'type': 'assistant', 'message': {'model': 'served-model'}}) + '\n', encoding='utf-8')
        self.assertEqual(review_ledger.served_model(), 'served-model')

    def test_subagent_lookup_reaches_the_foreign_parent_store(self):
        path = self.foreign_store / SESSION / 'subagents' / 'agent-child.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text('{}\n', encoding='utf-8')
        self.assertEqual(extract_subagent_tools.find_subagent_jsonl('child', SESSION), path)


    def plant(self, store, session=SESSION):
        path = store / (session + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'type': 'assistant', 'message': {'model': 'served-model'}}) + '\n', encoding='utf-8')
        return path

    def found_or_none(self, call):
        try:
            return call()
        except SystemExit:
            return None

    def owner_callers(self):
        """name -> (lookup(session id) as a transcript Path or None, whether it accepts a unique id prefix)."""
        def path_if_file(path):
            return Path(path) if path and Path(path).is_file() else None

        def dir_of(sid):
            found = orchestration_metrics.find_session_dir(sid)
            return Path(found + '.jsonl') if found else None

        def peer(sid):
            return self.transcript_path_of(sid) if peer_message_guard.idle_seconds(sid) is not None else None

        def served(sid):
            with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': sid}):
                return self.transcript_path_of(sid) if review_ledger.served_model() else None

        worktree = str(self.worktree)
        return {
            'wake_digest': (lambda sid: path_if_file(wake_digest.transcript_path(sid)), False),
            'reap': (lambda sid: reap.session_transcript(sid), False),
            'dispatch_table': (lambda sid: dispatch_table._transcript(sid), False),
            'extract_subagent_tools': (lambda sid: extract_subagent_tools.find_session_jsonl(sid), False),
            'peer_message_guard': (peer, False),
            'review_ledger': (served, False),
            'session_store': (lambda sid: path_if_file(
                session_digest.session_store(worktree, sid) / (sid + '.jsonl')), False),
            'task_record': (lambda sid: path_if_file(task_record._session_transcript(sid)), False),
            'session_end_check': (lambda sid: path_if_file(session_end_check.transcript_for(sid, worktree)), True),
            'orchestration_metrics': (dir_of, True),
            'pick_transcript': (lambda sid: self.found_or_none(
                lambda: session_digest.pick_transcript(self.worktree_store, sid, None)), True),
        }

    def transcript_path_of(self, sid):
        return model_registry.find_session_transcript(sid) or Path('the owner found nothing')

    def assert_callers_follow_the_owner(self, session, prefix_session=None):
        exact = model_registry.find_session_transcript(session)
        for name, (lookup, takes_prefix) in self.owner_callers().items():
            with self.subTest(caller=name, session=session):
                found = lookup(session)
                self.assertEqual(None if found is None else Path(found), exact)
        if prefix_session:
            for name, (lookup, takes_prefix) in self.owner_callers().items():
                with self.subTest(caller=name, prefix=prefix_session):
                    want = model_registry.find_session_transcript(prefix_session, prefix=takes_prefix)
                    found = lookup(prefix_session)
                    self.assertEqual(None if found is None else Path(found), want)

    def test_every_caller_follows_the_owner_for_a_session_in_another_login(self):
        expected = self.plant(self.foreign_store)
        self.assertEqual(model_registry.find_session_transcript(SESSION), expected)
        self.assert_callers_follow_the_owner(SESSION, SESSION[:8])

    def test_every_caller_follows_the_owner_for_a_session_in_the_current_login(self):
        expected = self.plant(self.main_store)
        self.plant(self.foreign_store)
        self.assertEqual(model_registry.find_session_transcript(SESSION), expected)
        self.assert_callers_follow_the_owner(SESSION, SESSION[:8])

    def test_a_session_under_two_projects_of_one_login_is_ambiguous_for_every_caller(self):
        second = self.main / '.claude' / 'worktrees' / 'wt2'
        git(self.main, 'worktree', 'add', '-q', '-b', 'wt2', str(second))
        self.plant(self.main_store)
        self.plant(session_digest.projects_dir(str(second)))
        self.assertIsNone(model_registry.find_session_transcript(SESSION))
        self.assert_callers_follow_the_owner(SESSION, SESSION[:8])

    def test_ambiguity_in_the_first_login_holding_the_session_never_falls_through(self):
        self.plant(self.foreign_store)
        self.plant(self.other / 'projects' / self.worktree_store.name)
        self.assertIsNone(model_registry.find_session_transcript(SESSION))
        self.assert_callers_follow_the_owner(SESSION, SESSION[:8])

    def test_a_session_that_exists_nowhere_is_none_for_every_caller(self):
        self.plant(self.main_store)
        self.assert_callers_follow_the_owner('absent-session', 'absent')

    def test_a_wildcard_is_never_read_as_a_glob(self):
        self.plant(self.main_store)
        self.assertIsNone(model_registry.find_session_transcript('*'))
        self.assert_callers_follow_the_owner('*')

    def test_subagent_lookups_follow_the_owner_for_the_parent_session(self):
        agent = 'a12345678'
        for stores, expected in (((self.foreign_store,), True), ((self.main_store, self.worktree_store), False)):
            with self.subTest(stores=len(stores)):
                session = f'sub-{len(stores)}-{agent}'
                for store in stores:
                    self.plant(store, session)
                    child = store / session / 'subagents' / f'agent-{agent}.jsonl'
                    child.parent.mkdir(parents=True)
                    child.write_text('{}\n', encoding='utf-8')
                self.assertEqual(model_registry.find_session_transcript(session) is not None, expected)
                self.assertEqual(extract_subagent_tools.find_subagent_jsonl(agent, session) is not None, expected)
                self.assertEqual(peer_message_guard.subagent_idle_seconds(session, agent) is not None, expected)

    def test_a_pricing_lookup_still_raises_on_an_ambiguous_session(self):
        self.plant(self.main_store)
        self.plant(self.worktree_store)
        with mock.patch.object(model_registry, 'transport_projects_root', return_value=str(self.config / 'projects')):
            with self.assertRaisesRegex(model_registry.RegistryError, 'ambiguous transcript'):
                model_registry.session_transcript(SESSION, 'paying-plan')

    def test_a_session_directory_alone_counts_only_when_asked(self):
        (self.main_store / SESSION / 'workflows').mkdir(parents=True)
        self.assertIsNone(model_registry.find_session_transcript(SESSION))
        found = model_registry.find_session_transcript(SESSION, allow_dir=True)
        self.assertEqual(found.with_suffix(''), self.main_store / SESSION)
        self.assertEqual(Path(orchestration_metrics.find_session_dir(SESSION)), self.main_store / SESSION)


class RegistryDiscovery(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.registry = self.root / 'external_models.json'
        self.data = json.loads((TOOLS.parent / 'reference' / 'external_models.json').read_text(encoding='utf-8'))
        patch = mock.patch.dict(os.environ, {model_registry.ENV_OVERRIDE: str(self.registry)})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(model_registry._cache.clear)

    def test_discovery_deduplicates_logins_without_approving_payment_plans(self):
        same = str(self.root / 'shared-login')
        self.data['transports']['anthropic']['configDir'] = same
        self.data['transports']['anthropic-secondary']['configDir'] = same
        self.registry.write_text(json.dumps(self.data), encoding='utf-8')
        with self.assertRaisesRegex(model_registry.RegistryError, 'share configDir'):
            model_registry.load()
        logins = model_registry.login_dirs()
        self.assertEqual(logins.count(same.replace('\\', '/')), 1)
        self.assertIn(Path(same) / 'projects', [Path(root) for root in model_registry.session_project_roots()])
        with self.assertRaisesRegex(model_registry.RegistryError, 'share configDir'):
            model_registry.load()

    def test_discovery_reads_local_overlay_without_unrelated_model_validation(self):
        self.data['models'] = []
        self.registry.write_text(json.dumps(self.data), encoding='utf-8')
        foreign = str(self.root / 'overlay-login')
        self.registry.with_name('external_models.local.json').write_text(json.dumps({
            'transports': {'anthropic-secondary': {'configDir': foreign}}}), encoding='utf-8')
        with self.assertRaises(model_registry.RegistryError):
            model_registry.load()
        self.assertIn(foreign.replace('\\', '/'), model_registry.login_dirs())

    def test_discovery_does_not_hide_unreadable_registry(self):
        self.registry.write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(model_registry.RegistryError, 'unreadable'):
            model_registry.login_dirs()


if __name__ == '__main__':
    unittest.main()
