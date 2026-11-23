#!/usr/bin/env python3
"""
Validate .claude/commands/ structural integrity.

Checks that every markdown link under commands/ resolves, that code_quality.md keeps its section
headers, and that the checklist item counts match the mirrors below. A count mirror is a tripwire
against a silently dropped item: change it in the same commit that adds or removes the item.

Run: python .claude/scripts/validate_commands.py
Exit: 0 = all pass, 1 = any fail
"""

import os
import re
import sys
from pathlib import Path

# All paths relative to project root
COMMANDS_DIR = Path(".claude/commands")
CHECKLISTS_DIR = COMMANDS_DIR / "checklists"


# --- Category 1: File References ---

# Markdown link targets to verify: (source_file, link_pattern)
# We scan all .md files for ](...md) links pointing within commands/
def check_file_references() -> list[str]:
    """Verify all markdown link targets under commands/ exist."""
    errors = []
    count = 0

    for md_file in COMMANDS_DIR.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        # Match ](relative/path.md) and ](relative/path.md#anchor) — relative links within commands
        for match in re.finditer(r'\]\(([^)#\s]+\.md)(?:#[^)]*)?\)', content):
            target = match.group(1)
            # Skip absolute paths (skill references like /.claude/skills/...)
            if target.startswith("/") or target.startswith("http"):
                count += 1
                continue

            resolved = (md_file.parent / target).resolve()
            count += 1
            if not resolved.exists():
                rel = os.path.relpath(md_file, COMMANDS_DIR)
                errors.append(f"  {rel} -> {target} (NOT FOUND)")

    return _format_result("File references", count, errors)


# --- Category 3: Section Headers ---

# Expected section headers in code_quality.md
# Update these if sections are intentionally renamed.
EXPECTED_CODE_QUALITY_SECTIONS = [
    "## Compliance (C)",
    "## Design (D)",
    "## Semantics (S)",
    "## Robustness (R)",
    "## Performance (P)",
    "## Intuitiveness (I)",
]


def check_section_headers() -> list[str]:
    """Verify code_quality.md contains all expected section headers."""
    errors = []
    cq_path = CHECKLISTS_DIR / "code_quality.md"

    if not cq_path.exists():
        return ["[FAIL] Section headers: code_quality.md not found"]

    content = cq_path.read_text(encoding="utf-8")
    count = len(EXPECTED_CODE_QUALITY_SECTIONS)

    for header in EXPECTED_CODE_QUALITY_SECTIONS:
        if header not in content:
            errors.append(f"  Missing section: {header}")

    return _format_result("Section headers", count, errors)


# --- Category 4: Checklist Item Counts ---

# Expected item counts per section. Update these IN THE SAME COMMIT that adds or removes
# a checklist item: the checklist files are authoritative and these mirror them, so drift
# here is always a stale mirror, never a content defect.
# Pattern matched: lines starting with `- [ ] **` (checklist items with bold labels)
EXPECTED_COUNTS = {
    "Compliance (C)": 5,
    "Design (D)": 21,
    "Semantics (S)": 8,
    "Robustness (R)": 11,
    "Performance (P)": 4,
    "Intuitiveness (I)": 6,
}
EXPECTED_CODE_QUALITY_TOTAL = 55
# The engine layer's `<stem>_godot.md` companions are checked only when that layer ships them.
EXPECTED_GODOT_COUNTS = {
    "Godot Compliance (C)": 11,
    "Godot Design (D)": 1,
    "Godot Robustness (R)": 4,
    "Godot Performance (P)": 1,
    "Godot Intuitiveness (I)": 3,
}
EXPECTED_TEST_QUALITY_TOTAL = 14
EXPECTED_GODOT_TEST_COUNTS = {
    "Godot Quality": 1,
    "Godot Curation": 1,
    "Godot Testability": 1,
}


