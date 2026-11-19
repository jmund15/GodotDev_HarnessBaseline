"""Machine-wide local work admission; coordinators do not hold capacity.

Usage:
  machine_slots.py run -- COMMAND ...   hold one lane while COMMAND runs
  machine_slots.py broker               print a JSON ready line, then hold until stdin closes
  machine_slots.py root                 print the common pool path

Waiters take a ticket in queue/ and only the oldest live one claims lanes (first come,
first served: a releaser that re-takes at once cannot starve a waiting proof). WSL uses a
Windows broker: flock and Windows delete-sharing are not one lock protocol.
HARNESS_SLOT_DIR isolates planted pools; HARNESS_MACHINE_ROOT preserves the real
pool across proof HOME/temp changes. HARNESS_SLOT_BUDGET loans one held lane to a
serial child. parallel_budget borrows only free extra lanes, never waits for them.
`run` exports HARNESS_LANE_CORES, the held lanes' share of the machine's cores.
"""
import argparse
from contextlib import contextmanager
import itertools
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

_CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CLAUDE / 'hooks'))
import _file_lock
from _hook_state import read_json_salvage, update_json_locked


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
    """The lanes held now: the caller's `held` plus each other slot whose lock a probe cannot take."""
    mine = {handle.path for handle in held}
    busy = len(held)
    for index in range(width):
        path = os.path.join(folder, 'slot-%d.lock' % index)
        if path in mine:
            continue
        probe = _file_lock.acquire(path, 0)
        if probe:
            _file_lock.release(probe)
        else:
            busy += 1
    return busy


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


def _proxy_take(exclusive, count, folder, width, deadline, notice):
    command = _windows_bridge() + ['broker', '--folder', _windows_folder(folder), '--width', str(width), '--count', str(count)]
    if exclusive:
        command.append('--exclusive')
    if notice:
        command.append('--notice')
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


def take(exclusive=False, count=1, *, folder=None, width=None, deadline=None, inherit=True, notice=False):
    """`notice` prints a stderr line while queued, so a waiting proof never reads as a hang."""
    if inherit and inherited():
        return []
    folder = folder or slot_dir()
    width = capacity(folder) if width is None else width
    if width == 0:
        return []
    # Native /tmp pools are planted fixtures. The WSL default and mounted Windows pools use Windows handles.
    if is_wsl() and (folder.startswith('/mnt/') or ':' in folder):
        return _proxy_take(exclusive, count, folder, width, deadline, notice)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise SlotStoreError('machine slot directory unusable: %s (%s)' % (folder, exc)) from exc
    held = []
    ticket = drain = None
    try:
        # First come, first served: only the oldest live ticket scans the slots, so a releaser that
        # re-takes at once, or a case pool borrowing extras, never passes a waiting proof.
        queue = os.path.join(folder, 'queue')
        name = '%020d-%d-%d.lock' % (time.time_ns(), os.getpid(), next(_TICKETS))
        ticket = _file_lock.acquire(os.path.join(queue, name), 0)
        if ticket is None:
            raise SlotStoreError('machine admission queue unusable: %s' % queue)
        began = time.monotonic()
        next_notice = began + NOTICE_AFTER
        while True:
            if not _live_ticket_before(queue, name):
                # The head keeps drain.lock and each slot it collects until it holds all it wants, or its deadline
                # passes and `finally` returns them. A harness without the queue (another project's, an old
                # checkout's) claims any free slot while drain.lock is free, so a slot the head let go between
                # scans would go to it, and an exclusive head would wait for as long as that harness runs.
                drain = drain or _file_lock.acquire(os.path.join(folder, 'drain.lock'), 0)
                if drain:
                    active_width = claim_width(folder, width)
                    wanted = active_width if exclusive else max(1, min(count, active_width))
                    mine = {handle.path for handle in held}
                    for index in range(active_width):
                        if len(held) >= wanted:
                            break
                        path = os.path.join(folder, 'slot-%d.lock' % index)
                        handle = None if path in mine else _file_lock.acquire(path, 0)
                        if handle:
                            held.append(handle)
                    if len(held) >= wanted:
                        result, held = held, []
                        return result
            if deadline is not None and time.time() >= deadline:
                return None
            if notice and time.monotonic() >= next_notice:
                print('machine slots: queued %ds behind the full pool (%s)%s' % (
                    time.monotonic() - began, folder, _holders(folder, held)), file=sys.stderr, flush=True)
                next_notice += 60
            time.sleep(.05)
    except (OSError, TimeoutError) as exc:
        raise SlotStoreError('machine admission failed: %s' % exc) from exc
    finally:
        release_all(held)
        _file_lock.release(drain)
        _file_lock.release(ticket)


