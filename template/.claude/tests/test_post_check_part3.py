#!/usr/bin/env python3
"""Part 3 of 4 of the post-check proof: its cases run beside the other parts, each still driving
post_check.py as its own process.

    python3 .claude/tests/test_post_check_part3.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _case_pool import run_sharded  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_sharded(os.path.join(HERE, "test_post_check.py"), 2, 4, jobs=1))
