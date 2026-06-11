#!/usr/bin/env python3
"""
Validate .claude/commands/ structural integrity.

Checks cross-file references, template variables, section headers,
checklist item counts, and agent name consistency. Catches silent
breakages that would degrade review/audit agent quality.

Run: python .claude/scripts/validate_commands.py
Exit: 0 = all pass, 1 = any fail
"""

import os
import re
import sys
from pathlib import Path

# All paths relative to project root
COMMANDS_DIR = Path(".claude/commands")
AGENTS_DIR = COMMANDS_DIR / "agents"
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
        # Match ](relative/path.md) — relative links within commands
        for match in re.finditer(r'\]\(([^)]+\.md)\)', content):
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


# --- Category 2: Template Variables ---

# Variables assembled at runtime by orchestrators — NOT defined in template files
RUNTIME_VARS = {"CONTEXT", "PR_NUM", "BRANCH", "TRANSCRIPT_CORRECTIONS"}

# Known injection mappings: variable -> which orchestrator(s) inject it
# Update this when adding new template variables.
KNOWN_INJECTIONS = {
    "CHECKLIST_CDS": ["review_pr.md"],
    "CHECKLIST_RP": ["review_pr.md"],
    "CHECKLIST_I": ["review_pr.md"],
    "TEST_QUALITY_CHECKLIST": ["review_pr.md", "session_audit.md"],
    "CODE_QUALITY_CHECKLIST": ["session_audit.md"],
}


def check_template_variables() -> list[str]:
    """Verify every {{VAR}} in agent templates has a known injection source."""
    errors = []
    count = 0

    for agent_file in AGENTS_DIR.glob("*_agents.md"):
        content = agent_file.read_text(encoding="utf-8")
        for match in re.finditer(r'\{\{(\w+)\}\}', content):
            var_name = match.group(1)
            count += 1

            if var_name in RUNTIME_VARS:
                continue
            if var_name not in KNOWN_INJECTIONS:
                rel = os.path.relpath(agent_file, COMMANDS_DIR)
                errors.append(f"  {rel}: {{{{{var_name}}}}} has no known injection source")

    return _format_result("Template variables", count, errors)


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

# Expected item counts per section. Update these when intentionally adding/removing items.
# Pattern matched: lines starting with `- [ ] **` (checklist items with bold labels)
EXPECTED_COUNTS = {
    "Compliance (C)": 14,
    "Design (D)": 8,
    "Semantics (S)": 8,
    "Robustness (R)": 14,
    "Performance (P)": 4,
    "Intuitiveness (I)": 10,
}
EXPECTED_CODE_QUALITY_TOTAL = 58
EXPECTED_TEST_QUALITY_TOTAL = 8


def check_checklist_counts() -> list[str]:
    """Verify checklist item counts match expectations."""
    errors = []

    # --- code_quality.md ---
    cq_path = CHECKLISTS_DIR / "code_quality.md"
    if not cq_path.exists():
        return ["[FAIL] Checklist counts: code_quality.md not found"]

    content = cq_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    current_section = None
    section_counts: dict[str, int] = {}

    for line in lines:
        # Detect section headers
        for section_name in EXPECTED_COUNTS:
            if f"## {section_name}" in line:
                current_section = section_name
                section_counts[current_section] = 0
                break

        # Count checklist items
        if current_section and re.match(r'^- \[ \] \*\*', line):
            section_counts[current_section] = section_counts.get(current_section, 0) + 1

    total = 0
    for section_name, expected in EXPECTED_COUNTS.items():
        actual = section_counts.get(section_name, 0)
        total += actual
        if actual != expected:
            diff = actual - expected
            sign = "+" if diff > 0 else ""
            errors.append(f"  {section_name}: expected {expected}, got {actual} ({sign}{diff})")

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

    check_count = len(EXPECTED_COUNTS) + 2  # per-section + code total + test total
    return _format_result("Checklist counts", check_count, errors)


# --- Category 5: Agent Name Consistency ---

# Agent template files and which orchestrator(s) reference their agents
AGENT_SOURCES = {
    "review_agents.md": ["review_pr.md", "review_prs.md"],
    "session_audit_agents.md": ["session_audit.md"],
}


