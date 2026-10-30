#!/usr/bin/env python3
"""Proof for tools/tree_cpu.py: the CPU a process tree used, read through a grandchild, growing while a
descendant works; a gone pid and a malformed argument are refusals, never a number."""
import subprocess
import sys
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'tree_cpu.py'
sys.path.insert(0, str(TOOL.parent))
import tree_cpu  # noqa: E402

fail = 0


def check(ok, label, detail=''):
    global fail
    print(('ok   ' if ok else 'FAIL ') + label + ('' if ok else ': ' + str(detail)))
    fail += not ok


# A parent that only waits, whose grandchild burns CPU: the tree total grows; the parent's own does not.
burn = 'import time\nend = time.monotonic() + 4\nwhile time.monotonic() < end: pass'
middle = 'import subprocess, sys; subprocess.run([sys.executable, "-c", %r])' % burn
parent = subprocess.Popen([sys.executable, '-c', 'import subprocess, sys; subprocess.run([sys.executable, "-c", %r])' % middle])
try:
    time.sleep(1.0)
    first = tree_cpu.tree_cpu(parent.pid)
    time.sleep(1.5)
    second = tree_cpu.tree_cpu(parent.pid)
    check(first is not None and second is not None and second - first >= 0.5,
          'a grandchild burning CPU grows the tree total', (first, second))
    out = subprocess.run([sys.executable, str(TOOL), str(parent.pid)], capture_output=True, text=True)
    check(out.returncode == 0 and float(out.stdout) >= second, 'the CLI prints the tree total', (out.returncode, out.stdout))
finally:
    parent.wait(timeout=30)

gone = subprocess.run([sys.executable, str(TOOL), str(parent.pid)], capture_output=True, text=True)
check(gone.returncode == 1 and gone.stdout == '', 'a gone root prints nothing and exits 1', (gone.returncode, gone.stdout))
bad = subprocess.run([sys.executable, str(TOOL), 'nope'], capture_output=True, text=True)
check(bad.returncode == 2 and bad.stdout == '', 'a malformed pid is refused with exit 2', (bad.returncode, bad.stdout))

print('PASS test_tree_cpu' if not fail else 'FAIL test_tree_cpu')
sys.exit(1 if fail else 0)
