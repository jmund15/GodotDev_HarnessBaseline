#!/usr/bin/env python3
"""Part 4 of 4 of the pre-bash dispatcher proof: its pytest cases run beside the other parts, each spawned
dispatcher run still in its own subprocess.

    python3 .claude/tests/test_pre_bash_dispatch_part4.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _case_pool import run_sharded  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_sharded(os.path.join(HERE, "test_pre_bash_dispatch.py"), 3, 4, jobs=1))
