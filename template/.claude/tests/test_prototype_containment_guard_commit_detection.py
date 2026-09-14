#!/usr/bin/env python3
"""Commit-detection proof for hooks/prototype_containment_guard.py — shared cases in _commit_guard_cases.py.

    python3 .claude/tests/test_prototype_containment_guard_commit_detection.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _commit_guard_cases import run_for  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_for("prototype_containment_guard.py"))
