"""Machine-wide local work admission; coordinators do not hold capacity.

Usage:
  machine_slots.py run -- COMMAND ...   hold one lane while COMMAND runs
  machine_slots.py broker [--holder J]   print a JSON ready line, then hold until stdin closes;
                                        J (a holder note) names the work the broker holds for
  machine_slots.py root                 print the common pool path

Waiters take a ticket in queue/ and only the head live one claims lanes: the lowest ticket name,
whole-machine tickets first. A shared ticket's name leads with a key (see `_ticket_name`) that is
first come, first served per caller (the pid in the name) and round-robin across callers: a
caller's n-th queued ticket sorts behind every other caller's first n-1, so a battery's worker
tickets cannot make a lone run wait behind all of them, and a releaser that re-takes at once is
its caller's last ticket, so it cannot starve a waiting proof. WSL uses a
Windows broker: flock and Windows delete-sharing are not one lock protocol.
HARNESS_SLOT_DIR isolates planted pools; HARNESS_MACHINE_ROOT preserves the real
pool across proof HOME/temp changes. HARNESS_SLOT_BUDGET loans one held lane to a
serial child. parallel_budget borrows only free extra lanes, never waits for them.
`run` exports HARNESS_LANE_CORES, the held lanes' share of the machine's cores.
Every take leaves its lease in the pool's leases.jsonl, which `lease_log.py` owns and queries.
"""
import argparse
from contextlib import contextmanager
import itertools
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

_CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CLAUDE / 'hooks'))
import _file_lock
from _msys import resolve_argv
from _hook_state import SUBAGENT_CACHE_WARM_SECONDS, read_json_salvage, update_json_locked
import lease_log


class SlotStoreError(OSError):
    """Admission is unavailable; running uncharged would break the machine limit."""


def is_wsl():
    return sys.platform == 'linux' and 'microsoft' in platform.release().lower()


def inherited():
    return os.environ.get('HARNESS_SLOT_BUDGET') == '1'


def _windows_bridge():
    python = os.environ.get('HARNESS_WINDOWS_PYTHON') or shutil.which('python.exe') or shutil.which('python3.exe')
    if not python:
        raise SlotStoreError('WSL admission needs Windows Python interop (HARNESS_WINDOWS_PYTHON)')
    script = os.environ.get('HARNESS_WINDOWS_SLOT_SCRIPT')
    if not script:
        try:
            script = subprocess.check_output(['wslpath', '-w', str(Path(__file__).resolve())], text=True).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SlotStoreError('WSL cannot locate the Windows slot broker: %s' % exc) from exc
    return [python, script]


