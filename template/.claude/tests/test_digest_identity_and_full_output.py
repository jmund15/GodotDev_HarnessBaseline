"""Exact digest identity and full-JSON integration over temporary transcripts only."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude/hooks'))
if os.environ.get('SUMMARY_MODULE'):
    spec = importlib.util.spec_from_file_location('_transcript_summary', os.environ['SUMMARY_MODULE'])
    summary = importlib.util.module_from_spec(spec)
    sys.modules['_transcript_summary'] = summary
    spec.loader.exec_module(summary)
spec = importlib.util.spec_from_file_location('digest_identity', os.environ.get('DIGEST_MODULE',
    ROOT / '.claude/tools/session_digest.py'))
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)


class DigestIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.transcripts = self.root / 'transcripts'
        self.transcripts.mkdir()

    def transcript(self, sid, prompt='A real user request.'):
        path = self.transcripts / (sid + '.jsonl')
        path.write_text(json.dumps({'type': 'user', 'message': {'content': prompt}}) + '\n')
        return path

    def test_missing_or_absent_active_identity_never_selects_peer(self):
        self.transcript('peer')
        for value in ('missing', ''):
            with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': value}):
                with self.assertRaises(SystemExit):
                    digest.pick_transcript(self.transcripts, None, None)

    def test_ambiguous_explicit_prefix_is_rejected(self):
        self.transcript('same-one')
        self.transcript('same-two')
        with self.assertRaises(SystemExit):
            digest.pick_transcript(self.transcripts, 'same', None)

    def test_exact_id_beats_longer_prefix_matches(self):
        expected = self.transcript('same')
        other = self.transcript('same-longer')
        os.utime(other, (2000000000, 2000000000))
        self.assertEqual(expected, digest.pick_transcript(self.transcripts, 'same', None))

    def test_previous_is_relative_to_the_active_session_not_newest_peer(self):
        previous = self.transcript('previous')
        active = self.transcript('active')
        peer = self.transcript('peer')
        for index, path in enumerate((previous, active, peer), 1):
            os.utime(path, (index * 100, index * 100))
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'active'}):
            self.assertEqual(previous, digest.pick_transcript(self.transcripts, None, None, previous=True))

    def test_listing_labels_only_the_exact_active_session(self):
        active = self.transcript('activeid')
        peer = self.transcript('peer-id')
        os.utime(active, (100, 100))
        os.utime(peer, (200, 200))
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'activeid'}):
            lines = digest.list_sessions(self.transcripts, 2).splitlines()
        self.assertNotIn('session', lines[0])
        self.assertIn('(active session)', lines[1])
        self.assertNotIn('(--previous)', '\n'.join(lines))

    def test_equal_match_scores_are_ambiguous(self):
        content = 'Distinctive alpha beta gamma delta epsilon zeta eta theta words in the closing report. ' * 4
        for sid in ('one', 'two'):
            path = self.transcript(sid)
            path.write_text(json.dumps({'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': content}]}}) + '\n')
            self.assertEqual(1, len(list(digest.assistant_blocks(path))))
        with self.assertRaisesRegex(SystemExit, 'ambiguous'):
            digest.match_transcript(self.transcripts, content)

    def test_tool_census_excludes_main_sidechains_but_counts_child_files(self):
        path = self.transcript('own')
        row = {'type': 'assistant', 'isSidechain': True, 'message': {'content': [
            {'type': 'tool_use', 'name': 'Read', 'id': 'child-call'}]}}
        path.write_text(json.dumps(row) + '\n')
        child_dir = self.transcripts / 'own/subagents'
        child_dir.mkdir(parents=True)
        (child_dir / 'agent-child.jsonl').write_text(json.dumps(row) + '\n')
        counts = digest.tool_census(path)
        self.assertEqual({}, counts['main'])
        self.assertEqual({'Read': 1}, counts['subagents'])

    def test_context_only_retains_all_boundaries_without_outcome_or_child_scans(self):
        transcript = self.transcripts / 'own.jsonl'
        transcript.write_text('\n'.join(json.dumps({'type': 'system', 'subtype': 'compact_boundary',
            'uuid': str(index)}) for index in range(50)))
        with mock.patch.object(digest, 'projects_dir', return_value=self.transcripts), \
             mock.patch.object(sys, 'argv', ['digest', '--session', 'own', '--context-only', '--json-only',
                                           '--project-dir', str(self.root)]), \
             mock.patch.object(digest, 'last_assistant_text', side_effect=AssertionError('outcome scan')), \
             mock.patch.object(digest, 'tool_census', side_effect=AssertionError('child scan')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            digest.main()
        value = json.loads(Path(output.getvalue().strip()).read_text())
        self.assertEqual(50, len(value['context_census']['boundaries']))
        self.assertNotIn('user_messages', value)

    def test_json_only_digest_has_every_prompt_without_printing_it(self):
        transcript = self.transcripts / 'own.jsonl'
        transcript.write_text('\n'.join(json.dumps({'type': 'user', 'message': {
            'content': 'Requirement number ' + str(index)}}) for index in range(405)))
        with mock.patch.object(digest, 'projects_dir', return_value=self.transcripts), \
             mock.patch.object(sys, 'argv', ['digest', '--session', 'own', '--json-only',
                                           '--project-dir', str(self.root)]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            digest.main()
        artifact = Path(output.getvalue().strip())
        self.assertTrue(artifact.is_file())
        value = json.loads(artifact.read_text())
        self.assertEqual(405, len(value['user_messages']))
        self.assertEqual(0, value['evidence_coverage']['omitted_user_messages'])


if __name__ == '__main__':
    unittest.main()
