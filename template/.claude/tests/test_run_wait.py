"""run_wait: one wait on a runner's log ends at that runner's own terminal line, never an inner runner's, and tells a
run still going (exit 3) from one that ended without a verdict (exit 2). The mechanism runs on planted rows; the godot
layer's rows run on logs cut from real runs wherever that layer ships."""
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / '.claude/tools/run_wait.py'
sys.path.insert(0, str(TOOL.parent))
import run_wait

GODOT_ROWS = TOOL.parent / 'run_wait.godot.json'

SUITE_DONE = """PHASE_MS name=module-load ms=63
LOCKWAIT_MS=66
SLOT=5/6 source=default abandoned=0 peerAtStart=0
USERDIR=C:/x/test-appdata/f6
STATUS=DONE  label=s1  exit=1  elapsed=51.2s  attempts=1
Failed!  - Failed:     1, Passed:   773, Skipped:     0, Total:   774
FAILED_TEST=Tests.Logic.A.B
FAILED_MSG=Tests.Logic.A.B :: expected 1
LOG=C:/x/s1.log
"""
VERIFY_RUNNING = """VERIFY scope=Visual

--- logic  Visual ---
PHASE_MS name=module-load ms=63
STATUS=DONE  label=verify-Visual  exit=1  elapsed=51.2s  attempts=1
Failed!  - Failed:     1, Passed:   773
FAILED_TEST=Tests.Logic.Visual.C
LOG=C:/x/verify-Visual.log

--- integration  Visual ---
PLAN batches=1 units=2 target=60s budget=690s
"""
GATE_RUNNING = """REAP site=preflight killed=0 scope=orphans-only
ENGINE=OK ver=4.6.0
BUILD=OK
TOTAL passed=2893 failed=0 baseline=2893
COMPLETENESS=OK
STATUS=DONE
SUITE Integration passed=2893 failed=0 tier=PASS
"""
BUILD_LOG = "Determining projects to restore...\nBuild succeeded.\n    0 Warning(s)\n    0 Error(s)\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def log(self, text, name='run.log'):
        path = Path(self.dir.name) / name
        path.write_text(text, encoding='utf-8', newline='\n')
        return path

    def wait(self, path, writer, max_s=0.6, settle_s=0.0, runners=None):
        out = io.StringIO()
        with redirect_stdout(out):
            code = run_wait.wait(str(path), max_s, poll_s=0.02, settle_s=settle_s, writer=writer, runners=runners)
        return code, out.getvalue()


class PlantedRows(Base):
    """Nesting decided by row order and own lines, whatever runners a layer ships."""

    def runners(self):
        rows = Path(self.dir.name) / 'rows'
        rows.mkdir()
        (rows / 'run_wait.zz.json').write_text(
            '{"runners": [{"name": "outer", "word": "OUTER=", "own": "^OUTER begin"}, {"name": "inner", "word": "INNER="}]}',
            encoding='utf-8')
        return run_wait.load_runners(str(rows))

    def test_rows_load_after_the_exit_row_in_file_order(self):
        self.assertEqual([r.name for r in self.runners()], ['clamped-call', 'outer', 'inner'])

    def test_an_inner_terminal_line_never_ends_the_outer_run(self):
        log = self.log('OUTER begin\nINNER=DONE\nmore\n')
        self.assertEqual(self.wait(log, writer=lambda p: True, runners=self.runners())[0], 3)

    def test_the_outer_terminal_line_ends_it_with_every_block(self):
        code, out = self.wait(self.log('OUTER begin\nINNER=DONE\ncount\n\nOUTER=PASS\n'), writer=lambda p: False,
                              runners=self.runners())
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith('RUN-WAIT DONE outer'), out)
        self.assertIn('INNER=DONE\ncount\nOUTER=PASS', out)

    def test_an_inner_run_alone_ends_at_its_own_line(self):
        code, out = self.wait(self.log('INNER=DONE\n'), writer=lambda p: False, runners=self.runners())
        self.assertTrue(code == 0 and out.startswith('RUN-WAIT DONE inner'), out)


