#!/usr/bin/env python3
"""Part 2 of 4 of the own-worktree proof: its cases, round-robin from `test_pattern_enforcer_own_worktree.main`, run beside the other parts.

    python3 .claude/tests/test_pattern_enforcer_own_worktree_part2.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_pattern_enforcer_own_worktree  # noqa: E402

if __name__ == "__main__":
    sys.exit(test_pattern_enforcer_own_worktree.main(2))