def check_agent_names() -> list[str]:
    """Verify agent names defined in templates are referenced by orchestrators."""
    errors = []
    count = 0

    for agent_file_name, orchestrator_names in AGENT_SOURCES.items():
        agent_path = AGENTS_DIR / agent_file_name
        if not agent_path.exists():
            errors.append(f"  Agent template not found: {agent_file_name}")
            continue

        agent_content = agent_path.read_text(encoding="utf-8")

        # Extract agent names from ### headers (e.g., "### code-reviewer")
        agent_names = re.findall(r'^### ((?:pp|sa)-[\w-]+)', agent_content, re.MULTILINE)

        # Read all orchestrator contents
        orchestrator_contents = {}
        for orch_name in orchestrator_names:
            orch_path = COMMANDS_DIR / orch_name
            if orch_path.exists():
                orchestrator_contents[orch_name] = orch_path.read_text(encoding="utf-8")
            else:
                errors.append(f"  Orchestrator not found: {orch_name}")

        for name in agent_names:
            count += 1
            # Check if at least one orchestrator references this agent
            found = any(name in content for content in orchestrator_contents.values())
            if not found:
                errors.append(f"  {name} (defined in {agent_file_name}) not referenced by any orchestrator")

    return _format_result("Agent names", count, errors)


# --- Category 6: No Hardcoded Templates ---

def check_no_hardcoded_templates() -> list[str]:
    """Verify orchestrators don't inline agent prompts (should reference template files)."""
    errors = []
    count = 0

    # Orchestrator files (top-level command files that spawn agents)
    orchestrator_files = ["review_pr.md", "review_prs.md", "session_audit.md"]

    for orch_name in orchestrator_files:
        orch_path = COMMANDS_DIR / orch_name
        if not orch_path.exists():
            continue

        content = orch_path.read_text(encoding="utf-8")
        count += 1

        # Check for inline agent prompts inside code fences
        in_code_fence = False
        for i, line in enumerate(content.split("\n"), 1):
            if line.strip().startswith("```"):
                in_code_fence = not in_code_fence
                continue

            if in_code_fence and re.search(r'You are (?:pp|sa)-', line):
                errors.append(f"  {orch_name}:{i} — hardcoded agent prompt (should reference template file)")

    return _format_result("No hardcoded templates", count, errors)


# --- Category 7: Fixture Integrity ---

FIXTURES_DIR = Path(".claude/tests/agent_fixtures")

# Authoritative agent name -> model mapping (must match template files)
# Update when agents are added/removed or models reassigned.
AGENT_MODEL_MAP = {
    "code-reviewer": ("opus", "review_agents.md"),
    "test-analyzer": ("opus", "review_agents.md"),
    "error-hunter": ("opus", "review_agents.md"),
    "type-reviewer": ("sonnet", "review_agents.md"),
    "data-integrity": ("haiku", "review_agents.md"),
    "pool-lifecycle": ("opus", "review_agents.md"),
    "transcript-auditor": ("haiku", "review_agents.md"),
    "sa-design-semantics": ("opus", "session_audit_agents.md"),
    "sa-robustness-performance": ("opus", "session_audit_agents.md"),
    "sa-intuitiveness-testability": ("sonnet", "session_audit_agents.md"),
}

VALID_CHECKLIST_SECTIONS = {"C+D+S", "R+P", "I", "full", "test", "full+test"}

REQUIRED_FIXTURE_SECTIONS = [
    "## Metadata",
    "## Synthetic Code",
    "## Synthetic Diff",
    "## Expected Findings",
]


