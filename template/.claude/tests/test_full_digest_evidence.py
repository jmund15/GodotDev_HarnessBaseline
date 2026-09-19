"""Full closeout evidence must survive long sessions; use only synthetic rows."""
import importlib.util
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('summary_full', os.environ.get('SUMMARY_MODULE',
    ROOT / '.claude/hooks/_transcript_summary.py'))
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


class FullEvidenceTests(unittest.TestCase):
    def builder(self):
        return summary.TranscriptSummaryBuilder('own', 'own.jsonl', full_evidence=True)

    def feed(self, builder, kind, content, **fields):
        builder.process_line(json.dumps({'type': kind, 'message': {'content': content}, **fields}))

    def test_invalid_message_shape_records_source_row_error(self):
        builder = self.builder()
        builder.process_line('{malformed')
        self.feed(builder, 'user', 'Keep this request')
        builder.process_line(json.dumps({'type': 'user', 'message': 'Not a valid message object'}))
        result = builder.finalize()
        self.assertEqual(2, result['user_messages'][0]['index'])
        self.assertEqual([3], result['evidence_coverage']['processing_error_rows'])
        self.assertEqual(1, result['evidence_coverage']['malformed_rows'])

    def test_mixed_tool_result_and_user_text_preserves_both(self):
        builder = self.builder()
        self.feed(builder, 'user', [{'type': 'text', 'text': 'Keep this request'},
            {'type': 'tool_result', 'tool_use_id': 't', 'is_error': True, 'content': 'Fixture error'}])
        result = builder.finalize()
        self.assertEqual(['Keep this request'], [row['content'] for row in result['user_messages']])
        self.assertEqual(1, len(result['friction']))

    def test_backup_caps_file_and_boundary_metadata_with_omission_counts(self):
        builder = summary.TranscriptSummaryBuilder('own', 'own.jsonl')
        builder.files_modified = {str(index) + '.py': 1 for index in range(1000)}
        builder.compaction_markers = list(range(100))
        for index in range(100):
            builder.context_census.observe({'type': 'system', 'subtype': 'compact_boundary', 'uuid': str(index)})
        result = builder.finalize()
        self.assertLess(len(result['files_modified']), 1000)
        self.assertLess(len(result['files_modified_counts']), 1000)
        self.assertLess(len(result['context_census']['boundaries']), 100)
        self.assertLess(len(result['compactions']['timestamps']), 100)
        omitted = result['evidence_coverage']['omitted_collections']
        self.assertEqual(1000, len(result['files_modified']) + omitted['files_modified'])
        self.assertEqual(100, len(result['context_census']['boundaries']) + omitted['context_census.boundaries'])
        self.assertEqual(100, result['compactions']['count'])

    def test_more_than_400_full_prompts_are_retained(self):
        builder = self.builder()
        content = 'Complete requirement. ' * 200
        for index in range(405):
            self.feed(builder, 'user', str(index) + content)
        result = builder.finalize()
        self.assertEqual(405, len(result['user_messages']))
        self.assertEqual('0' + content, result['user_messages'][0]['content'])
        self.assertEqual(0, result['evidence_coverage']['omitted_user_messages'])

    def test_short_replies_and_no_argument_commands_are_kept(self):
        builder = self.builder()
        for content in ('yes', 'no', '<command-name>/session_end</command-name><command-args></command-args>'):
            self.feed(builder, 'user', content)
        self.assertEqual(['yes', 'no', '/session_end'],
                         [row['content'] for row in builder.finalize()['user_messages']])

    def test_user_markup_is_not_assumed_to_be_a_hook(self):
        builder = self.builder()
        self.feed(builder, 'user', '<goal>Keep this requirement.</goal>')
        self.feed(builder, 'user', '<system-reminder>Hook.</system-reminder>', isMeta=True)
        self.assertEqual(['<goal>Keep this requirement.</goal>'],
                         [row['content'] for row in builder.finalize()['user_messages']])

    def test_meta_command_wrappers_are_not_user_requests(self):
        builder = self.builder()
        self.feed(builder, 'user', '<command-name>/injected</command-name>'
                  '<command-args>Not a user request</command-args>', isMeta=True)
        self.assertEqual([], builder.finalize()['user_messages'])

    def test_more_than_200_friction_rows_are_retained(self):
        builder = self.builder()
        for index in range(205):
            self.feed(builder, 'assistant', [{'type': 'tool_use', 'id': str(index),
                'name': 'Bash', 'input': {'command': 'fixture'}}])
            self.feed(builder, 'user', [{'type': 'tool_result', 'tool_use_id': str(index),
                'is_error': True, 'content': 'Fixture failed. ' * 100}])
        result = builder.finalize()
        self.assertEqual(205, len(result['friction']))
        self.assertIn('Fixture failed. ' * 99, result['friction'][0]['error'])

    def test_backup_stays_bounded_but_discloses_omitted_rows(self):
        builder = summary.TranscriptSummaryBuilder('own', 'own.jsonl')
        for index in range(405):
            self.feed(builder, 'user', 'Requirement number ' + str(index))
        result = builder.finalize()
        self.assertEqual(400, len(result['user_messages']))
        self.assertEqual(5, result['evidence_coverage']['omitted_user_messages'])

    def test_all_unknown_result_bodies_do_not_become_zero(self):
        census = summary.ContextCensus()
        census.observe({'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'a', 'content': None}]}})
        row = census.finalize()['tool_results']['by_tool']['unknown']
        self.assertIsNone(row['text_utf8_bytes'])
        self.assertEqual(1, row['missing_text_results'])


if __name__ == '__main__':
    unittest.main()
