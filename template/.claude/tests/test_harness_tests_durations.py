"""Recorded proof times are fastest whole passes, not the last machine load."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import harness_tests as runner


class Durations(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='proof_durations_')
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'durations.json'
        self.addCleanup(patch.stopall)
        patch.object(runner, 'DURATIONS_PATH', str(self.path)).start()

    def read(self):
        return json.loads(self.path.read_text())

    def test_loaded_pass_cannot_raise_the_baseline(self):
        runner._record_durations({'test_a.py': ('pass', 3.04, '')})
        runner._record_durations({'test_a.py': ('pass', 510.0, '')})
        self.assertEqual(self.read()['test_a.py'], 3.0)
        runner._record_durations({'test_a.py': ('pass', 2.2, '')})
        self.assertEqual(self.read()['test_a.py'], 2.2)

    def test_a_sub_rounding_pass_still_keeps_its_fastest_baseline(self):
        runner._record_durations({'test_a.py': ('pass', 0.01, '')})
        runner._record_durations({'test_a.py': ('pass', 2.0, '')})
        self.assertEqual(self.read()['test_a.py'], 0.0)

    def test_all_numeric_readers_see_the_same_fastest_time(self):
        runner._record_durations({'test_a.py': ('pass', 3.0, '')})
        runner._record_durations({'test_a.py': ('pass', 510.0, '')})
        with patch.object(runner, 'select_for', return_value=['test_a.py']), patch.object(runner, '_jobs', return_value=1):
            self.assertIn('~3s at recorded times', runner._selection_estimate(['test_a.py']))

    def test_timeout_reads_the_last_whole_pass_which_carries_the_load_the_proof_meets(self):
        runner._record_durations({'test_a.py': ('pass', 40.0, '')})
        runner._record_durations({'test_a.py': ('pass', 150.0, '')})
        self.assertEqual(self.read()['test_a.py'], 40.0)
        self.assertEqual(runner._timeout_for('test_a.py', self.read()), 450.0)
        runner._record_durations({'test_a.py': ('fail', 450.0, 'TIMEOUT after 450s')})
        self.assertEqual(runner._timeout_for('test_a.py', self.read()), 450.0)
        runner._record_durations({'test_a.py': ('pass', 30.0, '')})
        self.assertEqual(runner._timeout_for('test_a.py', self.read()), runner._TIMEOUT_SEC)
        self.assertEqual(runner._timeout_for('test_b.py', {'test_b.py': 50.0}), 150.0)
        self.assertEqual(runner._timeout_for('test_b.py', {'test_b.py': 50.0, '__last_passes__': 'torn'}), 150.0)

    def test_failure_forgets_retry_eligibility_but_timeout_keeps_it(self):
        runner._record_durations({'test_a.py': ('pass', 3.0, '')})
        runner._record_durations({'test_a.py': ('fail', 120.0, 'TIMEOUT after 120s')})
        self.assertEqual(self.read()['test_a.py'], 3.0)
        runner._record_durations({'test_a.py': ('fail', 1.0, 'FAIL assertion')})
        self.assertNotIn('test_a.py', self.read())
        runner._record_durations({'test_a.py': ('pass', 9.0, '')})
        self.assertEqual(self.read()['test_a.py'], 9.0)

    def test_partial_passes_never_set_history_floor_or_load(self):
        runs = [{'ts': 1, 'proofs': [
            {'proof': 'test_a.py', 'status': 'pass', 'run_s': 0.1, 'cases_reused': 10},
            {'proof': 'test_b.py', 'status': 'pass', 'run_s': 0.1, 'reused': True}]},
            {'ts': 2, 'proofs': [{'proof': 'test_a.py', 'status': 'pass', 'run_s': 30.0}]}]
        self.assertEqual(runner._fastest_passes({'ts': 3}, runs), {'test_a.py': 30.0})

    def test_partial_pass_is_not_in_the_current_load_or_wall_floor(self):
        history = [{'ts': 1, 'proofs': [
            {'proof': 'test_a.py', 'status': 'pass', 'run_s': 30.0},
            {'proof': 'test_b.py', 'status': 'pass', 'run_s': 10.0}]}]
        row = {'ts': 2, 'width': 1, 'proofs': [
            {'proof': 'test_a.py', 'status': 'pass', 'run_s': 0.1, 'cases_reused': 10},
            {'proof': 'test_b.py', 'status': 'pass', 'run_s': 20.0}]}
        self.assertIn('load x2.0', runner._time_line(row, history))
        self.assertEqual(runner._wall_floor(row, history)[0], 10.0)

    def test_a_deadline_cut_proof_is_skipped_and_leaves_no_trace(self):
        """A proof the run's deadline cut short is skipped: it reaches neither the flaky nor the red path and
        records no duration, so its timeout never stretches on a run that did not measure it."""
        runner._record_durations({'test_a.py': ('pass', 3.0, '')})
        runner._record_durations({'test_a.py': ('skipped', 9.0, 'not run: HARNESS_DEADLINE passed')})
        self.assertEqual(self.read()['test_a.py'], 3.0)
        self.assertEqual(self.read()['__last_passes__']['test_a.py']['run_s'], 3.0)

    def test_run_history_keeps_actual_load_but_partial_and_reused_passes_do_not_train(self):
        rows = {}
        options = runner.RunOptions(mode='for', row=rows)
        runner._record_durations({'test_a.py': ('pass', 3.0, '')})
        with patch.object(runner, '_behavior_plan', return_value={}), \
                patch.object(runner, '_launch', return_value=('pass', 510.0, '')), \
                patch.object(runner, '_record_run'), patch.object(runner, '_machine_slots', return_value=0), \
                patch.object(runner, '_jobs', return_value=1), \
                contextlib.redirect_stdout(io.StringIO()):
            runner._run_selected(['test_a.py'], False, options=options)
        self.assertEqual(rows['proofs'][0]['run_s'], 510.0)
        self.assertEqual(self.read()['test_a.py'], 3.0)


if __name__ == '__main__':
    unittest.main()
