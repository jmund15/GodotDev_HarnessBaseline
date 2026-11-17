#!/usr/bin/env python3
"""Part 2 of 2 of the baseline publish proof: its cases, round-robin from `test_baseline_publish.main`, run beside the other parts.

    python3 .claude/tests/test_baseline_publish_part2.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_baseline_publish  # noqa: E402

if __name__ == "__main__":
    sys.exit(test_baseline_publish.main(2))
