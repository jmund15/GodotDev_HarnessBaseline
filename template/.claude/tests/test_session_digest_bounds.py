#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('digest_bounds_subject', ROOT / '.claude/tools/session_digest.py')
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)


def record(size=100):
    return {
        'session_id': '11111111-2222-4333-8444-555555555555',
        'metadata': {'total_messages': size * 3, 'total_tool_calls': size, 'duration_seconds': 600},
        'compactions': {'count': 4},
        'user_messages': [{'index': i, 'timestamp': '2026-09-12T12:00:00Z', 'signals': [],
                           'content': ('LATEST_USER_SENTINEL ' if i == size - 1 else 'prompt ') + '猫' * 900}
                          for i in range(size)],
        'friction': [{'index': i + 10000, 'denied': False, 'tool': 'Bash', 'input': 'probe ' * 100,
                      'error': 'failure ' * 200 + ' ERROR_END_SENTINEL', 'response': 'next action ' * 100}
                     for i in range(size)],
        'files_modified_counts': {'src/' + str(i) + '/long_file_name.py': 1 for i in range(240)},
        'tool_census': {},
    }


class DigestBoundsTests(unittest.TestCase):
    def test_default_is_small_without_losing_latest_state_or_retrieval(self):
        data = record()
        text = digest.render(data, Path('session.jsonl'), 'LATEST_OUTCOME_SENTINEL ' + 'done ' * 1000,
                             digest.SESSION)
        self.assertLessEqual(len(text.encode('utf-8')), 2048)
        self.assertIn('LATEST_USER_SENTINEL', text)
        self.assertIn('LATEST_OUTCOME_SENTINEL', text)
        self.assertIn('session_digest_11111111.json', text)
        self.assertIn('--select', text)
        self.assertIn('100', text)

    def test_brief_has_the_same_global_bound(self):
        text = digest.render(record(300), Path('session.jsonl'), 'LATEST_OUTCOME_SENTINEL', digest.BRIEF)
        self.assertLessEqual(len(text.encode('utf-8')), 2048)
        self.assertIn('LATEST_USER_SENTINEL', text)
        self.assertIn('LATEST_OUTCOME_SENTINEL', text)
        self.assertIn('--select', text)

    def test_small_preview_caps_are_real_and_keep_latest(self):
        rows = record()['user_messages']
        for cap in (1, 2, 3, 4):
            with self.subTest(cap=cap):
                selected = digest._prompt_preview(rows, cap)
                self.assertLessEqual(len(selected), cap)
                self.assertIn(rows[-1], selected)

    def test_render_does_not_destroy_full_selected_evidence(self):
        data = record()
        original = json.dumps(data, ensure_ascii=False, sort_keys=True)
        digest.render(data, Path('session.jsonl'), 'outcome', digest.SESSION)
        self.assertEqual(json.dumps(data, ensure_ascii=False, sort_keys=True), original)
        selected = digest.select_evidence(data, ['U99', 'F10099'])
        serialized = json.dumps(selected, ensure_ascii=False)
        self.assertIn('猫' * 900, serialized)
        self.assertIn('ERROR_END_SENTINEL', serialized)

    def test_explicit_full_output_really_keeps_full_fields(self):
        data = record(2)
        text = digest.render(data, Path('session.jsonl'), 'outcome ' * 1000 + ' OUTCOME_END_SENTINEL', digest.FULL)
        self.assertIn('ERROR_END_SENTINEL', text)
        self.assertIn('OUTCOME_END_SENTINEL', text)
        self.assertIn('src/239/long_file_name.py', text)

    def test_files_page_names_every_file(self):
        index = digest.build_evidence_index(record())
        page = digest.evidence_page(index, 'files', 1, 50)
        self.assertEqual(page['total'], 240)
        self.assertEqual(page['pages'], 5)
        self.assertEqual(len(page['rows']), 50)
        names = [row['name'] for p in range(1, 6)
                 for row in digest.evidence_page(index, 'files', p, 50)['rows']]
        self.assertEqual(len(names), 240)
        self.assertEqual(set(names), set(record()['files_modified_counts']))
        self.assertEqual(len(names), len(set(names)))
        with self.assertRaises(ValueError):
            digest.evidence_page(index, 'bogus', 1, 20)

    def test_overview_never_claims_bytes_are_live_context(self):
        text = digest.render(record(), Path('session.jsonl'), 'outcome', digest.SESSION)
        self.assertNotIn('live context', text)
        self.assertIn('240', text)


def _row(**kw):
    base = {'type': 'user', 'timestamp': '2026-09-14T00:00:00Z'}
    base.update(kw)
    return json.dumps(base)


