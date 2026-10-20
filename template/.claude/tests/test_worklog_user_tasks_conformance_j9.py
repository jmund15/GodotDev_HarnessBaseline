"""Executed J9 state/tool contracts; fixtures never expose User-Tasks bodies."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CLAUDE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(CLAUDE / d) for d in ('hooks', 'tools', 'scripts')]
import harness_edit_skill_reminder as reminder
import session_digest
import task_record
import aggregate_routing_audit as audit


class Contracts(unittest.TestCase):
    def test_S08_notes_independent_of_latch_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp), \
                patch.object(reminder, 'EDIT_NOTES', {'rules/noted.md': 'NOTE-secret', 'data.json': 'DATA-note', 'C:/repo/Game/data.json': 'GAME-note'}, create=True):
            from _hook_state import mark_skill_in_context
            mark_skill_in_context('j9notes', 'instruction_quality')
            event = {'tool_name': 'Edit', 'session_id': 'j9notes', 'tool_input': {'file_path': 'C:/repo/.claude/rules/other.md'}}
            reminder.process(event)
            for path, note in [('rules/noted.md', 'NOTE-secret'), ('data.json', 'DATA-note')]:
                event['tool_input']['file_path'] = 'C:/repo/.claude/' + path
                for _ in range(2):
                    self.assertIn(note, (reminder.process(event) or {}).get('context', ''))
            event['tool_input']['file_path'] = 'C:/repo/Game/data.json'
            self.assertIn('GAME-note', (reminder.process(event) or {}).get('context', ''))


    def test_S13_lookup_and_render_failures_visible(self):
        with patch.object(task_record, 'active', side_effect=OSError('missing')):
            self.assertIn('unavailable', session_digest.task_record_block('s', '.').lower())
        with patch.object(task_record, 'active', return_value={'task_id': 't'}), patch.object(task_record, 'render_block', side_effect=ValueError('broken')):
            self.assertIn('unavailable', session_digest.task_record_block('s', '.').lower())
        with patch.object(task_record, 'active', return_value={'task_id': 't'}), patch.object(task_record, 'render_block', return_value=''):
            self.assertIn('unavailable', session_digest.task_record_block('s', '.').lower())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, HARNESS_TASK_RECORD_DIR=tmp):
            (Path(tmp)/'broken.json').write_text('not json')
            self.assertIn('unavailable', session_digest.task_record_block('s', '.').lower())
        with patch.object(task_record, 'active', return_value=None):
            self.assertEqual('', session_digest.task_record_block('s', '.'))


    def test_S17_empty_refresh_and_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, stats = Path(tmp)/'input.jsonl', Path(tmp)/'stats.json'
            log.write_text(''); stats.write_text('{"total_entries":999}')
            with patch.object(audit, 'LOG_PATH', log), patch.object(audit, 'STATS_PATH', stats), patch.object(sys, 'argv', ['audit']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, audit.main())
                self.assertEqual(0, json.loads(stats.read_text())['total_entries'])
                log.write_text('not json\n')
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertNotEqual(0, audit.main())
                self.assertIn('unavailable', err.getvalue().lower())


    def test_V50_namespace_and_attribute_coverage(self):
        import test_suite_gate_coverage_guard as coverage
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); tests = root/'Tests/Logic'; tests.mkdir(parents=True)
            suite = tests/'Wrong.cs'
            with patch.object(coverage, 'REPO', root), patch.object(coverage, 'TESTS_ROOT', root/'Tests'), patch.object(coverage, 'declared_filters', return_value=set(coverage.GATED)):
                suite.write_text('namespace Tests.Uncovered; [GdUnit4.TestSuiteAttribute()] public class Wrong {}')
                self.assertTrue(coverage.find_violations())
                suite.write_text('namespace Tests.Integration.Unlisted; [TestSuite] public class Wrong {}')
                self.assertTrue(coverage.find_violations())
                suite.write_text('namespace Tests { namespace Logic { [TestSuite, Other] public class Good {} } }')
                self.assertEqual([], coverage.find_violations())
                suite.write_text('namespace Tests.Uncovered; // [TestSuite]\npublic class Plain { string s = "[TestSuite]"; }')
                self.assertEqual([], coverage.find_violations())
                suite.write_text('namespace Tests.Uncovered { [TestSuite] public abstract class Base {} }')
                self.assertEqual([], coverage.find_violations())


    def test_runner_checkout_roots_normalized_not_different_errors(self):
        import harness_tests as runner
        a = "FileNotFoundError: missing 'C:\\\\repo\\\\.claude\\\\scratch\\\\a.json'"
        b = "FileNotFoundError: missing 'D:\\\\tmp\\\\harness_head_x\\\\.claude\\\\scratch\\\\a.json'"
        self.assertTrue(runner._is_pre_existing(a, b, 'C:/repo', 'D:/tmp/harness_head_x'))
        self.assertFalse(runner._is_pre_existing(a, b.replace('a.json', 'b.json'), 'C:/repo', 'D:/tmp/harness_head_x'))


    def test_V21_known_red_bounded(self):
        import harness_tests as runner
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'KNOWN_RED_LOG', str(Path(tmp)/'reds.jsonl')):
            for i in range(401):
                runner._record_known_red('proof', str(i), 'red', 'red')
            rows = Path(runner.KNOWN_RED_LOG).read_text().splitlines()
            self.assertLessEqual(len(rows), 400)
            self.assertEqual('400', json.loads(rows[-1])['head'])


    def test_runner_observes_named_pass_skip_and_missing(self):
        import harness_tests as runner
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'test_case.py'
            path.write_text('print("ok named")\nprint("skipped skipped-case")\n')
            sink = []
            descriptors = [{'caseId': name, 'selector':'check-label', 'inputKey':'key'} for name in ['named','skipped-case','absent']]
            self.assertEqual('pass', runner.run_proof(str(path), behavior_cases=descriptors, receipt_sink=sink)[0])
            self.assertEqual(['pass','uncovered','uncovered'], [r['status'] for r in sink])


    def test_runner_real_binding_mutation_and_reuse(self):
        import harness_tests as runner
        from test_conformance_j1 import ClaimTracer, DOC, IMPL, PROOF, SENTENCE, write
        fixture = ClaimTracer(); fixture.setUp()
        try:
            write(str(fixture.repo), DOC, SENTENCE + '\n')
            fixture.rows(fixture.row(section='__module__'))
            root = str(fixture.repo)
            proof = str(fixture.repo/PROOF)
            with patch.object(runner, 'STAMP_PATH', str(fixture.repo/'.claude/logs/stamp.json')), patch.dict(os.environ, HARNESS_MACHINE_SLOTS='0'):
                self.assertIn(proof, runner.select_for(root, [IMPL]))
                def execute():
                    row = {}
                    result = runner._run_selected([proof], False, options=runner.RunOptions(
                        runner._pass_cache(root, [proof]), root=root, mode='fixture', row=row, paths=[IMPL]))
                    return result, row
                result, row = execute()
                self.assertEqual(0, result[1], row)
                self.assertEqual('pass', row['observations'][0]['status'])
                self.assertEqual('pass', row['behavior']['claims'][0]['status'])
                result, row = execute()
                self.assertEqual(1, row['reused'])
                out = str(fixture.repo/'behavior-result.json')
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, runner.main(['--repo', root, '--proofs-for', IMPL, '--behavior-report', out]))
                exported = json.loads(Path(out).read_text())
                self.assertEqual('pass', exported['claims'][0]['status'])
                self.assertEqual('pass', exported['observations'][0]['status'])
                write(root, IMPL, 'def emit():\n    if True: return "wrong"\n    return "accepted"\n')
                result, row = execute()
                self.assertGreater(result[1], 0)
                self.assertEqual('fail', row['observations'][0]['status'])
                self.assertEqual('fail', row['behavior']['claims'][0]['status'])
                write(root, IMPL, 'def emit():\n    return "accepted"\n')
                result, row = execute()
                self.assertEqual(0, result[1])
                # A zero-exit skipped case stays uncovered despite a previous pass.
                original = (fixture.repo/PROOF).read_text()
                write(root, PROOF, original.replace('    def test_accepted', '    @unittest.skip("unavailable")\n    def test_accepted'))
                result, row = execute()
                self.assertTrue(result[2])
                self.assertEqual('uncovered', row['observations'][0]['status'])
        finally:
            fixture.tearDown()


    def test_runner_real_preexisting_root_comparison(self):
        import harness_tests as runner
        from test_extract_numbers import mkrepo, git, write, _rm
        tmp = tempfile.mkdtemp(prefix='j9_root_comparison_')
        proof = '.claude/tests/test_missing_evidence.py'
        source = 'from pathlib import Path\n(Path(__file__).resolve().parents[1]/"scratch/evidence.json").read_text()\n'
        try:
            root = mkrepo(tmp, 'repo', {proof: source})
            base = git(root, 'rev-parse', 'HEAD').strip()
            with patch.object(runner, 'STAMP_PATH', str(Path(root)/'.claude/logs/stamp.json')), patch.dict(os.environ, HARNESS_MACHINE_SLOTS='0', HARNESS_NO_PASS_CACHE='1'), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, runner.run_scoped(root, [proof], base=base))
                self.assertIn('pre-existing red', output.getvalue())
                write(root, proof, source.replace('evidence.json', 'different.json'))
                self.assertEqual(1, runner.run_scoped(root, [proof], base=base))
                self.assertIn('fails differently', output.getvalue())
        finally:
            _rm(tmp)

    def test_runner_missing_checker_is_uncovered_not_a_crash(self):
        import harness_tests as runner
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, extract_numbers=None), patch.dict(os.environ, HARNESS_MACHINE_SLOTS='0'), patch.object(runner, 'STAMP_PATH', str(Path(tmp)/'stamp.json')):
            proof = Path(tmp)/'test_plain.py'; proof.write_text('print("ok plain")\n')
            row = {}
            result = runner._run_selected([str(proof)], False, options=runner.RunOptions(repo=tmp, mode='fixture', row=row))
            self.assertEqual((1, 0, []), result[:3])
            self.assertEqual('uncovered', row['behavior']['status'])
            out = str(Path(tmp)/'behavior.json')
            result = runner._run_selected([str(proof)], False, options=runner.RunOptions(repo=tmp, mode='fixture', behavior_out=out))
            self.assertTrue(result[2])
            self.assertEqual('uncovered', json.loads(Path(out).read_text())['status'])

    def test_runner_unclaimed_changed_unit_is_no_obligation(self):
        # The behavior-claim census does not run (owner cut list T2.11), so a changed unit no claim names
        # stays advisory; an affected claim's failure still fails the run.
        import harness_tests as runner
        import extract_numbers
        unit = {'path': '.claude/commands/x.md', 'hash': 'h1', 'status': 'uncovered'}
        report = {'status': 'uncovered', 'counts': {'uncovered': 1}, 'units': [unit], 'claims': [], 'uncovered': [unit],
                  'scope': {'affected': [], 'changedUnits': [{'path': unit['path'], 'hash': 'h1'}], 'initialDebt': [], 'errors': []}}
        failing = dict(report, claims=[{'claimId': 'c1', 'status': 'fail'}], scope=dict(report['scope'], affected=['c1']))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, HARNESS_MACHINE_SLOTS='0'), \
                patch.object(runner, 'STAMP_PATH', str(Path(tmp)/'stamp.json')):
            proof = Path(tmp)/'test_plain.py'; proof.write_text('print("ok plain")\n')
            for planted, want in ((report, 'pass'), (failing, 'fail')):
                row = {}
                with patch.object(extract_numbers, 'behavior_report', return_value=planted):
                    runner._run_selected([str(proof)], False, options=runner.RunOptions(repo=tmp, mode='fixture', row=row))
                self.assertEqual(want, row['behaviorAcceptance'])




if __name__ == '__main__':
    unittest.main(verbosity=2)