def _holders(folder, mine):
    """`; held by pid N (k slots), ...` over the slots another process holds, read from their lock files."""
    ours = {handle.path for handle in mine}
    counts = {}
    for entry in sorted(os.listdir(folder)):
        path = os.path.join(folder, entry)
        if entry.startswith('slot-') and path not in ours:
            try:
                with open(path, encoding='ascii') as fh:
                    pid = fh.read().strip()
            except (OSError, ValueError):
                continue
            counts[pid] = counts.get(pid, 0) + 1
    return ''.join('; held by pid %s (%d)' % item for item in sorted(counts.items()) if item[0])


_TICKETS = itertools.count()


def _live_ticket_before(queue, name):
    """True while an older waiter is alive. A ticket whose owner died is reclaimed by the probe."""
    for other in sorted(os.listdir(queue)):
        if other >= name:
            return False
        probe = _file_lock.acquire(os.path.join(queue, other), 0)
        if not probe:
            return True
        _file_lock.release(probe)
    return False


@contextmanager
def guard(exclusive=False, count=1):
    deadline = os.environ.get('HARNESS_DEADLINE')
    held = take(exclusive, count, deadline=float(deadline) if deadline else None)
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
    """Admission for direct Python/Node proofs launched by a remote-work fan-out."""
    env = dict(env)
    bootstrap = str(_CLAUDE / 'scripts' / '_slot_bootstrap')
    paths = [p for p in env.get('PYTHONPATH', '').split(os.pathsep) if p and p != bootstrap]
    env['PYTHONPATH'] = os.pathsep.join([bootstrap] + paths)
    node = str(Path(bootstrap) / 'node_slots.js')
    option = '--require ' + json.dumps(node)
    if option not in env.get('NODE_OPTIONS', ''):
        env['NODE_OPTIONS'] = (env.get('NODE_OPTIONS', '') + ' ' + option).strip()
    env['HARNESS_SLOT_PYTHON'] = sys.executable
    env['HARNESS_SLOT_SCRIPT'] = str(Path(__file__).resolve())
    shell = (Path(bootstrap) / 'bash_slots.sh').as_posix()
    # Another checkout's bootstrap (a publish battery's template) is this one, not a previous BASH_ENV to chain:
    # chained, it sourced itself until the shell crashed.
    previous = env.get('BASH_ENV') or ''
    if previous and previous != shell and Path(previous).name != 'bash_slots.sh':
        env['HARNESS_PREVIOUS_BASH_ENV'] = previous
    env['BASH_ENV'] = shell
    env.setdefault('HARNESS_MACHINE_ROOT', machine_root())
    return env


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
    args, command = parser.parse_known_args()
    if args.mode == 'root':
        print(json.dumps(slot_dir()), flush=True)
        return 0
    if command and command[0] == '--':
        command = command[1:]
    if args.mode == 'run' and not command:
        parser.error('run needs -- COMMAND ...')
    held = None
    try:
        held = take(args.exclusive, args.count, folder=args.folder, width=args.width, deadline=args.deadline,
                    notice=args.notice or args.mode == 'run')
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
