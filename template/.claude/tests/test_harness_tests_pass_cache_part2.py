#!/usr/bin/env python3
"""Part 2 of 3 of the pass cache proof: its cases, round-robin from `test_harness_tests_pass_cache.main`, run beside the other parts.

    python3 .claude/tests/test_harness_tests_pass_cache_part2.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_harness_tests_pass_cache  # noqa: E402

if __name__ == "__main__":
    sys.exit(test_harness_tests_pass_cache.main(2))