def _count_sections(content: str, expected: dict[str, int]) -> tuple[dict[str, int], list[str]]:
    """Items per `## <name>` section named in `expected`, and a mismatch line for each section whose count differs."""
    current, counts = None, {}
    for line in content.split("\n"):
        header = re.match(r"^## (.*?)\s*$", line)
        if header:
            current = header.group(1) if header.group(1) in expected else None
            if current:
                counts[current] = 0
        elif current and re.match(r"^- \[ \] \*\*", line):
            counts[current] += 1
    errors = []
    for name, want in expected.items():
        got = counts.get(name, 0)
        if got != want:
            errors.append(f"  {name}: expected {want}, got {got} ({'+' if got > want else ''}{got - want})")
    return counts, errors


def check_checklist_counts() -> list[str]:
    """Verify checklist item counts match expectations."""
    errors = []

    # --- code_quality.md ---
    cq_path = CHECKLISTS_DIR / "code_quality.md"
    if not cq_path.exists():
        return ["[FAIL] Checklist counts: code_quality.md not found"]

    # Sections are anchored to a leading `## `, so a `### Design (D)` subheading is not read as its
    # section and an unlisted `## ` section closes the current one instead of inflating its count.
    section_counts, count_errors = _count_sections(cq_path.read_text(encoding="utf-8"), EXPECTED_COUNTS)
    errors.extend(count_errors)
    total = sum(section_counts.values())

    if total != EXPECTED_CODE_QUALITY_TOTAL:
        diff = total - EXPECTED_CODE_QUALITY_TOTAL
        sign = "+" if diff > 0 else ""
        errors.append(f"  code_quality.md total: expected {EXPECTED_CODE_QUALITY_TOTAL}, got {total} ({sign}{diff})")

    # --- test_quality.md ---
    tq_path = CHECKLISTS_DIR / "test_quality.md"
    if not tq_path.exists():
        errors.append("  test_quality.md not found")
    else:
        tq_content = tq_path.read_text(encoding="utf-8")
        tq_count = len(re.findall(r'^- \[ \] \*\*', tq_content, re.MULTILINE))
        if tq_count != EXPECTED_TEST_QUALITY_TOTAL:
            diff = tq_count - EXPECTED_TEST_QUALITY_TOTAL
            sign = "+" if diff > 0 else ""
            errors.append(f"  test_quality.md total: expected {EXPECTED_TEST_QUALITY_TOTAL}, got {tq_count} ({sign}{diff})")

    godot_path = cq_path.with_name(f"{cq_path.stem}_godot.md")
    godot_count = 0
    if godot_path.exists():
        _, godot_errors = _count_sections(godot_path.read_text(encoding="utf-8"), EXPECTED_GODOT_COUNTS)
        errors.extend(f"  {godot_path.name}{e}" for e in godot_errors)
        godot_count = len(EXPECTED_GODOT_COUNTS)

    tq_godot_path = tq_path.with_name(f"{tq_path.stem}_godot.md")
    if tq_godot_path.exists():
        _, tq_godot_errors = _count_sections(tq_godot_path.read_text(encoding="utf-8"), EXPECTED_GODOT_TEST_COUNTS)
        errors.extend(f"  {tq_godot_path.name}{e}" for e in tq_godot_errors)
        godot_count += len(EXPECTED_GODOT_TEST_COUNTS)

    check_count = len(EXPECTED_COUNTS) + 2 + godot_count  # per-section + code total + test total + godot sections
    return _format_result("Checklist counts", check_count, errors)


# --- Helpers ---

def _format_result(category: str, check_count: int, errors: list[str]) -> list[str]:
    """Format a category result as [PASS] or [FAIL] lines."""
    if errors:
        detail = "\n".join(errors)
        return [f"[FAIL] {category}: {len(errors)} issue(s)\n{detail}"]
    return [f"[PASS] {category}: {check_count}/{check_count} checks passed"]


def main() -> int:
    print("=== Command Validation ===\n")

    all_results: list[str] = []
    passed = 0
    failed = 0

    checks = [
        check_file_references,
        check_section_headers,
        check_checklist_counts,
    ]

    for check_fn in checks:
        results = check_fn()
        for r in results:
            print(r)
            if r.startswith("[PASS]"):
                passed += 1
            elif r.startswith("[FAIL]"):
                failed += 1
        all_results.extend(results)

    print(f"\nResults: {passed} passed, {failed} failed")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
