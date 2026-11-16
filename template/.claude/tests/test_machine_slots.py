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

    def test_windows_root_ignores_proof_home(self):
        with patch.object(slots.os, 'name', 'nt'), patch.dict(os.environ, {'USERPROFILE': 'C:/Users/worker', 'HOME': 'X:/fixture'}):
            os.environ.pop('HARNESS_SLOT_DIR')
            self.assertEqual(slots.slot_dir().replace('\\', '/'), 'C:/Users/worker/.claude/.harness_slots')

    def test_count_take_never_hoards_partial_capacity(self):
        first = slots.take(False)
        try:
            self.assertIsNone(slots.take(False, 2, deadline=time.time()))
            second = slots.take(False, deadline=time.time())
            self.assertIsNotNone(second, 'failed two-slot admission must return its partial hold')
            slots.release_all(second)
        finally:
            slots.release_all(first)

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
                err.truncate(0)
                self.assertIsNone(slots.take(False, deadline=time.time() + .3))
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
        self.assertIn('machine_slots.py run --', guard['withShell'])

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
            publish._validate(tree, [], False)
        self.assertEqual(len(labels), 5)
        self.assertEqual(peak, 1)

    def test_preview_exports_bridge_before_lane_home_changes(self):
        import baseline_linux_preview as preview
        script = preview.preview_script('/mnt/c/source', [('a', [('proof', 'python3 template/.claude/scripts/harness_tests.py')])])
        self.assertNotIn('HARNESS_SLOT_DIR="$R/.slots"', script)
        self.assertIn('HARNESS_WINDOWS_SLOT_SCRIPT', script)
        self.assertLess(script.index('HARNESS_WINDOWS_SLOT_SCRIPT'), script.index('HOME="$D/a.home"'))

    def test_delegate_shell_and_powershell_proofs_queue(self):
        sys.path.insert(0, str(ROOT / '.claude/tests'))
        from _sidecar_launch import bash_exe
        shell = Path(self.tmp.name) / 'test_probe.sh'
        shell.write_text('printf "%s\\n" "${HARNESS_SLOT_BUDGET:-missing}"\n', encoding='utf-8')
        command = [bash_exe(), str(shell)]
        self._queued_script(command, slots.proof_environment(os.environ))
        import shutil
        if shutil.which('pwsh'):
            proof = Path(self.tmp.name) / 'test_probe.ps1'
            proof.write_text('[Console]::WriteLine($env:HARNESS_SLOT_BUDGET)\n', encoding='utf-8')
            text = 'pwsh -NoProfile -File "' + proof.as_posix() + '"'
            self._queued_script([bash_exe(), '-c', text], slots.proof_environment(os.environ))

    def test_a_shell_whose_bash_env_is_another_checkouts_bootstrap_still_queues(self):
        """The publish battery runs proofs in a second checkout under its own bootstrap: chaining that copy as the
        previous BASH_ENV made it source itself until the shell crashed (0xC0000005, no output)."""
        sys.path.insert(0, str(ROOT / '.claude/tests'))
        from _sidecar_launch import bash_exe
        import shutil
        foreign = Path(self.tmp.name) / 'other_checkout' / 'bash_slots.sh'
        foreign.parent.mkdir()
        shutil.copy(ROOT / '.claude/scripts/_slot_bootstrap/bash_slots.sh', foreign)
        shell = Path(self.tmp.name) / 'test_probe.sh'
        shell.write_text('printf "%s\\n" "${HARNESS_SLOT_BUDGET:-missing}"\n', encoding='utf-8')
        with patch.dict(os.environ, {'BASH_ENV': foreign.as_posix()}):
            env = slots.proof_environment(os.environ)
        self._queued_script([bash_exe(), str(shell)], env)

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

    def test_delegate_node_proof_queues(self):
        import shutil
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        proof = Path(self.tmp.name) / 'probe_test.js'
        proof.write_text('console.log(process.env.HARNESS_SLOT_BUDGET || "missing");\n', encoding='utf-8')
        self._queued_script([node, str(proof)], slots.proof_environment(os.environ))

    def test_bootstrap_preserves_existing_python_startup(self):
        prior = Path(self.tmp.name) / 'prior'
        prior.mkdir()
        (prior / 'sitecustomize.py').write_text('import os;os.environ["PRIOR_STARTUP"]="seen"\n', encoding='utf-8')
        proof = Path(self.tmp.name) / 'test_startup.py'
        proof.write_text('import os;print(os.environ.get("HARNESS_SLOT_BUDGET", "missing")+":"+os.environ.get("PRIOR_STARTUP","missing"),flush=True)\n', encoding='utf-8')
        env = slots.proof_environment(dict(os.environ, PYTHONPATH=str(prior)))
        self._queued_script([sys.executable, str(proof)], env, '1:seen\n')

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
                           HARNESS_READ_RECORDER=recorder, **held)
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

    def test_fanout_bootstrap_charges_a_direct_proof(self):
        proof = Path(self.tmp.name) / 'test_probe.py'
        proof.write_text("import os;print(os.environ.get('HARNESS_SLOT_BUDGET','missing'),flush=True)\n", encoding='utf-8')
        env = slots.proof_environment(dict(os.environ))
        env['HARNESS_MACHINE_SLOTS'] = '1'
        held = slots.take(True)
        child = subprocess.Popen([sys.executable, str(proof)], env=env, stdout=subprocess.PIPE, text=True)
        ready, lines = threading.Event(), []
        thread = threading.Thread(target=lambda: (lines.append(child.stdout.readline()), ready.set()))
        thread.start()
        try:
            self.assertFalse(ready.wait(.15))
            slots.release_all(held)
            held = []
            self.assertTrue(ready.wait(10))
            self.assertEqual(lines, ['1\n'])
            self.assertEqual(child.wait(timeout=10), 0)
        finally:
            slots.release_all(held)
            child.wait(timeout=10)
            child.stdout.close()
            thread.join(10)


if __name__ == '__main__':
    unittest.main()