@unittest.skipUnless(GODOT_ROWS.is_file(), 'the godot layer ships these runners')
class Verdicts(Base):
    def test_a_finished_suite_log_is_done_with_its_verdict_block(self):
        code, out = self.wait(self.log(SUITE_DONE), writer=lambda p: False)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith('RUN-WAIT DONE run_test_suite'), out)
        for line in ('STATUS=DONE  label=s1', 'Failed!  - Failed:     1', 'FAILED_TEST=Tests.Logic.A.B', 'LOG=C:/x/s1.log'):
            self.assertIn(line, out)
        self.assertNotIn('USERDIR=', out)

    def test_an_inner_suite_status_never_ends_a_verify_run(self):
        code, out = self.wait(self.log(VERIFY_RUNNING), writer=lambda p: True)
        self.assertEqual(code, 3, out)
        self.assertTrue(out.startswith('RUN-WAIT RUNNING verify'), out)
        self.assertIn('last=PLAN batches=1', out)

    def test_an_inner_status_never_ends_a_gate_run(self):
        code, out = self.wait(self.log(GATE_RUNNING), writer=lambda p: True)
        self.assertEqual(code, 3, out)

    def test_a_gate_ends_at_its_verdict_line(self):
        code, out = self.wait(self.log(GATE_RUNNING + 'DETAIL=C:/x/gate_last.md\nVERDICT=PASS\n'), writer=lambda p: False)
        self.assertEqual(code, 0, out)
        self.assertIn('VERDICT=PASS', out)
        self.assertEqual(out.count('VERDICT=PASS'), 1, out)

    def test_the_run_ends_as_soon_as_its_terminal_line_lands_with_every_inner_block(self):
        path = self.log(VERIFY_RUNNING)

        def finish():
            time.sleep(0.15)
            with open(path, 'a', encoding='utf-8', newline='\n') as fh:
                fh.write('STATUS=DONE\n\nVERIFY=FAIL scope=Visual tiers=2\nA verify result does not back a commit.\n')
        threading.Thread(target=finish).start()
        began = time.monotonic()
        code, out = self.wait(path, writer=lambda p: True, max_s=5)
        self.assertEqual(code, 0, out)
        self.assertLess(time.monotonic() - began, 2)
        for line in ('VERIFY=FAIL scope=Visual', 'FAILED_TEST=Tests.Logic.Visual.C', 'STATUS=DONE  label=verify-Visual'):
            self.assertIn(line, out)

    def test_a_held_log_waits_for_the_lines_written_after_the_terminal_line(self):
        path = self.log(SUITE_DONE.split('Failed!')[0])

        def tail():
            time.sleep(0.1)
            with open(path, 'a', encoding='utf-8', newline='\n') as fh:
                fh.write('Failed!  - Failed:     1\nFAILED_TEST=Tests.Logic.A.B\n')
        threading.Thread(target=tail).start()
        code, out = self.wait(path, writer=lambda p: True, max_s=5, settle_s=0.4)
        self.assertEqual(code, 0, out)
        self.assertIn('FAILED_TEST=Tests.Logic.A.B', out)

    def test_a_clamped_commands_exit_line_ends_the_wait(self):
        code, out = self.wait(self.log('some output\nEXIT 1\n', 'b1.output'), writer=lambda p: True)
        self.assertEqual(code, 0, out)
        self.assertIn('EXIT 1', out)

    def test_a_suite_that_refused_before_its_locks_is_done(self):
        code, out = self.wait(self.log('STATUS=HELD_REFUSED  label=x  unmet=lock-error\n'), writer=lambda p: False)
        self.assertEqual(code, 0, out)


class NotARun(Base):
    @unittest.skipUnless(GODOT_ROWS.is_file(), 'the godot layer ships this runner')
    def test_a_run_stopped_before_its_terminal_line_ends_without_a_verdict(self):
        code, out = self.wait(self.log(VERIFY_RUNNING), writer=lambda p: False)
        self.assertEqual(code, 2, out)

    def test_a_log_no_runner_writes_ends_without_a_verdict_once_no_one_writes_it(self):
        code, out = self.wait(self.log(BUILD_LOG), writer=lambda p: False)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith('RUN-WAIT ENDED'), out)
        self.assertIn('0 Error(s)', out)

    def test_a_log_still_written_is_still_going_whatever_wrote_it(self):
        code, out = self.wait(self.log(BUILD_LOG), writer=lambda p: True)
        self.assertEqual(code, 3, out)

    def test_where_writers_cannot_be_seen_the_wait_never_calls_a_run_ended(self):
        code, out = self.wait(self.log(BUILD_LOG), writer=lambda p: None)
        self.assertEqual(code, 3, out)

    def test_a_log_that_never_appears_ends_at_the_bound(self):
        code, out = self.wait(Path(self.dir.name) / 'missing.log', writer=lambda p: False, max_s=0.1)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith('RUN-WAIT NO-LOG'), out)


class Bound(unittest.TestCase):
    def test_the_default_bound_returns_inside_the_callers_clamp(self):
        env = dict(os.environ)
        try:
            os.environ.pop('CLAUDE_CODE_PROMPT_CACHE_TTL', None)
            short = run_wait.default_max()
            os.environ['CLAUDE_CODE_PROMPT_CACHE_TTL'] = '1h'
            long = run_wait.default_max()
        finally:
            os.environ.clear()
            os.environ.update(env)
        self.assertEqual((short, long), (270 - run_wait.MARGIN_S, 600 - run_wait.MARGIN_S))

    def test_the_clamped_calls_exit_row_comes_first(self):
        self.assertEqual(run_wait.RUNNERS[0], run_wait.EXIT_ROW)


@unittest.skipUnless(os.name == 'nt', 'the writer probe reads Windows share modes')
class WriterProbe(Base):
    def test_an_open_write_handle_is_a_writer_and_a_closed_one_is_not(self):
        path = self.log('x\n')
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write('y\n')
            fh.flush()
            self.assertIs(run_wait.writer_open(str(path)), True)
        self.assertIs(run_wait.writer_open(str(path)), False)


@unittest.skipUnless(GODOT_ROWS.is_file(), 'the godot layer ships this runner')
class Cli(Base):
    def test_the_command_line_prints_the_block_and_exits_with_the_code(self):
        r = subprocess.run([sys.executable, str(TOOL), '--max', '5', str(self.log(SUITE_DONE))],
                           capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('STATUS=DONE  label=s1', r.stdout)


if __name__ == '__main__':
    unittest.main()
