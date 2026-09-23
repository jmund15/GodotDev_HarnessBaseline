#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / '.claude/tools/session_digest.py'
spec = importlib.util.spec_from_file_location('digest_bounds_subject', TOOL)
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
    def test_default_is_bounded_handoff_without_losing_latest_state_or_retrieval(self):
        data = record()
        text = digest.render(data, Path('session.jsonl'), 'LATEST_OUTCOME_SENTINEL ' + 'done ' * 1000,
                             digest.SESSION)
        self.assertLessEqual(len(text.encode('utf-8')), digest.HANDOFF_MAX_BYTES)
        self.assertIn('LATEST_USER_SENTINEL', text)
        self.assertIn('LATEST_OUTCOME_SENTINEL', text)
        self.assertIn('session_digest_11111111.json', text)
        self.assertIn('--select', text)
        self.assertIn('100', text)
        self.assertIn('## Prompt timeline', text)

    def test_brief_has_its_smaller_global_bound(self):
        text = digest.render(record(300), Path('session.jsonl'), 'LATEST_OUTCOME_SENTINEL', digest.BRIEF)
        self.assertLessEqual(len(text.encode('utf-8')), digest.BRIEF_MAX_BYTES)
        self.assertIn('LATEST_USER_SENTINEL', text)
        self.assertIn('LATEST_OUTCOME_SENTINEL', text)
        self.assertIn('--select', text)

    def test_default_and_explicit_handoff_match_and_retain_priority_rows(self):
        data = record(300)
        data['user_messages'] = [
            {'index': 1, 'timestamp': '2026-09-12T12:00:00Z', 'signals': [],
             'content': 'FEATURE_ANCHOR ' + 'a' * 900},
            {'index': 2, 'timestamp': '2026-09-12T12:01:00Z', 'signals': ['correction'],
             'content': 'PRIORITY_CORRECTION ' + 'b' * 900},
            {'index': 3, 'timestamp': '2026-09-12T12:02:00Z', 'signals': ['question'],
             'content': '(answer) PRIORITY_ANSWER ' + 'c' * 900, 'recovered': 'question_answer'},
        ] + [
            {'index': i, 'timestamp': '2026-09-12T12:03:00Z', 'signals': [],
             'content': f'noise-{i} ' + 'n' * 900}
            for i in range(4, 300)
        ] + [
            {'index': 300, 'timestamp': '2026-09-12T12:04:00Z', 'signals': [],
             'content': 'FINAL_DIRECTIVE ' + 'd' * 900},
        ]
        task = '[task-record] drive-1 phase=build next=NEXT_ACTION\n- requirement: NEAR_CAP_TASK_RECORD'
        default = digest.render(data, Path('session.jsonl'), 'FINAL_STATUS', digest.SESSION,
                                task_record=task)
        explicit = digest.render(data, Path('session.jsonl'), 'FINAL_STATUS', digest.HANDOFF,
                                 task_record=task)
        self.assertEqual(default, explicit)
        self.assertLessEqual(len(default.encode('utf-8')), digest.HANDOFF_MAX_BYTES)
        for sentinel in ('FEATURE_ANCHOR', 'PRIORITY_CORRECTION', 'PRIORITY_ANSWER',
                         'FINAL_DIRECTIVE', 'NEAR_CAP_TASK_RECORD'):
            self.assertIn(sentinel, default)
        self.assertIn('omitted', default)
        self.assertIn('shown/total', default)

    def test_handoff_pressure_never_clips_mandatory_tail(self):
        data = record(300)
        data['user_messages'] = [
            {'index': i, 'timestamp': '2026-09-12T12:00:00Z', 'signals': ['correction'],
             'content': ('PRESSURE_ANCHOR ' if i == 0 else f'priority-{i} ') + 'x' * 900}
            for i in range(300)
        ]
        task = '[task-record] drive-1 phase=build next=NEXT_ACTION\n- requirement: MANDATORY_TASK_RECORD'
        text = digest.render(data, Path('session.jsonl'), 'MANDATORY_STATUS', digest.HANDOFF,
                             task_record=task)
        self.assertLessEqual(len(text.encode('utf-8')), digest.HANDOFF_MAX_BYTES)
        self.assertIn('MANDATORY_TASK_RECORD', text)
        self.assertIn('Full JSON:', text)
        self.assertIn('Omitted prompt IDs:', text)
        self.assertNotIn('shown/total: 300/300', text)

    def test_typed_overnight_wrapper_yields_to_real_task_anchor(self):
        data = record(3)
        data['user_messages'] = [
            {'index': 1, 'timestamp': '2026-09-12T12:00:00Z', 'signals': [],
             'content': '/overnight this'},
            {'index': 2, 'timestamp': '2026-09-12T12:01:00Z', 'signals': [],
             'content': 'REAL_TASK_AFTER_WRAPPER'},
        ]
        text = digest.render(data, Path('session.jsonl'), 'status', digest.BRIEF)
        anchor = text.split('## Task anchor', 1)[1].split('##', 1)[0]
        self.assertIn('REAL_TASK_AFTER_WRAPPER', anchor)
        self.assertNotIn('/overnight this', anchor)

    def test_handoff_friction_includes_the_assistant_next_move(self):
        data = record(1)
        data['friction'] = [{
            'index': 10, 'denied': False, 'tool': 'Bash', 'input': 'bad command',
            'error': 'BROKEN_PATH', 'response': 'NEXT_MOVE_USE_PROJECT_DIR',
        }]
        text = digest.render(data, Path('session.jsonl'), 'status', digest.HANDOFF)
        self.assertIn('BROKEN_PATH', text)
        self.assertIn('NEXT_MOVE_USE_PROJECT_DIR', text)

    def test_brief_has_task_anchor_latest_directive_status_and_task_record(self):
        data = record(12)
        data['user_messages'] = [
            {'index': 1, 'timestamp': '2026-09-12T11:59:00Z', 'signals': [],
             'content': '/effort max'},
            {'index': 2, 'timestamp': '2026-09-12T12:00:00Z', 'signals': [],
             'content': 'FIRST_TASK_ANCHOR'},
            {'index': 3, 'timestamp': '2026-09-12T12:01:00Z', 'signals': [],
             'content': '/overnight this', 'recovered': 'command_arguments'},
        ]
        task = '[task-record] drive-1 phase=plan next=NEXT_ACTION\n- requirement: TASK_RECORD_SENTINEL'
        text = digest.render(data, Path('session.jsonl'), 'STATUS_EVIDENCE', digest.BRIEF,
                             task_record=task)
        self.assertLessEqual(len(text.encode('utf-8')), digest.BRIEF_MAX_BYTES)
        anchor = text.split('## Task anchor', 1)[1].split('##', 1)[0]
        self.assertIn('FIRST_TASK_ANCHOR', anchor)
        self.assertNotIn('/effort max', anchor)
        self.assertIn('/overnight this', text)
        self.assertIn('STATUS_EVIDENCE', text)
        self.assertIn('TASK_RECORD_SENTINEL', text)
        self.assertIn('evidence', text.lower())

    def test_recovered_command_arguments_are_not_task_anchor_but_are_unattributed_in_handoff(self):
        data = record(4)
        data['user_messages'] = [
            {'index': 1, 'timestamp': '2026-09-12T12:00:00Z', 'signals': [],
             'content': '/overnight RECOVERED_COMMAND_GOAL', 'recovered': 'command_arguments'},
            {'index': 2, 'timestamp': '2026-09-12T12:01:00Z', 'signals': [],
             'content': 'REAL_FIRST_TASK_ANCHOR'},
            {'index': 3, 'timestamp': '2026-09-12T12:02:00Z', 'signals': [],
             'content': 'FINAL_OWNER_DIRECTIVE'},
        ]
        brief = digest.render(data, Path('session.jsonl'), 'status', digest.BRIEF)
        handoff = digest.render(data, Path('session.jsonl'), 'status', digest.HANDOFF)
        anchor = brief.split('## Task anchor', 1)[1].split('##', 1)[0]
        self.assertIn('REAL_FIRST_TASK_ANCHOR', anchor)
        self.assertNotIn('RECOVERED_COMMAND_GOAL', anchor)
        self.assertIn('/overnight RECOVERED_COMMAND_GOAL', handoff)
        self.assertIn('unattributed', handoff.lower())
        index = digest.build_evidence_index(data)
        recovered = next(row for row in index['prompts'] if row['id'] == 'U1')
        self.assertEqual('unattributed', recovered['attribution'])

    def test_digest_and_compact_share_owner_prompt_classifier(self):
        hook_spec = importlib.util.spec_from_file_location(
            'compact_for_classifier', ROOT / '.claude/hooks/compact_directive_anchor.py')
        compact = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(compact)
        cases = [
            {'content': '[Request interrupted by user]', 'signals': []},
            {'content': '/model fable', 'signals': []},
            {'content': '/effort', 'signals': []},
            {'content': '/effort xhigh', 'signals': []},
            {'content': '/feature run', 'signals': []},
            {'content': 'owner prose', 'signals': []},
        ]
        for message in cases:
            with self.subTest(message=message):
                self.assertEqual(digest.is_substantive_owner_prompt(message), compact.keep(message))

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


