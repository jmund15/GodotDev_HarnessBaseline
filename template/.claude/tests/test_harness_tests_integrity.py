#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('harness_integrity_subject', ROOT / '.claude/scripts/harness_tests.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class HarnessIntegrityTests(unittest.TestCase):
    def run_fixture(self, mutate=False, status='pass', verbose=False):
        with tempfile.TemporaryDirectory(prefix='harness-integrity-') as tmp:
            stamp = Path(tmp) / 'stamp.json'
            state = {'.claude/tools/subject.py': 'tested-version'}
            def proof(_path):
                if mutate:
                    state['.claude/tools/subject.py'] = 'untested-version'
                return status, 0.0, 'unavailable target' if status == 'cannot-run' else ''
            with patch.object(runner, 'EXCLUDED', {}), patch.object(runner, 'STAMP_PATH', str(stamp)), \
                 patch.object(runner, 'discover', return_value=['test_fixture.py']), \
                 patch.object(runner, 'tree_entries', side_effect=lambda _root: dict(state)), \
                 patch.object(runner, '_git', return_value='fixture-head'), \
                 patch.object(runner, 'run_proof', side_effect=proof), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                if verbose:
                    code = runner.run_all(tmp, verbose=True)
                else:
                    code = runner.run_all(tmp)
            return code, json.loads(stamp.read_text(encoding='utf-8')) if stamp.exists() else None, output.getvalue()

    def test_changed_source_is_not_certified(self):
        code, stamp, output = self.run_fixture(mutate=True)
        self.assertNotEqual(code, 0)
        self.assertIsNone(stamp)
        self.assertIn('changed', output.lower())

    def test_stable_source_is_certified(self):
        code, stamp, _ = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(stamp['files']['.claude/tools/subject.py'], 'tested-version')
        self.assertEqual(stamp['run'], 1)
        self.assertEqual(stamp['pass'], 1)

    def test_unavailable_proof_cannot_create_a_passing_stamp(self):
        code, stamp, output = self.run_fixture(status='cannot-run')
        self.assertNotEqual(code, 0)
        self.assertIsNone(stamp)
        self.assertIn('cannot-run', output.lower())

    def test_default_green_output_is_summary_not_one_line_per_proof(self):
        code, _, output = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertIn('1 run, 1 pass', output)
        self.assertNotIn('test_fixture.py', output)

    def test_verbose_keeps_per_proof_progress(self):
        code, _, output = self.run_fixture(verbose=True)
        self.assertEqual(code, 0)
        self.assertIn('test_fixture.py', output)

    def test_help_never_executes_tests(self):
        with patch.object(runner, 'run_all') as run, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exited:
                runner.main(['--help'])
        self.assertEqual(exited.exception.code, 0)
        run.assert_not_called()

    def test_only_measured_slow_proof_receives_longer_default_timeout(self):
        # The built-in table (not a project's local `adaptation.json` override, which never
        # publishes) is the only slow-proof entry every checkout shares.
        slow_name, slow_timeout = next(iter(runner._BUILTIN_PROOF_TIMEOUTS.items()))
        success = runner.subprocess.CompletedProcess([], 0, '', '')
        with patch.object(runner.subprocess, 'run', return_value=success) as process:
            runner.run_proof(slow_name)
            self.assertEqual(process.call_args.kwargs['timeout'], slow_timeout)
            runner.run_proof('test_ordinary.py')
            self.assertEqual(process.call_args.kwargs['timeout'], 120)
            runner.run_proof(slow_name, timeout=10)
            self.assertEqual(process.call_args.kwargs['timeout'], 10)

    def test_invalid_arguments_fail_before_test_execution(self):
        for args in (['--unknown'], ['--repo'], ['--hash', '--all']):
            with self.subTest(args=args), patch.object(runner, 'run_all') as run, \
                 patch.object(runner, 'tree_hash', return_value='hash'), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    runner.main(args)
                self.assertEqual(exited.exception.code, 2)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
