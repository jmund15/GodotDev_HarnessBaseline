#!/usr/bin/env python3
"""Part 2 of 2 of the sidecar launcher registration proof: its cases, round-robin from
`test_sidecar_common_registration.main`, run beside the other part.

    python3 .claude/tests/test_sidecar_common_registration_part2.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sidecar_common_registration  # noqa: E402

if __name__ == "__main__":
    sys.exit(test_sidecar_common_registration.main(2))
