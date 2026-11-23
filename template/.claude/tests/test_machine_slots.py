"""The shared work budget: admission, loans, dead owners and caller wiring."""
import contextlib
import importlib.util
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
            if queue.is_dir() and len(list(queue.iterdir())) >= count:
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
