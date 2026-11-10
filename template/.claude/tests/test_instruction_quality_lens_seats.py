#!/usr/bin/env python3
"""Proof that the lenses reading the instruction_quality skill name every moved reference file.

The plan-check instruction-quality lens preloads SKILL.md and each moved file; the session-audit
harness-quality seat names each moved file in its read-first line. Both registries ship in the
coding layer, so this proof ships there; the skill's split itself is proved in
test_instruction_quality_split.py.

    python3 .claude/tests/test_instruction_quality_lens_seats.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(CLAUDE, "tools"))

import lens  # noqa: E402
from test_instruction_quality_split import REFS, check, read, FAILURES  # noqa: E402


def main():
    declared = {l.key: getattr(l, "preloads", None) for l in lens.parse("plan_check")[1]}
    want = ["skills/instruction_quality/SKILL.md"] + ["skills/instruction_quality/" + r for r in REFS]
    check("the plan-check instruction-quality lens preloads SKILL.md and every moved file",
          sorted(declared.get("plc-instruction-quality") or []) == sorted(want), declared.get("plc-instruction-quality"))

    audit = read(CLAUDE, "commands", "agents", "session_audit_agents.md")
    start = audit.index("You are sa-harness-quality")
    seat = audit[start:start + 600]
    check("the session-audit harness-quality lens names every moved file in its read-first line",
          all(os.path.basename(r) in seat for r in REFS), seat[:300])

    print("\n%d failed" % len(FAILURES) if FAILURES else "\nall passed")
    for f in FAILURES:
        print("  FAIL", f)
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