def check_fixture_integrity() -> list[str]:
    """Verify agent test fixtures have valid structure and metadata."""
    errors = []
    count = 0

    if not FIXTURES_DIR.exists():
        return ["[PASS] Fixture integrity: no fixtures directory (skipped)"]

    fixtures = list(FIXTURES_DIR.rglob("*.md"))
    # Exclude README files
    fixtures = [f for f in fixtures if f.name.lower() != "readme.md"]

    if not fixtures:
        return ["[PASS] Fixture integrity: no fixtures found (skipped)"]

    for fixture_path in fixtures:
        count += 1
        content = fixture_path.read_text(encoding="utf-8")
        rel = os.path.relpath(fixture_path, FIXTURES_DIR)

        # Check required sections
        for section in REQUIRED_FIXTURE_SECTIONS:
            if section not in content:
                errors.append(f"  {rel}: missing section '{section}'")

        # Extract metadata fields
        agent = _extract_metadata(content, "agent")
        model = _extract_metadata(content, "model")
        agent_source = _extract_metadata(content, "agent_source")
        checklist_section = _extract_metadata(content, "checklist_section")

        # Validate agent name
        if not agent:
            errors.append(f"  {rel}: missing 'agent' in metadata")
        elif agent not in AGENT_MODEL_MAP:
            known = ", ".join(sorted(AGENT_MODEL_MAP.keys()))
            errors.append(f"  {rel}: unknown agent '{agent}' (known: {known})")

        # Validate model matches agent's assigned model
        if not model:
            errors.append(f"  {rel}: missing 'model' in metadata")
        elif agent and agent in AGENT_MODEL_MAP:
            expected_model, _ = AGENT_MODEL_MAP[agent]
            if model != expected_model:
                errors.append(f"  {rel}: model '{model}' doesn't match {agent}'s assigned model '{expected_model}'")

        # Validate agent_source matches agent's source file
        if not agent_source:
            errors.append(f"  {rel}: missing 'agent_source' in metadata")
        elif agent and agent in AGENT_MODEL_MAP:
            _, expected_source = AGENT_MODEL_MAP[agent]
            if agent_source != expected_source:
                errors.append(f"  {rel}: agent_source '{agent_source}' doesn't match expected '{expected_source}'")

        # Validate checklist_section
        if not checklist_section:
            errors.append(f"  {rel}: missing 'checklist_section' in metadata")
        elif checklist_section not in VALID_CHECKLIST_SECTIONS:
            errors.append(f"  {rel}: invalid checklist_section '{checklist_section}' (valid: {', '.join(sorted(VALID_CHECKLIST_SECTIONS))})")

        # Validate min_count / max_count
        min_count = _extract_expected_field(content, "min_count")
        max_count = _extract_expected_field(content, "max_count")

        if min_count is None:
            errors.append(f"  {rel}: missing 'min_count' in Expected Findings")
        if max_count is None:
            errors.append(f"  {rel}: missing 'max_count' in Expected Findings")
        if min_count is not None and max_count is not None and min_count > max_count:
            errors.append(f"  {rel}: min_count ({min_count}) > max_count ({max_count})")

        # Validate at least one Required finding spec
        if "### Required" not in content:
            errors.append(f"  {rel}: missing '### Required' finding spec")
        else:
            # Count required specs (lines starting with number + period after ### Required)
            in_required = False
            required_count = 0
            for line in content.split("\n"):
                if "### Required" in line:
                    in_required = True
                    continue
                if in_required and line.startswith("###"):
                    break
                if in_required and re.match(r'^\d+\.', line.strip()):
                    required_count += 1
            if required_count == 0:
                errors.append(f"  {rel}: no numbered finding specs under '### Required'")

    return _format_result("Fixture integrity", count, errors)


def _extract_metadata(content: str, field: str) -> str | None:
    """Extract a metadata field value from fixture content (e.g., '- **agent:** code-reviewer')."""
    match = re.search(rf'^\s*-\s*\*\*{field}:\*\*\s*(.+)$', content, re.MULTILINE)
    if match:
        return match.group(1).strip().rstrip("`")
    return None


def _extract_expected_field(content: str, field: str) -> int | None:
    """Extract a numeric field from Expected Findings (e.g., '- **min_count:** 1')."""
    match = re.search(rf'^\s*-\s*\*\*{field}:\*\*\s*(\d+)', content, re.MULTILINE)
    if match:
        return int(match.group(1))
    return None


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
        check_template_variables,
        check_section_headers,
        check_checklist_counts,
        check_agent_names,
        check_no_hardcoded_templates,
        check_fixture_integrity,
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
