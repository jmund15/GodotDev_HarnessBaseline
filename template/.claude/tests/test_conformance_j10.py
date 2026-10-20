"""Recurrence validator/store/dashboard replay, using an exercised tool fixture."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
import analyze_eval_archive as analysis
import self_eval_archive_guard as guard
import self_eval_archive_store as store


from test_orchestration_metrics import MetricsConformance


def recurrence(changed=False):
    return dict(lessonId='lesson-acceptance', claimId='claim-acceptance', failureKey='reject-empty',
                priorLanding='abc123', behaviorChanged=changed,
                evidence='case accept-empty: before rejected, after rejected; same failure signature')


class RecurrenceConformance(unittest.TestCase):
    def test_malformed_recurrences_rejected(self):
        for bad in [None, {}, [None], [dict(recurrence(), behaviorChanged='false')],
                    [dict(recurrence(), evidence='')], [dict(recurrence(), priorLanding='')]]:
            with self.subTest(bad=bad):
                doc = dict(Self_Evaluate_Themes={'patterns': {}}, structured_entries=[
                    dict(id=1, outcome='clean', pattern=None, codified_recurrences=bad)])
                self.assertTrue(guard.check(json.dumps(doc)))

    def test_hook_advisory_and_analysis_reject_bad_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / '.claude'
            folder.mkdir()
            path = folder / 'self_evaluate_archive.json'
            path.write_text(json.dumps(dict(Self_Evaluate_Themes={'patterns': {}}, legacy_entries=[],
                structured_entries=[dict(id=1, outcome='clean', pattern=None, codified_recurrences=[{}])])), encoding='utf-8')
            result = guard.process(dict(tool_name='Write', tool_input={'file_path': str(path)}))
            self.assertIn('codified_recurrences', result['context'])
            with self.assertRaises(analysis.AnalysisError):
                analysis.compute(root)
            self.assertIsNone(guard.process(dict(tool_name='Read', tool_input={'file_path': str(path)})))

    def test_reach_only_is_not_a_changed_tool(self):
        row = dict(recurrence(False), repairKind='reach-only')
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / '.claude'
            folder.mkdir()
            (folder / 'self_evaluate_archive.json').write_text(json.dumps(dict(
                Self_Evaluate_Themes={'patterns': {}}, legacy_entries=[], structured_entries=[
                    dict(id=1, session_id='reach', outcome='clean', pattern=None, codified_recurrences=[row])])), encoding='utf-8')
            self.assertEqual(analysis.compute(root)['codified_recurrences'][0]['status'], 'reach-only repair recurred')

    def test_reproduction_round_trip_and_no_invented_matching(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / '.claude'
            folder.mkdir()
            archive = folder / 'self_evaluate_archive.json'
            legacy = dict(Self_Evaluate_Themes={'patterns': {'A': 'failure'}}, legacy_entries=[], structured_entries=[])
            archive.write_text(json.dumps(legacy), encoding='utf-8')
            original = archive.read_bytes()
            def entry(sid, **extra):
                return dict(session_id=sid, title=sid, date='2026-10-07', outcome='failure', pattern='A', **extra)
            store.upsert_entry(str(archive), entry('original', corrections=['reject-empty']))
            tool = 'def accepts(value):\n    return bool(value)\n'
            before = {}
            exec(tool, before)
            self.assertFalse(before['accepts'](''))
            text_landing = tool + '# Codified: empty input should be accepted.\n'
            after = {}
            exec(text_landing, after)
            self.assertFalse(after['accepts'](''))
            self.assertNotEqual(hashlib.sha256(tool.encode()).hexdigest(),
                                hashlib.sha256(text_landing.encode()).hexdigest())
            row = entry('repeated', codified_recurrences=[recurrence()])
            stored = store.upsert_entry(str(archive), row)
            revised = store.upsert_entry(str(archive), dict(stored, title='revised'))
            self.assertEqual(store.find_session(str(archive), 'repeated')['codified_recurrences'], [recurrence()])
            self.assertEqual(stored['id'], revised['id'])
            store.upsert_entry(str(archive), entry('different', corrections=['reject-large']))
            changed = {}
            exec('def accepts(value):\n    return True\n', changed)
            self.assertTrue(changed['accepts'](''))
            fixed = recurrence(True)
            fixed['evidence'] = 'case accept-empty: before rejected, after accepted; changed reproduction'
            store.upsert_entry(str(archive), entry('changed', codified_recurrences=[fixed]))
            unknown = recurrence(None)
            unknown['evidence'] = 'missing reproduction: prior runtime unavailable'
            store.upsert_entry(str(archive), entry('unknown', codified_recurrences=[unknown]))
            lines = []
            stats = analysis.compute(root, emit=lines.append)
            rows = stats['codified_recurrences']
            self.assertEqual(len(rows), 3)
            by_session = {r['session_id']: r for r in rows}
            self.assertEqual(by_session['repeated']['status'], 'text-only fix recurred')
            self.assertEqual(by_session['changed']['status'], 'changed behavior')
            self.assertEqual(by_session['unknown']['status'], 'unclassified')
            self.assertNotIn('different', by_session)
            self.assertTrue(any('text-only fix recurred' in line for line in lines))
            self.assertEqual(archive.read_bytes(), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
