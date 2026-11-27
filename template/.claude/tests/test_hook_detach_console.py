#!/usr/bin/env python3
"""Hooks that start a detached worker give it a hidden console its children share.

A console-less worker (DETACHED_PROCESS) makes every console child it starts allocate a fresh console through the
default terminal; measured 2026-10-05 at 16-131 s per child against 0.02 s with CREATE_NO_WINDOW. Windows ignores
CREATE_NO_WINDOW when DETACHED_PROCESS is also set, so the pair is still console-less.

    python3 .claude/tests/test_hook_detach_console.py   (0 pass, 1 fail, 2 cannot run)
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / 'hooks'
FAILURES = []


def load(name):
    sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location(name, HOOKS / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def captured(call):
    seen = []
    real = subprocess.Popen

    class Fake:
        def __init__(self, *a, **kw):
            seen.append(kw)
    subprocess.Popen = Fake
    try:
        call()
    finally:
        subprocess.Popen = real
    return seen


def check(name, seen):
    if len(seen) != 1:
        FAILURES.append('%s: expected one detached spawn, saw %d' % (name, len(seen)))
        return
    flags = seen[0].get('creationflags', 0)
    if flags & subprocess.DETACHED_PROCESS:
        FAILURES.append('%s: spawns with DETACHED_PROCESS (console-less worker)' % name)
    if not flags & subprocess.CREATE_NO_WINDOW:
        FAILURES.append('%s: spawns without CREATE_NO_WINDOW' % name)
    if not flags & subprocess.CREATE_NEW_PROCESS_GROUP:
        FAILURES.append('%s: spawns outside a new process group' % name)
    print(('FAIL ' if any(f.startswith(name) for f in FAILURES) else 'ok   ') + name)


def main():
    if os.name != 'nt':
        print('CANNOT RUN: Windows creation flags')
        return 2
    parity = load('login_parity_check')
    with tempfile.TemporaryDirectory() as tmp:
        check('login_parity_check.spawn', captured(lambda: parity.spawn([sys.executable, '-c', 'pass'], os.path.join(tmp, 'log.txt'))))
    posture = load('budget_posture')
    os.environ.pop('HARNESS_CAPACITY_WARM', None)
    check('budget_posture.start_warm', captured(lambda: posture.start_warm(['anthropic'])))
    for f in FAILURES:
        print('  ' + f)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
