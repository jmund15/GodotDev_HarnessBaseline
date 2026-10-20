#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import tempfile
import threading
import time
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
            def proof(_path, _timeout=None):
                if mutate:
                    state['.claude/tools/subject.py'] = 'untested-version'
                return status, 0.0, 'unavailable target' if status == 'cannot-run' else ''
            with patch.object(runner, 'EXCLUDED', {}), patch.object(runner, 'STAMP_PATH', str(stamp)), \
                 patch.object(runner, 'discover', return_value=['test_fixture.py']), \
                 patch.object(runner, 'tree_entries', side_effect=lambda _root: dict(state)), \
                 patch.object(runner, '_git', return_value='fixture-head'), \
                 patch.object(runner, 'run_proof', side_effect=proof), \
                 patch.dict(runner.os.environ, {'HARNESS_SLOT_DIR': str(Path(tmp) / 'slots')}), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                if verbose:
                    code = runner.run_all(tmp, verbose=True)
                else:
                    code = runner.run_all(tmp)
            return code, json.loads(stamp.read_text(encoding='utf-8')) if stamp.exists() else None, output.getvalue()

    def test_planted_runners_use_private_slot_directories(self):
        inherited = str(ROOT / '.claude/scratch/unrelated-slots')
        seen = []
        def run(root, **_kwargs):
            folder = Path(runner._slot_dir())
            seen.append(folder)
            self.assertNotEqual(str(folder), inherited)
            self.assertEqual(folder.parent, Path(root))
            return 0
        with patch.dict(runner.os.environ, {'HARNESS_SLOT_DIR': inherited}), \
                patch.object(runner, 'run_all', side_effect=run):
            self.run_fixture()
            self.run_pool(lambda *_args: ('pass', 0.0, ''), ['test_fixture.py'])
            self.assertEqual(runner.os.environ.get('HARNESS_SLOT_DIR'), inherited)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])

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
        with patch.object(runner, '_run_child', return_value=(0, '')) as process, \
                patch.dict(runner._PROOF_TIMEOUTS, runner._BUILTIN_PROOF_TIMEOUTS, clear=True):
            runner.run_proof(slow_name)
            self.assertEqual(process.call_args.args[2], slow_timeout)
            runner.run_proof('test_ordinary.py')
            self.assertEqual(process.call_args.args[2], 120)
            runner.run_proof(slow_name, timeout=10)
            self.assertEqual(process.call_args.args[2], 10)

    def run_pool(self, proof, names, serial=(), jobs=2, durations=None, with_timing=False, flaky_seed=None):
        with tempfile.TemporaryDirectory(prefix='harness-pool-') as tmp:
            stamp, flaky, timing = Path(tmp) / 'stamp.json', Path(tmp) / 'flaky.jsonl', Path(tmp) / 'd.json'
            if durations:
                timing.write_text(json.dumps(durations), encoding='utf-8')
            if flaky_seed:
                flaky.write_text(''.join(json.dumps(r) + '\n' for r in flaky_seed), encoding='utf-8')
            with patch.object(runner, 'EXCLUDED', {}), patch.object(runner, 'HEAVY', {}), \
                 patch.object(runner, 'SERIAL', {n: 'fixture' for n in serial}), \
                 patch.object(runner, 'STAMP_PATH', str(stamp)), patch.object(runner, 'FLAKY_LOG', str(flaky)), \
                 patch.object(runner, 'DURATIONS_PATH', str(timing)), \
                 patch.object(runner, 'discover', return_value=list(names)), \
                 patch.object(runner, 'tree_entries', return_value={'.claude/tools/subject.py': 'v'}), \
                 patch.object(runner, '_git', return_value='fixture-head'), \
                 patch.object(runner, 'run_proof', side_effect=proof), \
                 patch.dict(runner.os.environ, {'HARNESS_TEST_JOBS': str(jobs),
                                               'HARNESS_SLOT_DIR': str(Path(tmp) / 'slots')}), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                code = runner.run_all(tmp)
            flaky_rows = [json.loads(l) for l in flaky.read_text(encoding='utf-8').splitlines()] if flaky.exists() else []
            if with_timing:
                recorded = json.loads(timing.read_text(encoding='utf-8')) if timing.exists() else {}
                return code, stamp.exists(), output.getvalue(), flaky_rows, recorded
            return code, stamp.exists(), output.getvalue(), flaky_rows

    def test_proofs_run_concurrently(self):
        barrier = threading.Barrier(2, timeout=5)
        inherited = str(ROOT / '.claude/scratch/unrelated-slots')
        seen = []
        def proof(_path, _timeout=None):
            seen.append(Path(runner._slot_dir()))
            barrier.wait()
            return 'pass', 0.0, ''
        with patch.dict(runner.os.environ, {'HARNESS_SLOT_DIR': inherited}):
            code, stamped, output, _ = self.run_pool(proof, ['test_a.py', 'test_b.py'])
            self.assertEqual(runner.os.environ['HARNESS_SLOT_DIR'], inherited)
        self.assertEqual(code, 0, output)
        self.assertTrue(stamped)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])
        self.assertNotEqual(str(seen[0]), inherited)

    def test_serial_proof_never_overlaps_another(self):
        live, peak, lock = [0], {}, threading.Lock()
        def proof(path, _timeout=None):
            with lock:
                live[0] += 1
                peak[path] = live[0]
            time.sleep(0.05)
            with lock:
                live[0] -= 1
            return 'pass', 0.0, ''
        code, _, output, _ = self.run_pool(proof, ['test_a.py', 'test_b.py', 'test_c.py', 'test_lone.py'],
                                           serial=['test_lone.py'], jobs=3)
        self.assertEqual(code, 0, output)
        self.assertEqual(peak['test_lone.py'], 1)

    def test_a_failure_that_passes_alone_is_flaky_not_red(self):
        calls = {}
        def proof(path, _timeout=None):
            calls[path] = calls.get(path, 0) + 1
            if path == 'test_flaky.py' and calls[path] == 1:
                return 'fail', 0.0, 'TIMEOUT after 120s'
            return 'pass', 0.0, ''
        code, stamped, output, flaky_rows = self.run_pool(proof, ['test_ok.py', 'test_flaky.py'],
                                                          durations={'test_flaky.py': 3.0})
        self.assertEqual(code, 0, output)
        self.assertTrue(stamped)
        self.assertIn('FLAKY', output)
        self.assertIn('test_flaky.py', output)
        self.assertEqual([r['proof'] for r in flaky_rows], ['test_flaky.py'])
        self.assertIn('TIMEOUT', flaky_rows[0]['first'])

    def test_a_cold_timeout_that_passes_on_retry_is_not_flaky(self):
        calls = {}
        def proof(path, _timeout=None):
            calls[path] = calls.get(path, 0) + 1
            if path == 'test_slow.py' and calls[path] == 1:
                return 'fail', 0.0, 'TIMEOUT after 120s'
            return 'pass', 0.0, ''
        code, stamped, output, flaky_rows = self.run_pool(proof, ['test_ok.py', 'test_slow.py'])
        self.assertEqual(code, 0, output)
        self.assertTrue(stamped)
        self.assertEqual(calls['test_slow.py'], 2)
        self.assertNotIn('FLAKY', output)
        self.assertEqual(flaky_rows, [])

    def test_a_failure_that_repeats_alone_is_red_and_loses_its_pass_record(self):
        calls = {}
        def proof(path, _timeout=None):
            calls[path] = calls.get(path, 0) + 1
            return ('fail', 0.0, 'assertion') if path == 'test_red.py' else ('pass', 0.0, '')
        code, stamped, output, flaky_rows, timing = self.run_pool(
            proof, ['test_ok.py', 'test_red.py'], durations={'test_red.py': 3.0}, with_timing=True)
        self.assertEqual(code, 1)
        self.assertFalse(stamped)
        self.assertIn('test_red.py', output)
        self.assertEqual(flaky_rows, [])
        self.assertEqual(calls['test_red.py'], 2)
        self.assertNotIn('test_red.py', timing)

    def test_flaky_log_is_capped(self):
        with tempfile.TemporaryDirectory(prefix='harness-flaky-cap-') as tmp:
            log = Path(tmp) / 'flaky.jsonl'
            log.write_text(''.join(json.dumps({'ts': 0, 'proof': 'p%d' % i}) + '\n' for i in range(400)),
                           encoding='utf-8')
            with patch.object(runner, 'FLAKY_LOG', str(log)):
                runner._record_flaky('test_new.py', 'TIMEOUT', 1.0)
            rows = [json.loads(l) for l in log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 200)
        self.assertEqual(rows[-1]['proof'], 'test_new.py')

    def test_flaky_row_names_the_failing_cases(self):
        detail = '\n'.join([
            '  PASS  first case', '  FAIL  E4 refuses before the probe', '        gate=12 probe=9',
            'Traceback (most recent call last):', 'AssertionError: boom', 'suite: 18 passed, 1 failed', ''])
        with tempfile.TemporaryDirectory(prefix='harness-flaky-cases-') as tmp:
            log = Path(tmp) / 'flaky.jsonl'
            with patch.object(runner, 'FLAKY_LOG', str(log)):
                runner._record_flaky('test_shaky.sh', detail, 1.0)
            row = json.loads(log.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(row['first'], 'suite: 18 passed, 1 failed')
        self.assertEqual(row.get('failures'), ['FAIL  E4 refuses before the probe', 'AssertionError: boom'])

    def test_full_battery_names_proofs_with_flaky_history(self):
        def proof(_path, _timeout=None):
            return 'pass', 0.0, ''
        now = time.time()
        seed = [{'ts': ts, 'proof': name} for ts, name in
                ((now - 60, 'test_shaky.py'), (now - 120, 'test_shaky.py'), (now - 90 * 86400, 'test_old.py'))]
        code, _, output, _ = self.run_pool(proof, ['test_a.py'], flaky_seed=seed)
        self.assertEqual(code, 0, output)
        self.assertIn('test_shaky.py x2', output)
        self.assertNotIn('test_old.py', output)

    def test_an_assertion_failure_with_no_recorded_pass_is_not_retried(self):
        calls = {}
        def proof(path, _timeout=None):
            calls[path] = calls.get(path, 0) + 1
            return ('fail', 0.0, 'FAIL: case') if path == 'test_known_red.py' else ('pass', 0.0, '')
        code, _, _, _ = self.run_pool(proof, ['test_ok.py', 'test_known_red.py'])
        self.assertEqual(code, 1)
        self.assertEqual(calls['test_known_red.py'], 1)

    def test_a_timeout_with_no_recorded_pass_is_retried_once_with_a_longer_timeout(self):
        calls = {}
        def proof(path, timeout=None):
            calls.setdefault(path, []).append(timeout)
            return ('fail', 0.0, 'TIMEOUT after 120s') if path == 'test_slow.py' else ('pass', 0.0, '')
        code, _, _, _ = self.run_pool(proof, ['test_ok.py', 'test_slow.py'])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls['test_slow.py']), 2)
        self.assertEqual(calls['test_slow.py'][0], runner._TIMEOUT_SEC)
        self.assertGreater(calls['test_slow.py'][1], calls['test_slow.py'][0])

    def test_slowest_recorded_proof_starts_first(self):
        order = []
        def proof(path, _timeout=None):
            order.append(path)
            return 'pass', 0.0, ''
        self.run_pool(proof, ['test_a.py', 'test_slow.py', 'test_b.py'], jobs=1,
                      durations={'test_slow.py': 90.0, 'test_a.py': 1.0})
        self.assertEqual(order[0], 'test_slow.py')

    def test_the_process_tree_has_one_home(self):
        import _bounded_run
        self.assertIs(runner.ProcessTree, _bounded_run.ProcessTree)
        self.assertFalse(hasattr(runner, '_ProcessTree'))

    def test_timeout_returns_even_when_a_grandchild_holds_the_pipe(self):
        with tempfile.TemporaryDirectory(prefix='harness-timeout-') as tmp:
            proof = Path(tmp) / 'test_orphan.py'
            proof.write_text(
                'import subprocess, sys, time\n'
                'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(40)"])\n'
                'time.sleep(40)\n', encoding='utf-8')
            started = time.time()
            status, _, detail = runner.run_proof(str(proof), timeout=2)
            took = time.time() - started
        self.assertEqual(status, 'fail')
        self.assertIn('TIMEOUT', detail)
        self.assertLess(took, 15)

    def test_a_child_left_running_after_a_green_exit_is_ended(self):
        with tempfile.TemporaryDirectory(prefix='harness-leftover-') as tmp:
            marker = Path(tmp) / 'still_alive.txt'
            proof = Path(tmp) / 'test_leftover.py'
            proof.write_text(
                'import subprocess, sys\n'
                'subprocess.Popen([sys.executable, "-c", "import time, pathlib, sys; time.sleep(4); '
                'pathlib.Path(sys.argv[1]).write_text(\'x\')", %r])\n' % str(marker), encoding='utf-8')
            status, _, _ = runner.run_proof(str(proof), timeout=30)
            time.sleep(6)
            survived = marker.exists()
        self.assertEqual(status, 'pass')
        self.assertFalse(survived, 'a process the proof left running outlived it')

    def test_proofs_get_a_narrower_case_pool_than_standalone(self):
        with patch.object(runner, '_run_child', return_value=(0, '')) as process, \
                patch.dict(runner.os.environ, {}, clear=False):
            runner.os.environ.pop('HARNESS_CASE_JOBS', None)
            runner.run_proof('test_ordinary.py')
            self.assertEqual(process.call_args.args[1].get('HARNESS_CASE_JOBS'), '2')
            runner.os.environ['HARNESS_CASE_JOBS'] = '5'
            runner.run_proof('test_ordinary.py')
            self.assertEqual(process.call_args.args[1].get('HARNESS_CASE_JOBS'), '5')

    def test_underscore_helpers_are_not_proofs(self):
        with tempfile.TemporaryDirectory(prefix='harness-discover-') as tmp:
            for name in ('_helper_launcher.sh', '_fixture.ps1', 'test_real.sh', 'real_test.py'):
                (Path(tmp) / name).write_text('', encoding='utf-8')
            found = sorted(Path(p).name for p in runner.discover(tmp))
        self.assertEqual(found, ['real_test.py', 'test_real.sh'])

    def test_invalid_arguments_fail_before_test_execution(self):
        for args in (['--unknown'], ['--repo'], ['--tiers', '--all'], ['--hash']):
            with self.subTest(args=args), patch.object(runner, 'run_all') as run, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exited:
                    runner.main(args)
                self.assertEqual(exited.exception.code, 2)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
