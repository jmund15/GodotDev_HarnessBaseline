"""The shared work budget: admission, loans, dead owners and caller wiring."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / '.claude/tools'
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / '.claude/scripts'))
import machine_slots as slots


class MachineSlots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='machine_slots_')
        self.addCleanup(self.tmp.cleanup)
        self.folder = str(Path(self.tmp.name) / 'pool')
        env = {'HARNESS_SLOT_DIR': self.folder, 'HARNESS_MACHINE_SLOTS': '2'}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ('HARNESS_SLOT_BUDGET', 'HARNESS_MACHINE_ROOT'):
            old = os.environ.pop(name, None)
            self.addCleanup(lambda n=name, v=old: os.environ.__setitem__(n, v) if v is not None else os.environ.pop(n, None))

    def test_a_take_survives_an_unavailable_checkout(self):
        with patch.object(slots.os, 'getcwd', side_effect=FileNotFoundError):
            held = slots.take()
        try:
            self.assertEqual(len(held), 1)
            note = json.loads(Path(held[0].path).read_text(encoding='utf-8'))
            self.assertEqual(note['pid'], os.getpid())
            self.assertEqual(note['checkout'], '')
        finally:
            slots.release_all(held)

    def test_a_lane_gets_its_share_of_the_cores(self):
        with patch.object(slots.os, 'cpu_count', return_value=24):
            self.assertEqual(slots.lane_cores(1, 4), 6)
            self.assertEqual(slots.lane_cores(2, 4), 12)
            self.assertEqual(slots.lane_cores(4, 4), 24)
            self.assertEqual(slots.lane_cores(1, 0), 24, 'a zero width must not divide by zero')
        with patch.object(slots.os, 'cpu_count', return_value=2):
            self.assertEqual(slots.lane_cores(1, 4), 1, 'a share never drops below one core')
        with patch.object(slots.os, 'cpu_count', return_value=None):
            self.assertEqual(slots.lane_cores(1, 4), 1, 'an unknown core count gives one core')

    def test_the_share_splits_over_the_busy_lanes_not_the_idle_ones(self):
        with patch.object(slots.os, 'cpu_count', return_value=24):
            self.assertEqual(slots.lane_cores(1, 12, busy=1), 24, 'a lone lane in an idle pool takes every core')
            self.assertEqual(slots.lane_cores(1, 12, busy=4), 6)
            self.assertEqual(slots.lane_cores(1, 12, busy=12), 2, 'a full pool keeps the even split')
            self.assertEqual(slots.lane_cores(2, 12, busy=1), 24, 'busy never counts fewer lanes than the caller holds')

    def _run_cores(self):
        out = subprocess.run([sys.executable, str(TOOLS / 'machine_slots.py'), 'run', '--', sys.executable, '-c',
                              'import os; print(os.environ.get("HARNESS_LANE_CORES"))'],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return int(out.stdout.strip())

    def test_run_tells_its_command_the_share_of_the_busy_pool(self):
        cores = os.cpu_count() or 1
        self.assertEqual(self._run_cores(), cores, 'alone in the pool, the lane gets every core')
        other = slots.take(False)
        try:
            self.assertEqual(self._run_cores(), max(1, cores // 2), 'beside one held lane, it gets half')
        finally:
            slots.release_all(other)

    def _wait_for_lock(self, deadline=10):
        """The first lane lock a spawned holder writes, as (path, its parsed note)."""
        end = time.time() + deadline
        while time.time() < end:
            for entry in sorted(os.listdir(self.folder)) if os.path.isdir(self.folder) else []:
                if entry.startswith('slot-') and entry.endswith('.lock'):
                    path = Path(self.folder) / entry
                    try:
                        text = path.read_text(encoding='utf-8')
                        return path, json.loads(text)
                    except (OSError, ValueError):
                        continue
            time.sleep(.05)
        self.fail('a holder never wrote its lane')

    def test_a_held_lane_names_its_holder_and_the_queued_notice_repeats_it(self):
        # A queued proof used to read a bare pid, which named nothing once the holder was gone.
        env = dict(os.environ, HARNESS_SLOT_DIR=self.folder, HARNESS_MACHINE_SLOTS='2')
        child = subprocess.Popen([sys.executable, str(TOOLS / 'machine_slots.py'), 'run', '--',
                                  sys.executable, '-c', 'import time; time.sleep(10)'],
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _path, note = self._wait_for_lock()
            self.assertEqual(note['pid'], child.pid)
            self.assertIn('machine_slots.py', note['command'])
            self.assertTrue(note['checkout'])
            named = slots._holders(self.folder, [])
            self.assertIn('held by pid %d' % child.pid, named)
            self.assertIn('machine_slots.py', named)
        finally:
            child.terminate()
            child.wait(timeout=15)

    def test_a_brokered_lane_names_the_work_it_holds_for(self):
        # The Windows broker wrote its own command, so a C# or WSL lane's lock named `machine_slots.py broker`,
        # never the work: the notice could not say what held the machine.
        env = dict(os.environ, HARNESS_SLOT_DIR=self.folder, HARNESS_MACHINE_SLOTS='1')
        note = json.dumps({'pid': 999999, 'command': 'RuntimeSlots gate -Repo ExampleRepo',
                           'checkout': 'C:/repo'})
        broker = subprocess.Popen([sys.executable, str(TOOLS / 'machine_slots.py'), 'broker', '--folder',
                                   self.folder, '--width', '1', '--count', '1', '--holder', note],
                                  env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(json.loads(broker.stdout.readline()).get('held'), 1)
            written = json.loads((Path(self.folder) / 'slot-0.lock').read_text(encoding='utf-8'))
            self.assertEqual(written['command'], 'RuntimeSlots gate -Repo ExampleRepo')
            self.assertEqual(written['checkout'], 'C:/repo')
            self.assertEqual(str(written['pid']), str(broker.pid), 'the lane belongs to the broker process')
            self.assertEqual(written.get('caller_pid'), '999999')
            notice = io.StringIO()
            with patch.object(slots, 'NOTICE_AFTER', 0.2), contextlib.redirect_stderr(notice), \
                    patch.dict(os.environ, {'HARNESS_SLOT_DIR': self.folder, 'HARNESS_MACHINE_SLOTS': '1'}):
                self.assertIsNone(slots.take(notice=True, deadline=time.time() + 0.6))
            self.assertIn('held by pid %d' % broker.pid, notice.getvalue())
            self.assertIn('RuntimeSlots gate -Repo ExampleRepo', notice.getvalue())
        finally:
            broker.stdin.close()
            broker.wait(timeout=15)
            broker.stdout.close()
            broker.stderr.close()

    def test_a_lock_a_bare_pid_wrote_still_names_its_holder(self):
        # A lock an older runner holds carries the pid alone; the notice still prints it.
        os.makedirs(self.folder, exist_ok=True)
        (Path(self.folder) / 'slot-1.lock').write_text('424242', encoding='ascii')
        self.assertEqual(slots._holders(self.folder, []), '; held by pid 424242 (1)')

    def _run_named(self, *argv):
        bin_dir = Path(self.tmp.name) / 'bin'
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / 'tool.cmd').write_text('@echo off\r\necho [%1]\r\n', encoding='utf-8')
        env = dict(os.environ, PATH=str(bin_dir), HOME=self.tmp.name, USERPROFILE=self.tmp.name)
        return subprocess.run([sys.executable, str(TOOLS / 'machine_slots.py'), 'run', '--', *argv],
                              capture_output=True, text=True, timeout=60, env=env)

    @unittest.skipUnless(os.name == 'nt', 'CreateProcess applies no PATHEXT only on Windows')
    def test_run_finds_a_cmd_tool_by_its_bare_name(self):
        out = self._run_named('tool', 'a b')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('a b', out.stdout)

    @unittest.skipUnless(os.name == 'nt', 'cmd.exe parses a .cmd tool\'s arguments')
    def test_run_refuses_a_cmd_argument_cmd_exe_would_reparse(self):
        out = self._run_named('tool', 'x"&echo INJECTED')
        self.assertEqual(out.returncode, 2)
        self.assertNotIn('INJECTED', out.stdout)
        self.assertNotIn('Traceback', out.stderr)

    def test_run_names_a_missing_command_and_exits_2(self):
        out = self._run_named('no_such_tool_xyz')
        self.assertEqual(out.returncode, 2)
        self.assertIn('no_such_tool_xyz', out.stderr)
        self.assertNotIn('Traceback', out.stderr)

    def test_run_passes_a_proof_runner_through_without_a_lane(self):
        # A wrapped runner held one lane for its whole batch and ran every proof serially inside it, so an
        # exclusive head waited out the batch while the rest of the pool sat idle.
        runner = Path(self.tmp.name) / 'harness_tests.py'
        runner.write_text('import os; print(os.environ.get("HARNESS_SLOT_BUDGET"))', encoding='utf-8')
        held = slots.take(True)
        try:
            out = subprocess.run([sys.executable, str(TOOLS / 'machine_slots.py'), 'run', '--deadline', str(time.time() + 3),
                                  '--', sys.executable, str(runner), '--proofs-for', 'x.py'],
                                 capture_output=True, text=True, timeout=60)
        finally:
            slots.release_all(held)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(out.stdout.strip(), 'None', 'the runner admits itself per proof, never in an inherited lane')

    def test_run_does_not_take_a_machine_lane_for_pytest(self):
        # The wrapper must not sit on a lane for the entire pytest coordinator run.
        proof = Path(self.tmp.name) / 'test_no_lane.py'
        proof.write_text('import os\n\ndef test_no_lane():\n    assert not os.environ.get("HARNESS_SLOT_BUDGET")\n',
                         encoding='utf-8')
        held = slots.take(True)
        try:
            out = subprocess.run([sys.executable, str(TOOLS / 'machine_slots.py'), 'run', '--deadline',
                                  str(time.time() + 1), '--', sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider',
                                  '--rootdir', self.tmp.name, str(proof)],
                                 capture_output=True, text=True, timeout=60, cwd=self.tmp.name)
        finally:
            slots.release_all(held)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_run_still_queues_a_proof_that_names_the_runner_as_an_argument(self):
        held = slots.take(True)
        try:
            out = subprocess.run([sys.executable, str(TOOLS / 'machine_slots.py'), 'run', '--deadline', str(time.time() + 1),
                                  '--', sys.executable, '-c', 'print(1)', 'harness_tests.py'],
                                 capture_output=True, text=True, timeout=60)
        finally:
            slots.release_all(held)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_a_dispatched_jobs_run_gives_up_its_place_in_a_full_pool_and_names_the_run_owed(self):
        held = slots.take(True)
        out = Path(self.tmp.name) / 'out.txt'
        argv = ['machine_slots.py', 'run', '--', sys.executable, 'test_x.py']
        try:
            with patch.dict(os.environ, {'CLAUDE_CODE_SIDECAR': 'anthropic', 'CLAUDE_CODE_SIDECAR_KIND': 'author'}), \
                    patch.object(slots, 'JOB_WAIT_S', 0.3), patch.object(sys, 'argv', argv), \
                    open(out, 'w', encoding='utf-8') as fh, contextlib.redirect_stdout(fh), \
                    contextlib.redirect_stderr(fh):
                began = time.monotonic()
                code = slots.main()
                waited = time.monotonic() - began
        finally:
            slots.release_all(held)
        text = out.read_text(encoding='utf-8')
        self.assertEqual(code, 2, text)
        self.assertLess(waited, 10, 'the job waits one bounded call, not for as long as the pool stays full')
        self.assertIn('test_x.py', text, 'the line names the run the job owes its dispatcher')

    def test_only_a_dispatched_non_gate_job_gets_the_bound(self):
        with patch.dict(os.environ, {'CLAUDE_CODE_SIDECAR': 'anthropic', 'CLAUDE_CODE_SIDECAR_KIND': 'author'}):
            self.assertIsNotNone(slots.job_deadline())
        with patch.dict(os.environ, {'CLAUDE_CODE_SIDECAR': 'anthropic', 'CLAUDE_CODE_SIDECAR_KIND': 'gate'}):
            self.assertIsNone(slots.job_deadline(), 'a gate job exists to run the wide run, queue included')
        with patch.dict(os.environ):
            os.environ.pop('CLAUDE_CODE_SIDECAR', None)
            os.environ.pop('CLAUDE_CODE_ENTRYPOINT', None)
            self.assertIsNone(slots.job_deadline(), 'the session itself is woken by its watcher and keeps its place')
            os.environ['CLAUDE_CODE_ENTRYPOINT'] = 'sdk-cli'
            self.assertIsNotNone(slots.job_deadline(), 'a claude -p child is a delegate whatever its sidecar marker')

    def test_a_dispatched_jobs_proof_process_gives_up_its_place_the_same_way(self):
        # sitecustomize admits a direct `python test_x.py` through admit(); None makes it exit 2.
        held = slots.take(True)
        out = Path(self.tmp.name) / 'admit.txt'
        try:
            with patch.dict(os.environ, {'CLAUDE_CODE_SIDECAR': 'anthropic', 'CLAUDE_CODE_SIDECAR_KIND': 'author'}), \
                    patch.object(slots, 'JOB_WAIT_S', 0.3), open(out, 'w', encoding='utf-8') as fh, \
                    contextlib.redirect_stderr(fh):
                got = slots.admit(['python', 'test_x.py'])
        finally:
            slots.release_all(held)
        self.assertIsNone(got)
        self.assertIn('test_x.py', out.read_text(encoding='utf-8'))

    def test_windows_root_ignores_proof_home(self):
        with patch.object(slots.os, 'name', 'nt'), patch.dict(os.environ, {'USERPROFILE': 'C:/Users/worker', 'HOME': 'X:/fixture'}):
            os.environ.pop('HARNESS_SLOT_DIR')
            self.assertEqual(slots.slot_dir().replace('\\', '/'), 'C:/Users/worker/.claude/.harness_slots')

    def test_a_count_take_that_misses_its_deadline_returns_its_partial_hold(self):
        first = slots.take(False)
        try:
            self.assertIsNone(slots.take(False, 2, deadline=time.time()))
            second = slots.take(False, deadline=time.time())
            self.assertIsNotNone(second, 'failed two-slot admission must return its partial hold')
            slots.release_all(second)
        finally:
            slots.release_all(first)

    def test_a_lane_lease_leaves_a_take_and_an_end_row_and_a_missed_deadline_one_end_row(self):
        held = slots.take(holder=json.dumps({'pid': 7, 'label': 'job-x', 'command': 'c', 'checkout': 'C:/w'}))
        try:
            self.assertIsNone(slots.take(True, deadline=time.time() + 0.3))
        finally:
            slots.release_all(held)
        slots.release_all(held)
        rows = [json.loads(line) for line in Path(self.folder, 'leases.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([(r['event'], r.get('ended')) for r in rows], [('take', None), ('end', 'deadline'), ('end', 'released')],
                         'a second release of the same lease writes nothing')
        take, missed, end = rows
        self.assertEqual(take['id'], end['id'])
        self.assertNotEqual(take['id'], missed['id'])
        self.assertEqual((take['pool'], take['pid'], take['label'], take['ask']['lanes'], take['got']['lanes']),
                         ('lane', 7, 'job-x', 1, [0]))
        self.assertEqual(missed['ask']['lanes'], 'all')
        self.assertGreaterEqual(missed['wait']['lane'], 0.25)

    def test_nested_budget_serializes_runner_but_can_borrow_free_cases(self):
        import harness_tests as runner
        held = slots.take(False)
        try:
            with patch.dict(os.environ, {'HARNESS_SLOT_BUDGET': '1', 'HARNESS_MACHINE_ROOT': self.folder, 'HARNESS_TEST_JOBS': '8', 'HARNESS_DEADLINE': str(time.time() - 1)}):
                self.assertEqual(runner._jobs(), 1)
                self.assertEqual(runner._take_slots(True), [])
                with slots.parallel_budget(4) as width:
                    self.assertEqual(width, 2)
                    self.assertIsNone(slots.take(False, deadline=time.time(), inherit=False))
            available = slots.take(False, deadline=time.time())
            self.assertIsNotNone(available)
            slots.release_all(available)
        finally:
            slots.release_all(held)

    def test_exclusive_waiter_drains_without_new_work_entering(self):
        first = slots.take(False)
        acquired, release = threading.Event(), threading.Event()
        def waiter():
            held = slots.take(True, deadline=time.time() + 10)
            if held is not None:
                acquired.set()
                release.wait(10)
                slots.release_all(held)
        worker = threading.Thread(target=waiter)
        worker.start()
        try:
            self._wait_for_tickets(1)
            self.assertIsNone(slots.take(False, deadline=time.time()))
            slots.release_all(first)
            first = []
            self.assertTrue(acquired.wait(10))
        finally:
            slots.release_all(first)
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())

    def test_an_older_harness_taker_cannot_starve_an_exclusive_head(self):
        """A harness from before the queue (another project's, an old worktree's) shares this pool. Each of its
        workers claims any free slot once drain.lock is free and holds it for a whole proof; its workers overlap,
        so some slot is nearly always held. The exclusive head must still get the machine."""
        folder, stop = Path(self.folder), threading.Event()
        folder.mkdir(parents=True, exist_ok=True)

        def older_worker(offset):
            time.sleep(offset)
            while not stop.is_set():
                probe = slots._file_lock.acquire(str(folder / 'drain.lock'), 0)
                if not probe:
                    time.sleep(.005)
                    continue
                slots._file_lock.release(probe)
                taken = None
                for index in range(2):
                    taken = slots._file_lock.acquire(str(folder / ('slot-%d.lock' % index)), 0)
                    if taken:
                        break
                if taken:
                    time.sleep(.04)
                    slots._file_lock.release(taken)

        workers = [threading.Thread(target=older_worker, args=(offset,)) for offset in (0, .02)]
        for worker in workers:
            worker.start()
        try:
            time.sleep(.1)
            got = slots.take(True, deadline=time.time() + 5)
            self.assertIsNotNone(got, 'the exclusive head never got the machine while an older taker ran')
            self.assertEqual(len(got), 2)
            slots.release_all(got)
        finally:
            stop.set()
            for worker in workers:
                worker.join(10)

    def _ordered_takers(self, kinds):
        """One taker per kind ('x' exclusive, 's' shared), queued in that order behind a held lane; returns the
        order they were served in and each taker's `split`."""
        os.environ['HARNESS_MACHINE_SLOTS'] = '1'
        holder = slots.take(False)
        served, splits, threads = [], {}, []
        for index, kind in enumerate(kinds):
            splits[index] = {}
            def taker(index=index, kind=kind):
                held = slots.take(kind == 'x', deadline=time.time() + 20, split=splits[index])
                served.append(index)
                time.sleep(.05)
                slots.release_all(held)
            thread = threading.Thread(target=taker)
            thread.start()
            threads.append(thread)
            self._wait_for_tickets(index + 1)
        time.sleep(.3)
        slots.release_all(holder)
        for thread in threads:
            thread.join(20)
        return served, splits

    def test_exclusive_takers_queued_together_share_one_drain(self):
        """Measured 2026-11-19: runs each took the whole machine for their serial proofs, interleaved with shared
        work, and every exclusive take after shared work waited out the longest running lane again; a shared run
        waited 2441 s for about 340 s of proofs. Exclusive tickets are served together, ahead of shared ones."""
        served, _ = self._ordered_takers('xsx')
        self.assertEqual(served, [0, 2, 1])

    def test_shared_tickets_still_go_in_arrival_order_behind_an_exclusive_head(self):
        served, _ = self._ordered_takers('xss')
        self.assertEqual(served, [0, 1, 2])

    def _head_overtaken_by_exclusive(self, width, shared_count):
        """A shared head holds drain.lock behind a full pool, an exclusive ticket then queues ahead of it, and the
        holder releases. Returns the order the two were served in."""
        os.environ['HARNESS_MACHINE_SLOTS'] = str(width)
        holder = slots.take(False)
        served, threads, events = [], [], {}
        def taker(name, exclusive, count):
            events[name] = threading.Event()
            held = slots.take(exclusive, count, deadline=time.time() + 8)
            if held is not None:
                served.append(name)
                events[name].set()
                time.sleep(.1)
                slots.release_all(held)
        try:
            threads.append(threading.Thread(target=taker, args=('shared', False, shared_count)))
            threads[-1].start()
            self._wait_for_tickets(1)
            drain = Path(self.folder) / 'drain.lock'
            until = time.monotonic() + 5
            while not drain.exists() and time.monotonic() < until:
                time.sleep(.01)
            self.assertTrue(drain.exists(), 'the shared head never took drain.lock')
            threads.append(threading.Thread(target=taker, args=('exclusive', True, 1)))
            threads[-1].start()
            self._wait_for_tickets(2)
            time.sleep(.2)
        finally:
            slots.release_all(holder)
        for thread in threads:
            thread.join(20)
        return served, events

    def test_a_head_overtaken_by_an_exclusive_ticket_lets_it_have_the_machine(self):
        """Observed 2026-11-21: a shared broker held drain.lock with no slot held and the whole pool free for 47
        minutes, and 61 tickets queued behind it. It had taken drain.lock as head; an exclusive ticket then went
        ahead of it, so it stopped collecting but kept drain.lock, which the exclusive head needs."""
        started = time.monotonic()
        served, _ = self._head_overtaken_by_exclusive(1, 1)
        self.assertEqual(served, ['exclusive', 'shared'])
        self.assertLess(time.monotonic() - started, 6, 'the exclusive head waited for the shared head to give up')

    def test_an_overtaken_head_returns_the_slots_it_had_collected(self):
        started = time.monotonic()
        served, _ = self._head_overtaken_by_exclusive(2, 2)
        self.assertEqual(served, ['exclusive', 'shared'])
        self.assertLess(time.monotonic() - started, 6, 'the exclusive head waited for a slot the shared head kept')

    def test_a_take_reports_where_its_wait_went(self):
        _, splits = self._ordered_takers('xs')
        self.assertGreaterEqual(splits[0].get('collect', 0), .2, 'the exclusive head collected while a lane ran')
        self.assertGreaterEqual(splits[1].get('behind', 0), .2, 'the shared taker waited behind the exclusive head')

    def _queued(self, deadline=10):
        """A taker that waits in its own thread; returns (thread, result dict)."""
        result = {}
        def wait():
            held = slots.take(False, deadline=time.time() + deadline)
            result['at'] = time.monotonic()
            result['held'] = held
        thread = threading.Thread(target=wait)
        thread.start()
        return thread, result

    def _wait_for_tickets(self, count):
        queue = Path(self.folder) / 'queue'
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if queue.is_dir() and len(slots._queued(str(queue))) >= count:
                return
            time.sleep(.01)
        self.fail('the waiters never queued')

    def test_waiters_are_served_in_arrival_order_and_a_releaser_cannot_jump_them(self):
        os.environ['HARNESS_MACHINE_SLOTS'] = '1'
        holder = slots.take(False)
        first, a = self._queued()
        self._wait_for_tickets(1)
        second, b = self._queued()
        self._wait_for_tickets(2)
        slots.release_all(holder)
        # A holder that re-takes at once, and a borrower that never waits, both queue behind the waiters.
        self.assertIsNone(slots.take(False, deadline=time.time(), inherit=False))
        first.join(10)
        self.assertIsNotNone(a.get('held'))
        self.assertNotIn('held', b, 'the second waiter was served before the first released')
        slots.release_all(a['held'])
        second.join(10)
        self.assertIsNotNone(b.get('held'))
        self.assertLess(a['at'], b['at'])
        slots.release_all(b['held'])

    def test_a_queued_taker_says_so_on_stderr_only_when_asked(self):
        import io
        os.environ['HARNESS_MACHINE_SLOTS'] = '1'
        holder = slots.take(False)
        try:
            with patch.object(slots, 'NOTICE_AFTER', 0), patch.object(sys, 'stderr', io.StringIO()) as err:
                self.assertIsNone(slots.take(False, deadline=time.time() + .3, notice=True))
                self.assertIn('queued', err.getvalue())
                self.assertIn(str(os.getpid()), err.getvalue(), 'the queued line names the pid holding the pool')
                err.truncate(0)
                self.assertIsNone(slots.take(False, deadline=time.time() + .3))
                self.assertEqual(err.getvalue(), '')
        finally:
            slots.release_all(holder)

    def test_a_guard_says_it_queued_only_when_asked(self):
        import io
        os.environ['HARNESS_MACHINE_SLOTS'] = '1'
        os.environ['HARNESS_DEADLINE'] = str(time.time() + .3)
        self.addCleanup(os.environ.pop, 'HARNESS_DEADLINE', None)
        holder = slots.take(False)
        try:
            with patch.object(slots, 'NOTICE_AFTER', 0), patch.object(sys, 'stderr', io.StringIO()) as err:
                with self.assertRaises(slots.SlotStoreError), slots.guard(notice=True):
                    pass
                self.assertIn('queued', err.getvalue())
                err.truncate(0)
                with self.assertRaises(slots.SlotStoreError), slots.guard():
                    pass
                self.assertEqual(err.getvalue(), '')
        finally:
            slots.release_all(holder)

    def _plant(self, pid, exclusive=False):
        """A live ticket another process (named `pid`) holds in the queue, named by production's own rule."""
        queue = Path(self.folder) / 'queue'
        queue.mkdir(parents=True, exist_ok=True)
        with slots._TICKET_LOCK:
            name = slots._ticket_name(str(queue), exclusive, pid=pid)
            handle = slots._file_lock.acquire(str(queue / name), 0)
        self.assertIsNotNone(handle)
        self.addCleanup(slots.release, handle)
        return name, handle

    def _served_order(self, tickets):
        """Release the head ticket, over and over, as admission would; returns the names in that order."""
        queue, order = str(Path(self.folder) / 'queue'), []
        live = dict(tickets)
        while live:
            head = [name for name in live if not slots._live_ticket_before(queue, name)]
            self.assertEqual(len(head), 1, 'exactly one ticket is the head')
            order.append(head[0])
            slots.release(live.pop(head[0]))
        return order

    def test_a_callers_nth_ticket_waits_behind_every_other_callers_first_n_minus_one(self):
        """Shared tickets are first come first served per caller, round-robin across callers, so one caller's
        many tickets never queue a lone caller's single ticket behind all of them."""
        many = [self._plant(111) for _ in range(3)]
        lone = self._plant(222)
        other = self._plant(333)
        order = self._served_order(many + [lone, other])
        self.assertEqual(order[0], many[0][0])
        self.assertEqual(set(order[1:3]), {lone[0], other[0]}, 'each other caller\'s first ticket goes before the many-ticket caller\'s second')
        self.assertEqual(order[3:], [many[1][0], many[2][0]], 'a caller\'s tickets keep arrival order')

    def test_a_caller_that_arrives_late_joins_the_current_round_not_the_end(self):
        many = [self._plant(111) for _ in range(4)]
        time.sleep(.2)
        lone = self._plant(222)
        order = self._served_order(many + [lone])
        self.assertEqual(order.index(lone[0]), 1)

    def test_a_releaser_that_takes_again_queues_behind_the_waiting_callers_next_ticket(self):
        waiting = [self._plant(111) for _ in range(3)]
        churner = self._plant(222)
        slots.release(churner[1])
        again = self._plant(222)
        order = self._served_order(waiting + [again])
        self.assertEqual(order[:2], [waiting[0][0], again[0]])
        self.assertEqual(order[2:], [waiting[1][0], waiting[2][0]])

    def test_a_ticket_from_a_checkout_without_rounds_is_never_served_after_a_newer_one(self):
        """The pool is shared by checkouts at different harness versions; an old one orders by arrival only."""
        queue = Path(self.folder) / 'queue'
        queue.mkdir(parents=True)
        old = ['%020d-%d-%d.lock' % (time.time_ns() + i, 999, i) for i in range(3)]
        handles = [(name, slots._file_lock.acquire(str(queue / name), 0)) for name in old]
        fresh = [self._plant(111), self._plant(222)]
        order = self._served_order(handles + fresh)
        self.assertEqual(order[:3], old)

    def test_a_process_queuing_many_tickets_at_once_spaces_them_by_round(self):
        os.environ['HARNESS_MACHINE_SLOTS'] = '1'
        holder = slots.take(False)
        threads = [threading.Thread(target=lambda: slots.release_all(slots.take(False, deadline=time.time() + 8))) for _ in range(6)]
        for thread in threads:
            thread.start()
        self._wait_for_tickets(6)
        keys = sorted(int(name.split('-')[0]) for name in slots._queued(str(Path(self.folder) / 'queue')))
        self.assertEqual(len(set(k // slots._ROUND_NS for k in keys)), 6, 'tickets queued together shared a round')
        slots.release_all(holder)
        for thread in threads:
            thread.join(10)

    def test_a_ticket_being_created_is_not_a_ticket(self):
        """On POSIX a lock is created as `<name>.<hex>.new` and hard-linked to its name, so the queue briefly holds
        both. Ticket order and every count of the queue read only finished `.lock` names."""
        queue = Path(self.folder) / 'queue'
        queue.mkdir(parents=True, exist_ok=True)
        (queue / ('%020d-424242-1-qx.lock.0011223344556677.new' % 5)).write_bytes(b'')
        name = slots._ticket_name(str(queue), False)
        self.assertGreater(int(name.split('-')[0]), 10 ** 9, 'a staged twin set the shared key floor')
        self.assertEqual(slots._queued(str(queue)), [])

    def test_a_lone_caller_is_admitted_before_a_many_ticket_callers_second(self):
        os.environ['HARNESS_MACHINE_SLOTS'] = '1'
        first = self._plant(111)
        self._plant(111)
        self._plant(111)
        thread, lone = self._queued()
        self._wait_for_tickets(4)
        time.sleep(.3)
        self.assertNotIn('held', lone, 'the head ticket is served first')
        slots.release(first[1])
        thread.join(10)
        self.assertIsNotNone(lone.get('held'), 'the lone ticket waited behind the second and third older tickets')
        slots.release_all(lone['held'])

    def test_an_exclusive_ticket_still_goes_ahead_of_every_shared_round(self):
        shared = [self._plant(111) for _ in range(2)]
        exclusive = self._plant(222, exclusive=True)
        later = self._plant(333, exclusive=True)
        order = self._served_order(shared + [exclusive, later])
        self.assertEqual(order, [exclusive[0], later[0], shared[0][0], shared[1][0]])

    def test_a_dead_waiters_ticket_does_not_block_the_line(self):
        os.environ['HARNESS_MACHINE_SLOTS'] = '1'
        crash = subprocess.run([sys.executable, '-c',
            "import sys,os,time;sys.path.insert(0,sys.argv[1]);import machine_slots as s;"
            "h=s.take(False);import threading;threading.Thread(target=lambda:s.take(False,deadline=time.time()+30),daemon=True).start();"
            "time.sleep(1);os._exit(0)", str(TOOLS)], env=dict(os.environ))
        self.assertEqual(crash.returncode, 0)
        held = slots.take(False, deadline=time.time() + 10)
        self.assertIsNotNone(held, 'a crashed waiter left a ticket that still blocks admission')
        slots.release_all(held)

    def test_dead_broker_releases_and_two_processes_share_the_cap(self):
        env = dict(os.environ, HARNESS_MACHINE_SLOTS='1')
        cmd = [sys.executable, str(TOOLS / 'machine_slots.py'), 'broker']
        first = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.assertEqual(json.loads(first.stdout.readline())['held'], 1)
        second = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        line = []
        ready = threading.Event()
        thread = threading.Thread(target=lambda: (line.append(second.stdout.readline()), ready.set()))
        thread.start()
        try:
            self.assertFalse(ready.wait(.15), 'second process must queue behind the held slot')
            first.stdin.close()
            self.assertEqual(first.wait(timeout=10), 0)
            self.assertTrue(ready.wait(10))
            self.assertEqual(json.loads(line[0])['held'], 1)
        finally:
            for child in (first, second):
                if not child.stdin.closed:
                    child.stdin.close()
                child.wait(timeout=10)
                child.stdout.close()
            thread.join(10)
        # Abrupt interpreter exit leaves a stale filename, not a live lease.
        crash = subprocess.run([sys.executable, '-c',
            "import sys,os;sys.path.insert(0,sys.argv[1]);import machine_slots as s;s.take(False);os._exit(0)", str(TOOLS)], env=env)
        self.assertEqual(crash.returncode, 0)
        held = slots.take(True, deadline=time.time() + 5)
        self.assertIsNotNone(held)
        slots.release_all(held)

    def test_wsl_hands_a_windows_pool_path_to_the_broker_unchanged(self):
        # wslpath -w mangles a path that is already Windows-style (drops its separators).
        with patch.object(slots, '_wsl_path', side_effect=AssertionError('wslpath must not touch a Windows path')):
            self.assertEqual(slots._windows_folder(r'C:\pool\slots'), r'C:\pool\slots')
        with patch.object(slots, '_wsl_path', return_value=r'C:\mapped') as mapped:
            self.assertEqual(slots._windows_folder('/mnt/c/pool'), r'C:\mapped')
            mapped.assert_called_once_with('/mnt/c/pool', '-w')

    def test_wsl_uses_windows_broker_or_fails_closed(self):
        with patch.object(slots, 'is_wsl', return_value=True), patch.object(slots, '_windows_bridge', side_effect=slots.SlotStoreError('no bridge')):
            os.environ.pop('HARNESS_SLOT_DIR')
            with self.assertRaisesRegex(slots.SlotStoreError, 'no bridge'):
                slots.take(False)

    def test_wsl_broker_receives_the_callers_holder_as_one_argument(self):
        folder = r'C:\pool\slots'
        bridge = ['windows-python', 'machine_slots.py']
        with patch.object(slots, 'is_wsl', return_value=True), \
                patch.object(slots, '_windows_bridge', return_value=bridge), \
                patch.object(slots.subprocess, 'Popen') as popen, \
                patch.dict(os.environ, {'CLAUDE_CODE_SIDECAR_LABEL': 'caller with spaces'}):
            proc = popen.return_value
            proc.stdin.closed = False
            proc.stdout.readline.return_value = '{"held": 1}\n'
            held = slots.take(folder=folder, width=1)
        try:
            argv = popen.call_args.args[0]
            self.assertEqual(argv[:-2], bridge + ['broker', '--folder', folder, '--width', '1', '--count', '1'])
            self.assertEqual(argv[-2], '--holder')
            note = json.loads(argv[-1])
            self.assertEqual(note['pid'], os.getpid())
            self.assertEqual(note['label'], 'caller with spaces')
        finally:
            slots.release_all(held)
        proc.stdin.close.assert_called_once()

    def test_busy_count_takes_a_broker_held_lane_that_has_no_path(self):
        # A WSL caller holds its lane through the Windows broker: the handle is a _Proxy with no slot path.
        folder = tempfile.mkdtemp(prefix='busy_lanes_')
        proxy = slots._Proxy(None)
        with patch.object(slots._file_lock, 'acquire', side_effect=lambda path, *_a: 'probe'), \
                patch.object(slots._file_lock, 'release'):
            self.assertEqual(slots.busy_lanes(folder, 3, [proxy]), 1)
        with patch.object(slots._file_lock, 'acquire',
                          side_effect=lambda path, *_a: None if path.endswith('slot-0.lock') else 'probe'), \
                patch.object(slots._file_lock, 'release'):
            self.assertEqual(slots.busy_lanes(folder, 3, [proxy]), 1, 'the probe of its own slot is not a second lane')

    def test_case_pool_uses_one_lane_when_pool_full(self):
        sys.path.insert(0, str(ROOT / '.claude/tests'))
        import _case_pool
        held = slots.take(True)
        try:
            with patch.dict(os.environ, {'HARNESS_SLOT_BUDGET': '1', 'HARNESS_MACHINE_ROOT': self.folder}):
                with _case_pool._budget(4) as width:
                    self.assertEqual(width, 1)
                self.assertEqual(_case_pool._jobs(8), 8, 'requested case width stays an input, not the admitted width')
        finally:
            slots.release_all(held)

    def test_native_delegate_guard_names_slotted_direct_proof_command(self):
        guard = json.loads((ROOT / '.claude/guards/single_flight.json').read_text(encoding='utf-8'))
        self.assertIn('machine_slots.py run --', guard['writer'])

    def test_manual_case_pool_callers_hold_a_budget(self):
        # A proof that sizes its own ThreadPoolExecutor from _case_pool's width must hold the budget for it.
        for path in sorted((ROOT / '.claude/tests').glob('test_*.py')):
            text = path.read_text(encoding='utf-8')
            if path.name != Path(__file__).name and '_case_jobs(' in text and 'ThreadPoolExecutor(' in text:
                with self.subTest(path=path.name):
                    self.assertTrue('with _case_budget(' in text, path.name + ': a requested width is not admission')

    def test_publish_does_not_reserve_machine_for_nested_battery(self):
        import baseline_publish as publish
        import inspect
        source = inspect.getsource(publish._validate)
        self.assertNotIn('publish_slots_', source)
        self.assertNotIn('runner._slot_guard(tasks[index][2])', source)
        self.assertIn('machine_slots', source)

    def test_nested_publish_spends_one_parent_lane(self):
        import baseline_publish as publish
        tree = Path(self.tmp.name) / 'publish'
        (tree / 'tests').mkdir(parents=True)
        for index in range(3):
            (tree / 'tests' / ('test_%d.py' % index)).touch()
        active, peak, labels = 0, 0, []
        lock = threading.Lock()
        def run(*args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.05)
            with lock:
                active -= 1
                labels.append(args[2])
            return 'ok'
        with patch.object(publish, '_run_python_args', run), patch.dict(os.environ, {'HARNESS_SLOT_BUDGET': '1'}):
            publish._validate(tree, [], True)
        self.assertEqual(len(labels), 5)
        self.assertEqual(peak, 1)

    def test_preview_exports_bridge_before_lane_home_changes(self):
        import baseline_linux_preview as preview
        script = preview.preview_script('/mnt/c/source', [('a', [('proof', 'python3 template/.claude/scripts/harness_tests.py')])])
        self.assertNotIn('HARNESS_SLOT_DIR="$R/.slots"', script)
        self.assertIn('HARNESS_WINDOWS_SLOT_SCRIPT', script)
        self.assertLess(script.index('HARNESS_WINDOWS_SLOT_SCRIPT'), script.index('HOME="$D/a.home"'))

    def test_delegate_shell_charges_the_script_it_runs_not_a_test_file_it_names(self):
        """`harness_tests.py --proofs-for tests/test_x.py` is a coordinator, not a proof: a lane would serialize it."""
        sys.path.insert(0, str(ROOT / '.claude/tests'))
        from _sidecar_launch import bash_exe
        tool = Path(self.tmp.name) / 'runner_tool.py'
        tool.write_text("import os;print(os.environ.get('HARNESS_SLOT_BUDGET','missing'),flush=True)\n", encoding='utf-8')
        named = (Path(self.tmp.name) / 'test_named.py').as_posix()
        held = slots.take(True)
        child = subprocess.Popen([bash_exe(), '-c', 'python3 "%s" --proofs-for "%s"' % (tool.as_posix(), named)],
                                 env=slots.proof_environment(os.environ), stdout=subprocess.PIPE, text=True)
        ready, lines = threading.Event(), []
        thread = threading.Thread(target=lambda: (lines.append(child.stdout.readline()), ready.set()))
        thread.start()
        try:
            self.assertTrue(ready.wait(15), 'the coordinator waited for a lane the pool never freed')
            self.assertEqual(lines, ['missing\n'])
        finally:
            slots.release_all(held)
            child.wait(timeout=15)
            child.stdout.close()
            thread.join(15)

    def test_bootstrap_and_read_recorder_start_in_either_order(self):
        """A runner started inside an admitted shell hands each proof the recorder first and the inherited bootstrap
        after it (`run_proof`); the fan-out puts the bootstrap first. Each shadows the other's sitecustomize, so
        either order must start, and the recorder must still log the proof's reads."""
        recorder = str(ROOT / '.claude/scripts/_read_recorder')
        bootstrap = str(ROOT / '.claude/scripts/_slot_bootstrap')
        proof = Path(self.tmp.name) / 'test_order.py'
        proof.write_text('import os;print("started:"+os.environ.get("HARNESS_SLOT_BUDGET","missing"),flush=True)\n', encoding='utf-8')
        # `run_proof` also sets the budget itself: the recorder runs the bootstrap through runpy, which hides the proof's
        # script name from the bootstrap's admission, so a proof started in that order inherits its lane from the runner.
        for order, held in (((recorder, bootstrap), {'HARNESS_SLOT_BUDGET': '1'}), ((bootstrap, recorder), {})):
            with self.subTest(order=[Path(p).name for p in order]):
                log = Path(self.tmp.name) / ('reads_' + Path(order[0]).name)
                env = dict(os.environ, PYTHONPATH=os.pathsep.join(order), HARNESS_READ_LOG=str(log),
                           HARNESS_READ_RECORDER=recorder, CLAUDE_CODE_SIDECAR='1',
                           CLAUDE_CODE_SIDECAR_KIND='review', **held)
                done = subprocess.run([sys.executable, str(proof)], env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, 'started:1\n', ''))
                self.assertIn(str(proof).lower(), ''.join(p.read_text(encoding='utf-8') for p in log.glob('*.jsonl')).lower().replace('\\\\', '\\'))

    def test_bootstrap_leaves_a_proof_its_own_checkouts_harness_modules(self):
        """A delegate's worktree proof runs under the main checkout's bootstrap. The harness modules the bootstrap
        imports to take a lane must not stay cached or ahead on the path, or the proof tests main's copy of them."""
        hooks = Path(self.tmp.name) / 'wt/.claude/hooks'
        hooks.mkdir(parents=True)
        for name in ('_hook_state', '_file_lock'):
            (hooks / (name + '.py')).write_text('MARK = "worktree"\n', encoding='utf-8')
        proof = Path(self.tmp.name) / 'wt/.claude/tests/test_own.py'
        proof.parent.mkdir(parents=True)
        proof.write_text(
            'import sys\nfrom pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))\n'
            'import _hook_state, _file_lock\n'
            'print(getattr(_hook_state, "MARK", "main") + ":" + getattr(_file_lock, "MARK", "main"), flush=True)\n',
            encoding='utf-8')
        env = slots.proof_environment(dict(os.environ))
        done = subprocess.run([sys.executable, str(proof)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout), (0, 'worktree:worktree\n'), done.stderr)

    def _queued_script(self, command, env, expected='1\n'):
        held = slots.take(True)
        child = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, text=True)
        ready, lines = threading.Event(), []
        thread = threading.Thread(target=lambda: (lines.append(child.stdout.readline()), ready.set()))
        thread.start()
        try:
            self.assertFalse(ready.wait(.15))
            slots.release_all(held)
            held = []
            self.assertTrue(ready.wait(15))
            self.assertEqual(lines, [expected])
            self.assertEqual(child.wait(timeout=15), 0)
        finally:
            slots.release_all(held)
            child.wait(timeout=15)
            child.stdout.close()
            thread.join(15)

if __name__ == '__main__':
    unittest.main()
