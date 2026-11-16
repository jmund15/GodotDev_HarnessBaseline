#!/usr/bin/env python3
"""Part 2 of 3 of the regenerable-clone proof: the retired-worktree cases, run beside the other parts.

    python3 .claude/tests/test_regenerable_clone_part2.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_regenerable_clone  # noqa: E402

if __name__ == "__main__":
    sys.exit(test_regenerable_clone.main(2))
