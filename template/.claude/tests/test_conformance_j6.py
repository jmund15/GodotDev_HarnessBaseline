"""Executed dispatch/watch conformance cases; no provider calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.claude/tools'), str(ROOT / '.claude/hooks'), str(ROOT / '.claude/tests')]
import dispatch_table
import kill_guard
import watch_records as watch


class DispatchInterfaceTests(unittest.TestCase):
    def test_help_without_stdin(self):
        result = subprocess.run([sys.executable, str(ROOT / '.claude/hooks/dispatch_table.py'), '--help'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('usage:', result.stdout.lower())
        self.assertNotIn('JSONDecodeError', result.stderr)


class StopInvocationTests(unittest.TestCase):
    def test_mention_only_waiter_allowed_paid_launch_denied(self):
        commands = [("until test -s .claude/scratch/sidecar_fanout.py/out/a.record.json; do sleep 60; done", False),
                    ("printf '%s' '.claude/tools/sidecar_fanout.py'", False),
                    ("python3 .claude/tools/sidecar_fanout.py jobs.json", True),
                    ("bash -c 'python3 .claude/tools/sidecar_fanout.py jobs.json'", True)]
        for command, denied in commands:
            with self.subTest(command=command), patch.object(kill_guard, '_launch_of',
                    return_value=('Bash', {'command': command}, None)):
                verdict = kill_guard.taskstop_verdict(None, {'task_id': 'wait'}, str(ROOT))
                self.assertEqual(verdict is not None, denied, verdict)


class TerminalStatusTests(unittest.TestCase):
    def test_output_cap_is_incomplete(self):
        import sidecar_fanout as fanout
        with tempfile.TemporaryDirectory() as temp:
            record = Path(temp) / 'seat.record.json'
            record.write_text(json.dumps({'stopReason': 'output-cap'}))
            Path(str(record) + '.exit').write_text('1')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(fanout.print_status(temp), 0)
            self.assertIn('incomplete', output.getvalue())
            self.assertIn('output-cap', output.getvalue())
            self.assertNotIn('finished', output.getvalue())


class WakeDecisionTests(unittest.TestCase):
    def test_unseen_first_poll_wakes_and_acknowledged_event_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            ack = str(Path(temp) / 'state.json')
            group = {'u': {'records': [('seat', str(Path(temp) / 'seat.record.json'))], 'fanout_dir': None}}
            with patch.object(watch, 'record_state', return_value=('DONE', '1', 'exit')), \
                    patch.object(watch, 'CHECKIN_SECONDS', 0):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    watch.wake_loop(lambda: group, ack_path=ack)
                self.assertIn('STILL RUNNING 0', output.getvalue())
                self.assertNotIn('NONE', output.getvalue())
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    watch.wake_loop(lambda: group, ack_path=ack)
                self.assertIn('NONE', output.getvalue())
                self.assertNotIn('STILL RUNNING', output.getvalue())

    def test_acknowledgment_retains_every_current_event_not_a_history_tail(self):
        lines = [('end', ('end', f'record-{i}'), '', True) for i in range(2100)]
        with tempfile.TemporaryDirectory() as temp, patch.object(watch, '_wake', return_value=(lines, 0, set())):
            path = str(Path(temp) / 'state.json')
            with contextlib.redirect_stdout(io.StringIO()):
                watch.wake_loop(lambda: {}, ack_path=path)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                watch.wake_loop(lambda: {}, ack_path=path)
            self.assertEqual(output.getvalue().strip(), 'NONE')

    def test_fresh_stall_becoming_stale_wakes_without_marker_recreation(self):
        group = {'u': {'records': [('seat', 'seat.record.json')], 'fanout_dir': None}}
        with patch.object(watch, 'record_state', return_value=('WATCH', None, 'pid')), \
                patch.object(watch, 'stall_idle', return_value='1002'), \
                patch.object(watch, 'upstream_age', side_effect=[23, watch.UPSTREAM_LIVE_S + 1]), \
                patch.object(watch.time, 'sleep'):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                watch.wake_loop(lambda: group)
            self.assertIn('STILL RUNNING 1', output.getvalue())
            self.assertNotIn('CHECKIN', output.getvalue())

    def test_standing_stale_stall_wakes_but_fresh_traffic_does_not(self):
        group = {'u': {'records': [('seat', 'seat.record.json')], 'fanout_dir': None}}
        for age in (None, watch.UPSTREAM_LIVE_S + 1, 23):
            with self.subTest(age=age), patch.object(watch, 'record_state', return_value=('WATCH', None, 'pid')), \
                    patch.object(watch, 'stall_idle', return_value='1002'), \
                    patch.object(watch, 'upstream_age', return_value=age), \
                    patch.object(watch, 'CHECKIN_SECONDS', 0):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    watch.wake_loop(lambda: group)
                self.assertEqual('CHECKIN' in output.getvalue(), age == 23, output.getvalue())


class ExistingRepairTests(unittest.TestCase):
    def test_spilldir_launch_registration_is_discovered_by_session_watcher(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / 'spill'
            out.mkdir()
            jobs = root / 'jobs.json'
            jobs.write_text(json.dumps({'spillDir': out.as_posix(), 'jobs': [
                {'label': 'seat', 'model': 'sonnet', 'shape': 'review'}]}))
            record = out / 'seat.record.json'
            inventory = out / 'fanout-fixture.inventory.json'
            inventory.write_text(json.dumps({'parentSessionId': 'fixture',
                'fanoutProcess': watch.process_line(), 'jobs': [{'label': 'seat', 'recordPath': str(record)}]}))
            state = root / 'state.json'
            payload = {'tool_name': 'Bash', 'session_id': 'fixture', 'tool_use_id': 'launch',
                       'cwd': str(ROOT), 'tool_input': {'command': f'python3 .claude/tools/sidecar_fanout.py "{jobs.as_posix()}"'},
                       'tool_response': {}}
            with patch.object(dispatch_table._hook_state, 'state_path', return_value=str(state)), \
                    patch.object(dispatch_table, '_call_issued_at', return_value=time.time() - 1), \
                    patch.object(watch, 'finish_rows', return_value=[]):
                dispatch_table.launch(payload)
                pending = watch.pending_sidecars('fixture')
            self.assertEqual(len(pending), 1)
            self.assertEqual(Path(pending[0]['fanout_dir']).resolve(), out.resolve())
            self.assertEqual(Path(pending[0]['record']).resolve(), record.resolve())
            self.assertEqual(watch._wake(watch.units(pending))[1], 1)


if __name__ == '__main__':
    unittest.main()
