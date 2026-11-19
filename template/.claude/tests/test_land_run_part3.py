#!/usr/bin/env python3
"""Part 3 of 3 of the land-runner proof: its cases, round-robin from `test_land_run.main`, run beside the other parts.

    python3 .claude/tests/test_land_run_part3.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_land_run  # noqa: E402

if __name__ == "__main__":
    sys.exit(test_land_run.main(3))
