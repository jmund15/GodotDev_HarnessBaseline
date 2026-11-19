#!/usr/bin/env python3
"""Part 4 of 4 of the sidecar dispatch-context proof: its cases run beside the other parts.

    python3 .claude/tests/test_sidecar_dispatch_context_part4.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_sidecar_dispatch_context as proof  # noqa: E402

if __name__ == "__main__":
    proof.parts_present()
    rc = proof.main(4)
    rc = proof.pytest_share(3) or rc
    sys.exit(rc)
