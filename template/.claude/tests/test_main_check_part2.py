#!/usr/bin/env python3
"""Part 2 of 2 of the main-check proof: its pytest cases run beside the other part, each spawned main-check run still
in its own subprocess.

    python3 .claude/tests/test_main_check_part2.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _case_pool import run_sharded  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_sharded(os.path.join(HERE, "test_main_check.py"), 1, 2))
