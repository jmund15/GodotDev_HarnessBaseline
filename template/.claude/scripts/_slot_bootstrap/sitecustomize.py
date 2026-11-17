"""Local proof admission inherited by delegate shells; runner processes coordinate instead."""
import atexit
import os
from pathlib import Path
import sys


def _admit():
    script = Path(sys.argv[0]).name if sys.argv else ''
    if script == 'harness_tests.py' and os.environ.get('HARNESS_SLOT_BUDGET') == '1':
        os.environ['HARNESS_TEST_JOBS'] = '1'
        os.environ['HARNESS_CASE_JOBS'] = '1'
        # A baseline checkout may predate loan-aware admission. It spends its parent's lane, not a second pool.
        if 'import machine_slots' not in Path(sys.argv[0]).read_text(encoding='utf-8'):
            os.environ['HARNESS_MACHINE_SLOTS'] = '0'
        return
    if not (script.startswith('test_') or '_test.' in script):
        return
    claude = Path(__file__).resolve().parents[2]
    saved_path, saved_modules = list(sys.path), set(sys.modules)
    sys.path.insert(0, str(claude / 'tools'))
    try:
        import machine_slots
    finally:
        # A proof in another checkout (a delegate's worktree) must import its own copies of these harness modules.
        sys.path[:] = saved_path
        for name in set(sys.modules) - saved_modules:
            origin = getattr(sys.modules[name], '__file__', None)
            if origin and Path(origin).resolve().is_relative_to(claude):
                del sys.modules[name]
    if machine_slots.inherited():
        return
    try:
        held = machine_slots.take(notice=True)
        if held is None:
            raise machine_slots.SlotStoreError('proof admission unavailable')
        atexit.register(machine_slots.release_all, held)
        os.environ['HARNESS_MACHINE_ROOT'] = machine_slots.machine_root()
        os.environ['HARNESS_SLOT_BUDGET'] = '1'
    except OSError as exc:
        print('CANNOT-RUN machine slots: %s' % exc, file=sys.stderr, flush=True)
        os._exit(2)


_admit()


def _previous_startup():
    import importlib.machinery
    import importlib.util
    here = Path(__file__).resolve().parent
    # Only what Python would reach after this folder: a sitecustomize ahead of it (the read recorder) already ran, and
    # starts this one in turn, so running it from here would hand startup back and forth until the stack ends.
    folders = [Path(entry).resolve() if entry else None for entry in sys.path]
    after = folders.index(here) + 1 if here in folders else 0
    paths = [entry for entry, folder in zip(sys.path[after:], folders[after:]) if entry and folder != here]
    previous = importlib.machinery.PathFinder.find_spec('sitecustomize', paths)
    if previous and previous.origin and Path(previous.origin).resolve() != Path(__file__).resolve():
        spec = importlib.util.spec_from_file_location('_harness_prior_startup', previous.origin)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        # The read recorder exposes retarget to case-pool workers through sitecustomize.
        if hasattr(module, 'retarget'):
            globals()['retarget'] = module.retarget


_previous_startup()
