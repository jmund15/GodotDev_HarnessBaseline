#!/usr/bin/env python3
"""Proof for the drive spine's phase split: no citation pairs the spine with a moved section.

    python3 .claude/tests/test_drive_phase_links.py

Doctrine scope for the citation sweep is `.claude/{commands,skills,rules,guards}/*.md` plus the
top-level `CLAUDE*.md`; plans, auto-memory, logs, scratch and test fixtures are history.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
SPINE = "skills/_brainstorm_shared/drive_spine.md"
EXECUTION = "skills/_brainstorm_shared/drive_execution.md"
CLOSE = "skills/_brainstorm_shared/drive_close.md"
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail)[:300] if detail else ""))


def read(rel):
    path = CLAUDE / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def doctrine_texts():
    texts = {}
    for sub in ("commands", "skills", "rules", "guards"):
        for path in sorted((CLAUDE / sub).rglob("*.md")):
            texts[path.relative_to(CLAUDE).as_posix()] = path.read_text(encoding="utf-8")
    for path in sorted(CLAUDE.glob("CLAUDE*.md")):
        texts[path.name] = path.read_text(encoding="utf-8")
    return texts


def citation_errors(texts):
    """Every line pairing the spine with a moved section without naming its phase file."""
    errors = []
    for rel, body in texts.items():
        for number, line in enumerate(body.splitlines(), 1):
            if "drive_spine.md" not in line:
                continue
            if "§Slices" in line and "drive_execution.md" not in line:
                errors.append("%s:%d cites drive_spine.md §Slices" % (rel, number))
            if "§Close" in line and "drive_close.md" not in line:
                errors.append("%s:%d cites drive_spine.md §Close" % (rel, number))
    return errors


def main():
    check("drive_execution.md exists", bool(read(EXECUTION)), EXECUTION)
    check("drive_close.md exists", bool(read(CLOSE)), CLOSE)
    errors = citation_errors(doctrine_texts())
    check("every drive_spine.md §Slices/§Close citation names its phase file", not errors, errors)

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for failure in FAILURES:
        print("  " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
