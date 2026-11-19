#!/usr/bin/env python3
"""Part 4 of the runner proof: its cases, round-robin from `test_harness_tests_runner.main`, run beside the other parts.

    python3 .claude/tests/test_harness_tests_runner_part4.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_harness_tests_runner  # noqa: E402

if __name__ == "__main__":
    sys.exit(test_harness_tests_runner.main(4))