class FullExportCliTests(unittest.TestCase):
    def _write_transcript(self, project: Path) -> tuple[str, Path]:
        sid = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        transcript_dir = digest.projects_dir(str(project))
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript = transcript_dir / f'{sid}.jsonl'
        rows = [
            {'type': 'user', 'uuid': 'u-cli-1', 'timestamp': '2026-09-12T12:00:00Z',
             'message': {'role': 'user', 'content': 'CLI_PROMPT_SENTINEL build the feature'}},
            {'type': 'assistant', 'uuid': 'a-cli-1', 'timestamp': '2026-09-12T12:00:01Z',
             'message': {'role': 'assistant', 'content': [
                {'type': 'text', 'text': ('CLI_STATUS_SENTINEL the assistant status evidence carries '
                                               'distinctive alpha bravo charlie delta echo foxtrot golf hotel india '
                                               'juliet kilo lima mike november oscar papa quebec romeo sierra tango '
                                               'uniform victor whiskey xray yankee zulu and enough closing context '
                                               'to qualify as a substantive assistant handoff block for matching.')}]}},
        ]
        transcript.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        return sid, transcript

    def test_full_cli_writes_human_projection_and_stdout_receipt_only(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            sid, transcript = self._write_transcript(project)
            result = subprocess.run(
                [sys.executable, str(TOOL), '--project-dir', str(project), '--session', sid, '--full'],
                capture_output=True, text=True, encoding='utf-8', env=os.environ.copy())
            self.assertEqual(result.returncode, 0,
                             result.stderr + "\\ntranscript=" + str(transcript))
            self.assertLessEqual(len(result.stdout.encode('utf-8')), digest.FULL_RECEIPT_MAX_BYTES)
            self.assertIn('Full export:', result.stdout)
            self.assertNotIn('CLI_PROMPT_SENTINEL', result.stdout)
            export = project / 'logs' / f'session_digest_{sid[:8]}.full.md'
            self.assertTrue(export.exists())
            exported = export.read_text(encoding='utf-8')
            self.assertIn('CLI_PROMPT_SENTINEL', exported)
            self.assertIn('CLI_STATUS_SENTINEL', exported)
            self.assertIn('human-readable evidence', exported)
            self.assertTrue((project / 'logs' / f'session_digest_{sid[:8]}.json').exists())
            self.assertTrue((project / 'logs' / f'session_digest_{sid[:8]}.index.json').exists())

    def test_full_export_failure_has_no_success_receipt_or_partial_target(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            sid, _ = self._write_transcript(project)
            logs = project / 'logs'
            logs.mkdir()
            target = logs / f'session_digest_{sid[:8]}.full.md'
            target.mkdir()
            result = subprocess.run(
                [sys.executable, str(TOOL), '--project-dir', str(project), '--session', sid, '--full'],
                capture_output=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertIn('full export failed:', result.stderr)
            self.assertTrue(target.is_dir())
            self.assertEqual(list(logs.glob('.session-digest-*')), [])

    def test_brief_cli_caps_raw_stdout_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            sid, transcript = self._write_transcript(project)
            rows = [json.loads(line) for line in transcript.read_text(encoding='utf-8').splitlines()]
            rows[0]['message']['content'] += ' long-input' * 1000
            rows[1]['message']['content'][0]['text'] += ' long-status' * 1000
            transcript.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(TOOL), '--project-dir', str(project), '--session', sid, '--brief'],
                capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            self.assertLessEqual(len(result.stdout), digest.BRIEF_MAX_BYTES)

    def test_match_plus_full_keeps_stdout_receipt_only(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            sid, _ = self._write_transcript(project)
            match = ('CLI_STATUS_SENTINEL the assistant status evidence carries distinctive alpha bravo '
                     'charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar '
                     'papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu and enough '
                     'closing context to qualify as a substantive assistant handoff block for matching')
            result = subprocess.run(
                [sys.executable, str(TOOL), '--project-dir', str(project), '--match', match, '--full'],
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith('Full export:'), result.stdout)
            self.assertNotIn('MATCH ', result.stdout)
            self.assertIn('MATCH ', result.stderr)

    def test_transcript_presentation_flags_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            sid, _ = self._write_transcript(project)
            result = subprocess.run(
                [sys.executable, str(TOOL), '--project-dir', str(project), '--session', sid,
                 '--brief', '--handoff'], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertIn('not allowed with argument', result.stderr)

    def test_workflow_full_cannot_mix_with_transcript_full(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [sys.executable, str(TOOL), '--project-dir', temp, '--full', '--workflow-dir', temp,
                 '--workflow-kind', 'review', '--workflow-manifest'],
                capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertIn('mutually exclusive', result.stderr)


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
        owner_section = text.split('## Latest owner input/directive', 1)[1].split('##', 1)[0]
        self.assertIn('OWNER_REQUEST_SENTINEL', owner_section)
        self.assertNotIn('/delegate model-issued command', owner_section)
        self.assertIn('## Latest command request (unattributed)', text)
        self.assertIn('/delegate model-issued command', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
