"""Discriminating review regressions using temporary synthetic transcripts only."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude/hooks'))
import _transcript_summary as summary
spec = importlib.util.spec_from_file_location('review_digest', ROOT / '.claude/tools/session_digest.py')
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)


class DigestFinalReviewTests(unittest.TestCase):
    def test_valid_json_spacing_survives_prompt_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'s.jsonl'
            for separator in (':', ': ', ' : ', '\t:\t'):
                with self.subTest(separator=separator):
                    path.write_text(json.dumps({'type':'user','message':{'content':'keep this exact prompt'}},separators=(',',separator)),encoding='utf-8')
                    self.assertEqual('keep this exact prompt',digest.last_prompt(path))

    def test_non_object_json_rows_do_not_abort_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'s.jsonl'
            path.write_text('[]\nnull\n"string"\n'+json.dumps({'type':'user','message':{'content':'kept'}}),encoding='utf-8')
            self.assertEqual('kept',digest.last_prompt(path))

    def test_malformed_message_preserves_top_level_user_text(self):
        for message in (None, 'malformed', ['malformed'], 4):
            with self.subTest(message=message):
                b=summary.TranscriptSummaryBuilder('fixture','unused',full_evidence=True)
                b.process_line(json.dumps({'type':'user','message':message,'content':'Do not drop this correction'}))
                self.assertEqual('Do not drop this correction',b.last_user_request)

    def test_selection_recovers_malformed_message_top_level_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'s.jsonl'
            for message in (None,'malformed',['malformed'],4):
                with self.subTest(message=message):
                    path.write_text(json.dumps({'type':'user','message':message,'content':'keep this prompt'}),encoding='utf-8')
                    self.assertEqual('keep this prompt',digest.last_prompt(path))

    def test_assistant_blocks_accept_whitespace_and_recover_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'s.jsonl'
            text='long response ' * 30
            path.write_text(json.dumps({'type':'assistant','message':'bad','content':[{'type':'text','text':text}]},separators=(',', ' : ')),encoding='utf-8')
            self.assertEqual(text.strip(),digest.last_assistant_text(path))
            self.assertTrue(list(digest.assistant_blocks(path)))

    def test_sidechain_selection_remains_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'s.jsonl'
            path.write_text(json.dumps({'type':'user','isSidechain':True,'message':{'content':'peer'}}),encoding='utf-8')
            self.assertEqual('',digest.last_prompt(path))


if __name__=='__main__':
    unittest.main()
