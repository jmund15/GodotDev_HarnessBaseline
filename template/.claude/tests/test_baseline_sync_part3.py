#!/usr/bin/env python3
"""Part 3 of 3 of the baseline sync proof: its cases, round-robin from `test_baseline_sync.main`, run beside the other parts.

    python3 .claude/tests/test_baseline_sync_part3.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_baseline_sync  # noqa: E402

if __name__ == "__main__":
    sys.exit(test_baseline_sync.main(3))
