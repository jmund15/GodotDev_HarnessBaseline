#!/usr/bin/env python3
"""Proof for the drive spine's phase split: Slices and Close live once, in their phase files.

    python3 .claude/tests/test_drive_phase_links.py

Doctrine scope for the citation sweep is `.claude/{commands,skills,rules,guards}/*.md` plus the
top-level `CLAUDE*.md`; plans, auto-memory, logs, scratch and test fixtures are history.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude"
SPINE = "skills/_brainstorm_shared/drive_spine.md"
EXECUTION = "skills/_brainstorm_shared/drive_execution.md"
CLOSE = "skills/_brainstorm_shared/drive_close.md"
ADAPTER_MARK = re.compile(r"Run the stages[^\n]*drive_spine\.md")
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail)[:300] if detail else ""))


def read(rel):
    path = CLAUDE / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def headings(body):
    return re.findall(r"(?m)^## .+$", body)


def section(body, heading):
    if heading + "\n" not in body:
        return ""
    return body.split(heading + "\n", 1)[1].split("\n## ", 1)[0]


def spine_citers():
    """Every command/skill file citing the spine, from git when available."""
    try:
        out = subprocess.run(["git", "grep", "-l", "drive_spine.md", "--", ".claude/commands", ".claude/skills"],
                             cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=60)
        if out.returncode == 0:
            return [line.strip() for line in out.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(str(p.relative_to(ROOT)).replace("\\", "/")
                  for sub in ("commands", "skills") for p in (CLAUDE / sub).rglob("*.md")
                  if "drive_spine.md" in p.read_text(encoding="utf-8"))


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


def header_paragraph(body):
    blocks = [block for block in re.split(r"\n\s*\n", body) if ADAPTER_MARK.search(block)]
    return blocks[0] if blocks else ""


def main():
    spine = read(SPINE)
    execution = read(EXECUTION)
    close = read(CLOSE)
    check("drive_execution.md exists", bool(execution), EXECUTION)
    check("drive_close.md exists", bool(close), CLOSE)
    check("## Slices exists exactly once in drive_execution.md", headings(execution).count("## Slices") == 1,
          headings(execution))
    check("## Slices is gone from drive_spine.md", "## Slices" not in headings(spine))
    check("## Close exists exactly once in drive_close.md", headings(close).count("## Close") == 1, headings(close))
    check("## Close is gone from drive_spine.md", "## Close" not in headings(spine))
    check("drive_spine.md links drive_execution.md", "drive_execution.md" in spine)
    check("drive_spine.md links drive_close.md", "drive_close.md" in spine)
    check("the moved slice rules landed whole",
          "RED before GREEN per behaviour change" in section(execution, "## Slices")
          and "Behavioural baseline" in section(execution, "## Slices"))
    check("the moved close steps landed whole",
          "Verify by chain position" in section(close, "## Close")
          and "Candidate review" in section(close, "## Close"))

    adapters = {}
    for rel in spine_citers():
        body = (ROOT / rel).read_text(encoding="utf-8") if (ROOT / rel).is_file() else ""
        paragraph = header_paragraph(body)
        if paragraph:
            adapters[rel] = paragraph
    check("the git-grep adapter set reaches the split phases", len(adapters) >= 5, sorted(adapters))
    for rel, paragraph in adapters.items():
        check("%s links drive_execution.md at its slice stage" % rel, "drive_execution.md" in paragraph, paragraph)
        check("%s links drive_close.md at its close stage" % rel, "drive_close.md" in paragraph, paragraph)

    errors = citation_errors(doctrine_texts())
    check("every drive_spine.md §Slices/§Close citation names its phase file", not errors, errors)

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for failure in FAILURES:
        print("  " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
