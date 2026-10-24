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
TOOLS_DIR = Path(".claude/tools")
# Optional: absent in projects that carry no external-model transport.
REGISTRY_FILE = Path(".claude/reference/external_models.json")

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


# --- Category 2: Template Variables ---

# Variables assembled at runtime by orchestrators — NOT defined in template files
RUNTIME_VARS = {"CONTEXT", "PR_NUM", "BRANCH", "TRANSCRIPT_CORRECTIONS"}

# The baseline's sync placeholders: a consumer's pull renders them from its lock `substitutions`, so a
# baseline copy carries them unrendered (sync_baseline §Placeholders).
SYNC_PLACEHOLDERS = {"PROJECT_NAME", "PROJECT_NAMESPACE", "VAULT_ROOT", "PROJECT_ROOT"}

# Known injection mappings: variable -> which orchestrator(s) inject it
# Update this when adding new template variables.
KNOWN_INJECTIONS = {
    "CHECKLIST_CDS": ["review_pr.md"],
    "CHECKLIST_RP": ["review_pr.md"],
    "CHECKLIST_I": ["review_pr.md"],
    "TEST_QUALITY_CHECKLIST": ["review_pr.md", "session_audit.md"],
    "CODE_QUALITY_CHECKLIST": ["../tools/lens_briefs.py"],
    "DESIGN_LIST": ["reference/session_audit_seats.md"],
    "SHORT_PLAN": ["session_audit.md"],
    "DOCTRINE_COMMITS": ["session_audit.md", "reference/session_audit_seats.md"],
    "HOT_SET": ["reference/session_audit_seats.md"],
    "HARNESS_HUNKS": ["session_audit.md", "reference/session_audit_seats.md"],
    "UNIT_LIST": ["session_audit.md", "reference/session_audit_seats.md"],
}


def check_template_variables() -> list[str]:
    """Verify every {{VAR}} in agent templates is injected by a real orchestrator.

    Two-sided: the variable must be declared in KNOWN_INJECTIONS, AND at least one
    declared orchestrator must actually name it. A declaration alone is a rubber
    stamp: it passes for a variable whose injection was renamed away or never
    written, which is the failure this check exists to catch.
    """
    errors = []
    count = 0

    for agent_file in AGENTS_DIR.glob("*_agents.md"):
        content = agent_file.read_text(encoding="utf-8")
        for match in re.finditer(r'\{\{(\w+)\}\}', content):
            var_name = match.group(1)
            count += 1
            rel = os.path.relpath(agent_file, COMMANDS_DIR)

            if var_name in RUNTIME_VARS or var_name in SYNC_PLACEHOLDERS:
                continue
            if var_name not in KNOWN_INJECTIONS:
                errors.append(f"  {rel}: {{{{{var_name}}}}} has no known injection source")
                continue

            injectors = KNOWN_INJECTIONS[var_name]
            missing = [n for n in injectors if not (COMMANDS_DIR / n).is_file()]
            if missing:
                errors.append(f"  {rel}: {{{{{var_name}}}}} declares missing orchestrator(s): {', '.join(missing)}")
                continue
            if not any(var_name in (COMMANDS_DIR / n).read_text(encoding="utf-8") for n in injectors):
                errors.append(
                    f"  {rel}: {{{{{var_name}}}}} declared injected by {', '.join(injectors)}, "
                    f"but no such file mentions it"
                )

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

VALID_CHECKLIST_SECTIONS = {"C+D+S", "R+P", "I", "I+test", "full", "test", "full+test"}

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


def check_external_model_registry() -> list[str]:
    """Validate .claude/reference/external_models.json against its field contracts.

    SKIPS SILENTLY WHEN THE FILE IS ABSENT. This validator is baseline-tracked and
    ships to projects that will never carry an external-model registry; hard-failing
    on a missing optional file would break every downstream harness on the next
    baseline pull.
    """
    registry = REGISTRY_FILE
    if not registry.is_file():
        return []

    tools = TOOLS_DIR.resolve()
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import model_registry
    except Exception as exc:
        return _format_result("External model registry", 1, [f"  reader unimportable: {exc}"])

    try:
        data = model_registry.load(registry)
    except Exception as exc:
        return _format_result("External model registry", 1, [f"  {exc}"])

    # Non-fatal: a stale rate degrades a cited fact into an assumption while the
    # sidecar's cost echo still states it as measured.
    for model_id, why in model_registry.stale_prices(data):
        print(f"[WARN] External model registry: {model_id} price.asOf is {why} - re-probe the vendor page")

    return _format_result("External model registry", len(data["models"]), [])


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
        check_external_model_registry,
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