def _wsl_path(path, direction):
    try:
        return subprocess.check_output(['wslpath', direction, path], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SlotStoreError('WSL cannot map the common slot pool: %s' % exc) from exc


def _windows_folder(folder):
    """The broker's Windows path; a path already Windows-style passes unchanged (wslpath -w mangles it)."""
    return folder if ':' in folder else _wsl_path(folder, '-w')


def slot_dir():
    explicit = os.environ.get('HARNESS_SLOT_DIR') or os.environ.get('HARNESS_MACHINE_ROOT')
    if explicit:
        return explicit
    if os.name == 'nt':
        home = os.environ.get('USERPROFILE')
        if not home:
            raise SlotStoreError('USERPROFILE is missing; refusing a private machine pool')
    elif is_wsl():
        try:
            home = json.loads(subprocess.check_output(_windows_bridge() + ['root'], text=True))
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            raise SlotStoreError('Windows machine pool unavailable: %s' % exc) from exc
        return _wsl_path(home, '-u')
    else:
        home = os.path.expanduser('~')
    return os.path.join(home, '.claude', '.harness_slots')


def machine_root():
    return os.environ.get('HARNESS_MACHINE_ROOT') or slot_dir()


def capacity(folder=None):
    try:
        value = int(os.environ.get('HARNESS_MACHINE_SLOTS', ''))
    except ValueError:
        value = -1
    if value >= 0:
        return value
    path = os.path.join(folder or slot_dir(), 'width.json')
    recorded = read_json_salvage(path).get('width') if os.path.exists(path) else None
    return recorded if type(recorded) is int and recorded > 0 else 4


def lane_cores(lanes, width, busy=None):
    """The cores `lanes` held lanes may use: the machine's cores split over the `busy` lanes held at admission
    (the whole `width` when unknown), at least one. `run` exports it as HARNESS_LANE_CORES, so a runner that would
    take every core (a test worker pool, a build's job count) takes its share instead. Splitting over the whole
    width starved a lone suite of idle cores (2 of 24 ran it at half speed); a later arrival gets a smaller share,
    so staggered starts oversubscribe at most the harmonic sum of the width."""
    cores = os.cpu_count() or 1
    if width <= 0:
        return cores
    return max(1, cores * lanes // max(lanes, min(width, busy if busy is not None else width)))


def busy_lanes(folder, width, held):
    """The lanes held now: the caller's `held` plus each other slot whose lock a probe cannot take. A lane held
    through the Windows broker has no slot path, and its own slot probes busy, so it is counted once, with the probes."""
    named = {handle.path for handle in held if hasattr(handle, 'path')}
    unnamed = len(held) - len(named)
    probed_busy = 0
    for index in range(width):
        path = os.path.join(folder, 'slot-%d.lock' % index)
        if path in named:
            continue
        probe = _file_lock.acquire(path, 0)
        if probe:
            _file_lock.release(probe)
        else:
            probed_busy += 1
    return len(named) + max(unnamed, probed_busy)


SATURATED = 0.90      # a CPU share at or above this leaves a proof no room: its timeout is the machine's, not its own
MIN_WINDOW_S = 1.0    # a shorter window reads the counter's granularity, not the load


def cpu_read():
    """The machine's cumulative `(busy, total)` CPU time, or None where the platform exposes no counter. Lane
    admission bounds only lane work; a pytest run, a model server or a peer's build takes cores no lane counts."""
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            ticks = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime  # noqa: E731
            total = ticks(kernel) + ticks(user)  # kernel time includes idle
            return total - ticks(idle), total
        with open('/proc/stat', encoding='ascii') as fh:
            fields = [int(part) for part in fh.readline().split()[1:9]]
        return sum(fields) - fields[3] - fields[4], sum(fields)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class Room:
    """Whether the machine had room for the work that just ran: its CPU share between `mark()` and `busy(mark)`.
    `read`, `clock` and `sleep` are planted by a proof; an unreadable machine answers None, never saturated."""

    def __init__(self, read=None, clock=time.monotonic, sleep=time.sleep):
        self._read, self._clock, self._sleep = read or cpu_read, clock, sleep

    def mark(self):
        return self._clock(), self._read()

    def busy(self, mark):
        """The CPU share of the whole machine since `mark`, or None (unreadable, or shorter than MIN_WINDOW_S)."""
        began, before = mark
        now, after = self._clock(), self._read()
        if before is None or after is None or now - began < MIN_WINDOW_S or after[1] <= before[1]:
            return None
        return (after[0] - before[0]) / (after[1] - before[1])

    def wait(self, limit_s, quiet_s=15.0):
        """Block until one `quiet_s` window runs below SATURATED, for at most `limit_s`: `(seconds waited, whether
        room came)`. A machine that cannot be read has room by definition, so a wait never holds on it."""
        if limit_s <= 0:
            return 0.0, False
        began = self._clock()
        while self._clock() - began < limit_s:
            mark = self.mark()
            self._sleep(quiet_s)
            busy = self.busy(mark)
            if busy is None or busy < SATURATED:
                return self._clock() - began, True
        return self._clock() - began, False


def claim_width(folder, count):
    """Called under drain.lock: adopt the held width; change it only with no live lane."""
    path = os.path.join(folder, 'width.json')
    old = read_json_salvage(path).get('width') if os.path.exists(path) else None
    if old == count:
        return count
    if type(old) is int and old > 0:
        for index in range(max(old, count)):
            handle = _file_lock.acquire(os.path.join(folder, 'slot-%d.lock' % index), 0)
            if not handle:
                return old
            _file_lock.release(handle)
    try:
        update_json_locked(path, lambda state: state.update(width=count), raising=True)
    except (OSError, TimeoutError) as exc:
        raise SlotStoreError('machine slot width cannot be recorded in %s (%s)' % (folder, exc)) from exc
    return count


class _Proxy:
    def __init__(self, proc):
        self.proc = proc


def release(handle):
    if isinstance(handle, _Proxy):
        if not handle.proc.stdin.closed:
            handle.proc.stdin.close()
        handle.proc.wait()
        handle.proc.stdout.close()
    else:
        _file_lock.release(handle)


def release_all(handles):
    for handle in handles or ():
        release(handle)
    if isinstance(handles, _Lease) and handles.row is not None:
        lease_log.append(handles.path, dict(handles.row, event='end', at=lease_log.stamp(), ended='released'))
        handles.row = None
        handles.clear()


class _Lease(list):
    """The lanes one take holds, with its history row: `release_all` writes the matching end row once."""

    def __init__(self, handles, path, row):
        super().__init__(handles)
        self.path, self.row = path, row


def _proxy_take(exclusive, count, folder, width, deadline, notice, holder=None):
    """The Windows broker's take. `holder` is this side's note: the broker writes it into the lane it holds, so the
    lock names the work the WSL caller runs, not only the broker process."""
    command = _windows_bridge() + ['broker', '--folder', _windows_folder(folder), '--width', str(width), '--count', str(count)]
    if exclusive:
        command.append('--exclusive')
    if notice:
        command.append('--notice')
    if holder:
        command += ['--holder', holder]
    if deadline is not None:
        command += ['--deadline', str(deadline)]
    env = dict(os.environ)
    env.pop('HARNESS_SLOT_BUDGET', None)
    # Only Windows paths reach the Windows broker. Its own default never depends on WSL HOME.
    env.pop('HARNESS_SLOT_DIR', None)
    env.pop('HARNESS_MACHINE_ROOT', None)
    proc = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        answer = json.loads(proc.stdout.readline())
        if 'error' in answer:
            raise SlotStoreError(answer['error'])
        if answer.get('held') is None:
            proc.stdin.close()
            proc.wait()
            proc.stdout.close()
            return None
        return [_Proxy(proc)]
    except (OSError, ValueError) as exc:
        proc.stdin.close()
        proc.wait()
        proc.stdout.close()
        raise SlotStoreError('Windows slot broker failed: %s' % exc) from exc


NOTICE_AFTER = 30
# A dispatched job's one bounded call: the wait guard clamps each of its waits to this same prompt-cache bound.
JOB_WAIT_S = SUBAGENT_CACHE_WARM_SECONDS


def job_deadline():
    """The deadline of a take made by a caller that may not start a wide run (a dispatched non-gate sidecar job,
    `_wide_runs.gate_caller`), else None. The job cannot move a full pool, and each wait re-reads its context; its
    dispatcher's watcher wakes it, so the run is the dispatcher's."""
    from _wide_runs import gate_caller
    return None if gate_caller({}, os.environ) else time.time() + JOB_WAIT_S


def owed_line(command):
    return ('machine slots: the pool stayed full for %ds, so this job did not run: %s\n'
            'Return now and list that run as owed: your dispatcher must run it before it reads your work as done.'
            % (JOB_WAIT_S, ' '.join(command)))


def admit(command):
    """One lane for `command`'s own process, or None once a dispatched job's bounded take has printed the owed line."""
    deadline = job_deadline()
    held = take(notice=True, deadline=deadline)
    if held is None:
        print(owed_line(command), file=sys.stderr, flush=True)
    return held


def take(exclusive=False, count=1, *, folder=None, width=None, deadline=None, inherit=True, notice=False,
         split=None, holder=None):
    """`notice` prints a stderr line while queued, so a waiting proof never reads as a hang. `split`, a dict,
    receives the seconds this take spent `behind` an older ticket, `drain` (head, another process holds
    drain.lock) and `collect` (head, holding drain.lock, slots short)."""
    if inherit and inherited():
        return []
    folder = folder or slot_dir()
    width = capacity(folder) if width is None else width
    if width == 0:
        return []
    # Native /tmp pools are planted fixtures. The WSL default and mounted Windows pools use Windows handles.
    if is_wsl() and (folder.startswith('/mnt/') or ':' in folder):
        return _proxy_take(exclusive, count, folder, width, deadline, notice, holder=holder or _holder_note())
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise SlotStoreError('machine slot directory unusable: %s (%s)' % (folder, exc)) from exc
    note = holder or _holder_note()
    split = {} if split is None else split
    began = time.time()
    held = _queue_take(exclusive, count, folder, width, deadline, notice, split, note)
    return _record(folder, note, exclusive, count, width, began, split, held)


def _record(folder, note, exclusive, count, width, began, split, held):
    """The take's history row (`lease_log`): a held take comes back as a `_Lease` whose release writes its end row; a
    missed deadline writes its end row now. A deadline already due on entry still writes one: a broker's deadline can
    pass during its own start-up, after its caller waited."""
    try:
        who = json.loads(note)
    except ValueError:
        who = None
    if not isinstance(who, dict):
        who = {'command': str(note)}
    row = {'v': 1, 'id': uuid.uuid4().hex[:12], 'pool': 'lane', 'kind': 'lane', 'pid': who.get('pid'),
           'caller_pid': who.get('caller_pid'), 'label': who.get('label') or '', 'command': who.get('command') or '',
           'checkout': who.get('checkout') or '', 'start': lease_log.stamp(began),
           'ask': {'lanes': 'all' if exclusive else count, 'width': width}, 'wait': {'lane': round(time.time() - began, 3)},
           'split': {phase: round(secs, 3) for phase, secs in split.items()}}
    path = os.path.join(folder, 'leases.jsonl')
    if held is None:
        lease_log.append(path, dict(row, event='end', at=lease_log.stamp(), ended='deadline', got={'lanes': []}))
        return None
    lanes = sorted(int(m.group(1)) for m in (re.search(r'slot-(\d+)\.lock$', h.path) for h in held) if m)
    row = dict(row, event='take', at=lease_log.stamp(), got={'lanes': lanes})
    lease_log.append(path, row)
    return _Lease(held, path, row)


def _queue_take(exclusive, count, folder, width, deadline, notice, split, note):
    """`take`'s queue: the lanes once this ticket is the live head and collects them, or None at the deadline."""
    held = []
    ticket = drain = None
    try:
        # First come, first served: only the oldest live ticket scans the slots, so a releaser that
        # re-takes at once, or a case pool borrowing extras, never passes a waiting proof.
        queue = os.path.join(folder, 'queue')
        os.makedirs(queue, exist_ok=True)
        with _TICKET_LOCK:
            name = _ticket_name(queue, exclusive)
            ticket = _file_lock.acquire(os.path.join(queue, name), 0)
        if ticket is None:
            raise SlotStoreError('machine admission queue unusable: %s' % queue)
        began = time.monotonic()
        next_notice = began + NOTICE_AFTER
        phase, since = 'behind', began
        while True:
            now = time.monotonic()
            split[phase] = split.get(phase, 0.0) + now - since
            since = now
            phase = 'behind'
            if _live_ticket_before(queue, name):
                release_all(held)
                held = []
                _file_lock.release(drain)
                drain = None
            else:
                phase = 'drain'
                # The head keeps drain.lock and each slot it collects until it holds all it wants, or its deadline
                # passes and `finally` returns them. A harness without the queue (another project's, an old
                # checkout's) claims any free slot while drain.lock is free, so a slot the head let go between
                # scans would go to it, and an exclusive head would wait for as long as that harness runs.
                # A head that an exclusive ticket overtakes returns both above: the new head collects under
                # drain.lock, so one kept here stalls the whole queue while every slot stays free.
                drain = drain or _file_lock.acquire(os.path.join(folder, 'drain.lock'), 0)
                if drain:
                    phase = 'collect'
                    active_width = claim_width(folder, width)
                    wanted = active_width if exclusive else max(1, min(count, active_width))
                    mine = {handle.path for handle in held}
                    for index in range(active_width):
                        if len(held) >= wanted:
                            break
                        path = os.path.join(folder, 'slot-%d.lock' % index)
                        handle = None if path in mine else _file_lock.acquire(path, 0, note)
                        if handle:
                            held.append(handle)
                    if len(held) >= wanted:
                        result, held = held, []
                        return result
            if deadline is not None and time.time() >= deadline:
                return None
            if notice and time.monotonic() >= next_notice:
                print('machine slots: queued %ds, phase %s, holding %d slots (%s)%s' % (
                    time.monotonic() - began, phase, len(held), folder, _holders(folder, held)), file=sys.stderr, flush=True)
                next_notice += 60
            time.sleep(.05)
    except (OSError, TimeoutError) as exc:
        raise SlotStoreError('machine admission failed: %s' % exc) from exc
    finally:
        release_all(held)
        _file_lock.release(drain)
        _file_lock.release(ticket)


def _holder_note():
    """What this taker can say about itself, written into every lane it takes: its pid, the command it runs, its
    label (a sidecar job's) and its checkout, so a queued notice names the holder and not only a pid."""
    command = ' '.join(sys.argv)
    if len(command) > 400:
        command = command[:397] + '...'
    try:
        checkout = os.getcwd()
    except OSError:
        checkout = ''
    note = {'pid': os.getpid(), 'command': command, 'checkout': checkout}
    label = os.environ.get('CLAUDE_CODE_SIDECAR_LABEL') or os.environ.get('SC_LABEL')
    if label:
        note['label'] = label
    return json.dumps(note)


def _holder_of(text):
    """`(pid, label, command, checkout)` a lock file names: its taker's note, or a bare pid from an older runner."""
    try:
        note = json.loads(text)
    except ValueError:
        note = None
    if not isinstance(note, dict):
        return text, '', '', ''
    return (str(note.get('pid') or ''), str(note.get('label') or ''),
            str(note.get('command') or ''), str(note.get('checkout') or ''))


def _holders(folder, mine):
    """`; held by pid N (k slots) <label> <command> in <checkout>, ...` over the slots another process holds, read
    from their lock files."""
    ours = {handle.path for handle in mine}
    counts = {}
    for entry in sorted(os.listdir(folder)):
        path = os.path.join(folder, entry)
        if entry.startswith('slot-') and path not in ours:
            try:
                with open(path, encoding='utf-8', errors='replace') as fh:
                    holder = _holder_of(fh.read().strip())
            except OSError:
                continue
            if holder[0]:
                counts[holder] = counts.get(holder, 0) + 1
    return ''.join('; held by pid %s (%d)%s%s' % (
        pid, count, ' ' + ' '.join(part for part in (label, command) if part) if label or command else '',
        ' in ' + checkout if checkout else '')
        for (pid, label, command, checkout), count in sorted(counts.items()))


_TICKETS = itertools.count()
_TICKET_LOCK = threading.Lock()
_EXCLUSIVE = 'x'
# Key distance between one caller's consecutive queued tickets; tickets of different callers in one round sit
# within a nanosecond of each other, so any positive spacing separates rounds.
_ROUND_NS = 1_000_000


def _ticket_name(queue, exclusive, pid=None):
    """`<key>-<pid>-<n>-q[x].lock`: tickets are served in name order, and the name is fixed at enqueue, so every
    harness version that sorts names agrees on the order (a rank re-derived from the live queue would let an
    older checkout and this one each wait on the other). A whole-machine ticket's key is its arrival time. A
    shared ticket's key is the queue's head key plus one (just behind the ticket being served) or, when its own
    caller already queues, that caller's newest key plus `_ROUND_NS`. A caller's n-th queued ticket is therefore
    in round n and sorts behind every other caller's first n-1, and a caller that arrives later joins at the
    head's round, not behind every older ticket: a lone caller waits for at most one ticket per other caller.
    A ticket from a checkout without rounds (three name fields, no `-q`) keeps arrival order and sorts before
    every ticket made here, so a mixed pool never serves an old checkout after a newer one. Callers are
    processes (one battery's workers share a pid); `pid` names another's in a proof. The caller holds
    `_TICKET_LOCK`, so a process's simultaneous tickets see each other."""
    pid = os.getpid() if pid is None else pid
    key = time.time_ns()
    if not exclusive:
        shared = [t.split('-') for t in os.listdir(queue) if not t.endswith(_EXCLUSIVE + '.lock')]
        shared = [(int(parts[0]), parts[1], len(parts) == 3) for parts in shared if len(parts) > 2 and parts[0].isdigit()]
        floors = [min(k for k, _, _ in shared) + 1] if shared else [key]
        floors += [k + 1 for k, _, old in shared if old]
        floors += [k + _ROUND_NS for k, owner, _ in shared if owner == str(pid)]
        key = max(floors)
    return '%020d-%d-%d-q%s.lock' % (key, pid, next(_TICKETS), _EXCLUSIVE if exclusive else '')


def _live_ticket_before(queue, name):
    """True while another waiter is ahead of `name`. Tickets are served in name order (`_ticket_name`), except
    that exclusive tickets (`_EXCLUSIVE` suffix) go first, among themselves in arrival order: each whole-machine take that
    follows shared work pays one drain of the longest running lane, so exclusive takers queued together pay
    it once between them. A ticket whose owner died is reclaimed by the probe."""
    mine = name.endswith(_EXCLUSIVE + '.lock')
    for other in sorted(os.listdir(queue)):
        if other == name:
            continue
        ahead = (not mine or other < name) if other.endswith(_EXCLUSIVE + '.lock') else (not mine and other < name)
        if not ahead:
            continue
        probe = _file_lock.acquire(os.path.join(queue, other), 0)
        if not probe:
            return True
        _file_lock.release(probe)
    return False


@contextmanager
def guard(exclusive=False, count=1, *, notice=False):
    deadline = os.environ.get('HARNESS_DEADLINE')
    held = take(exclusive, count, deadline=float(deadline) if deadline else None, notice=notice)
    if held is None:
        raise SlotStoreError('deadline passed before the machine slots were free')
    try:
        yield held
    finally:
        release_all(held)


@contextmanager
def parallel_budget(wanted):
    """Base lane plus free extras; children are serial owners of their assigned lane."""
    base = [] if inherited() else take(notice=True)
    if base is None:
        raise SlotStoreError('machine admission unavailable')
    extras = []
    old = os.environ.get('HARNESS_SLOT_BUDGET')
    root = machine_root()
    try:
        width = 1
        for _ in range(max(1, wanted) - 1):
            held = take(folder=root, deadline=time.time(), inherit=False)
            if not held:
                break
            extras.extend(held)
            width += 1
        os.environ['HARNESS_SLOT_BUDGET'] = '1'
        yield width
    finally:
        if old is None:
            os.environ.pop('HARNESS_SLOT_BUDGET', None)
        else:
            os.environ['HARNESS_SLOT_BUDGET'] = old
        release_all(extras)
        release_all(base)


def proof_environment(env):
    """The pool root a remote-work fan-out's shells and proofs share; no shell is wrapped in a lane."""
    env = dict(env)
    env.setdefault('HARNESS_MACHINE_ROOT', machine_root())
    return env


def _command_name(value):
    return value.replace('\\', '/').rsplit('/', 1)[-1].lower()


def _no_lane(command):
    """Skip admission for pytest and for the legacy coordinator, never for an argument that merely names one."""
    exe = _command_name(command[0]).removesuffix('.exe')
    if exe in ('pytest', 'py.test'):
        return True
    python = re.fullmatch(r'py|python(?:\d+(?:\.\d+)?)?', exe) is not None
    for i, arg in enumerate(command[1:], 1):
        if arg == '-m':
            return python and command[i + 1:i + 2] == ['pytest']
        if arg in ('-c', '-Command', '-e', '--eval'):
            return False
        if arg.startswith('-'):
            continue
        return _command_name(arg) == 'harness_tests.py'
    return False


def _broker_holder(given):
    """`given`'s holder note as the broker must write it: the work it holds for, with the broker's own pid (the
    process the lane belongs to) and the caller's kept as `caller_pid`."""
    if not given:
        return None
    try:
        note = json.loads(given)
    except ValueError:
        note = None
    if not isinstance(note, dict):
        note = {'command': str(given)}
    caller = note.get('pid')
    note['pid'] = os.getpid()
    if caller and str(caller) != str(note['pid']):
        note['caller_pid'] = str(caller)
    return json.dumps(note)


def main():
    from cli_usage import usage
    parser = argparse.ArgumentParser(description=usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('root', 'broker', 'run'))
    parser.add_argument('--folder')
    parser.add_argument('--width', type=int)
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--exclusive', action='store_true')
    parser.add_argument('--deadline', type=float)
    parser.add_argument('--notice', action='store_true')
    parser.add_argument('--holder', help='a holder note (JSON) naming the work a broker holds for')
    args, command = parser.parse_known_args()
    if args.mode == 'root':
        print(json.dumps(slot_dir()), flush=True)
        return 0
    if command and command[0] == '--':
        command = command[1:]
    if args.mode == 'run' and not command:
        parser.error('run needs -- COMMAND ...')
    if args.mode == 'run':
        command, problem = resolve_argv(command)
        if problem:
            print(problem, file=sys.stderr, flush=True)
            return 2
    if args.mode == 'run' and _no_lane(command):
        return subprocess.call(command)
    held = None
    deadline = args.deadline
    bounded = None
    if deadline is None and args.mode == 'run':
        deadline = bounded = job_deadline()
    try:
        held = take(args.exclusive, args.count, folder=args.folder, width=args.width, deadline=deadline,
                    notice=args.notice or args.mode == 'run', holder=_broker_holder(args.holder))
        if held is None and bounded:
            print(owed_line(command), file=sys.stderr, flush=True)
            return 2
        if args.mode == 'broker':
            print(json.dumps({'held': None if held is None else (args.width or capacity()) if args.exclusive else args.count}), flush=True)
            if held is not None:
                sys.stdin.read()
            return 0 if held is not None else 2
        if held is None:
            raise SlotStoreError('deadline passed before admission')
        width = args.width or capacity(args.folder)
        lanes = width if args.exclusive else args.count
        busy = busy_lanes(args.folder or slot_dir(), width, held) if held else None
        env = dict(os.environ, HARNESS_SLOT_BUDGET='1', HARNESS_MACHINE_ROOT=machine_root(),
                   HARNESS_LANE_CORES=str(lane_cores(lanes, width, busy)))
        return subprocess.call(command, env=env)
    except SlotStoreError as exc:
        print(json.dumps({'error': str(exc)}) if args.mode == 'broker' else str(exc), flush=True)
        return 2
    finally:
        release_all(held)


if __name__ == '__main__':
    sys.exit(main())