def _text_row(text, is_meta=True):
    return _row(isMeta=is_meta,
                message={'role': 'user', 'content': [{'type': 'text', 'text': text}]})


def _string_row(content):
    return _row(message={'role': 'user', 'content': content})


OVERNIGHT_BODY = ('# /overnight — Unattended run to completion\n\n'
                   'The user is leaving for hours. Decide, park what you cannot, finish the rest.\n\n'
                   "ARGUMENTS: finish the harness work while I'm away")
DELEGATE_BODY = ('# Delegate\n\nCanonical route for ordinary ad hoc delegation across the current '
                  'transport and registered sidecars.\n')


class CommandArgumentRecoveryTests(unittest.TestCase):
    """A `/overnight <goal>` turn is stored `isMeta: true` -- the same flag a static skill/command
    body re-injects with no user text at all -- so `_transcript_summary.py`'s isMeta filter drops
    real argument text as invisibly as it drops the body. Observed 2026-09-14, session
    `417af437-e526-4a44-bc5c-af551368213a`: the scan reported 6 prompts where the owner typed 7.
    """

    def _write_fixture(self):
        rows = [
            _string_row('do the actual thing'),                                  # plain prompt
            _text_row(OVERNIGHT_BODY),                                           # dropped today
            _string_row('<local-command-stdout>Set effort level to high'
                        '</local-command-stdout>'),                              # already excluded
            _text_row(DELEGATE_BODY),                                            # isMeta, no ARGUMENTS
            _string_row('<system-reminder>background note</system-reminder>'),   # runtime envelope
        ]
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / 'fixture.jsonl'
        path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        return path

    def test_a_command_turn_with_arguments_is_recovered_and_labeled(self):
        path = self._write_fixture()
        recovered = digest.recover_meta_command_prompts(path)
        self.assertEqual(len(recovered), 1,
                          'exactly the /overnight turn -- the Delegate body has no ARGUMENTS')
        self.assertTrue(recovered[0]['content'].startswith('/overnight '),
                         'labeled with the command name: %r' % recovered[0]['content'])
        self.assertIn("finish the harness work while I'm away", recovered[0]['content'],
                       'the goal text is preserved verbatim')
        self.assertNotIn('Canonical route for ordinary ad hoc delegation', recovered[0]['content'])

    def test_a_body_with_no_arguments_stays_excluded(self):
        path = self._write_fixture()
        recovered = digest.recover_meta_command_prompts(path)
        contents = [r['content'] for r in recovered]
        self.assertFalse(any('Delegate' in c for c in contents))

    def test_merge_does_not_duplicate_an_already_present_row(self):
        base = [{'index': 2, 'content': '/overnight already there'}]
        recovered = [{'index': 2, 'content': '/overnight already there'},
                     {'index': 9, 'content': '/overnight new one'}]
        merged = digest.merge_recovered_prompts(base, recovered)
        self.assertEqual(len(merged), 2)

    def test_end_to_end_builder_plus_recovery_yields_exactly_the_two_real_prompts(self):
        path = self._write_fixture()
        sys.path.insert(0, str(ROOT / '.claude' / 'hooks'))
        from _transcript_summary import TranscriptSummaryBuilder
        b = TranscriptSummaryBuilder('fixture', str(path), full_evidence=True)
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                b.process_line(line)
        d = b.finalize(backup_limits=False)
        merged = digest.merge_recovered_prompts(
            d.get('user_messages') or [], digest.recover_meta_command_prompts(path))
        self.assertEqual(len(merged), 2, 'plain prompt + the one real /overnight goal: %r' % merged)
        contents = [m['content'] for m in merged]
        self.assertIn('do the actual thing', contents)
        self.assertTrue(any(c.startswith('/overnight ') for c in contents), contents)
    def test_model_command_row_does_not_replace_latest_owner_request(self):
        data = record(2)
        data['user_messages'] = [
            {'index': 1, 'timestamp': '2026-09-14T00:00:00Z', 'signals': [],
             'content': 'OWNER_REQUEST_SENTINEL'},
            {'index': 2, 'timestamp': '2026-09-14T00:01:00Z', 'signals': [],
             'content': '/delegate model-issued command', 'recovered': 'command_arguments'},
        ]
        text = digest.render(data, Path('session.jsonl'), 'outcome', digest.SESSION)
        owner_section = text.split('## Latest user request', 1)[1].split('##', 1)[0]
        self.assertIn('OWNER_REQUEST_SENTINEL', owner_section)
        self.assertNotIn('/delegate model-issued command', owner_section)
        self.assertIn('## Latest command request (unattributed)', text)
        self.assertIn('/delegate model-issued command', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
