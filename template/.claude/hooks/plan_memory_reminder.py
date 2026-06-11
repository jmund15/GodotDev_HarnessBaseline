#!/usr/bin/env python3
"""
Hook: PostToolUse on ExitPlanMode — Inject memory + skill reminders for inferred domains.

Why:
- CLAUDE.md mandates "search auto-memory and load relevant Skills before planning"
  but enforcement is self-discipline. The /plan_check command covers high-stakes
  plans; this hook covers the routine plans below /plan_check's litmus.

What it does:
- Triggers only on ExitPlanMode tool calls.
- Locates the most-recently-modified plan file under ~/.claude/plans/.
- Pre-processes plan text: strips "Out of scope" sections and fenced code
  blocks (their content is examples/exclusions, not scope statements).
- If the plan has a "Critical files" / "Files to modify" / "Files changed"
  section, restricts domain inference to that section (highest-precision
  scope signal). Otherwise falls back to stripped whole-text.
- Infers domains by case-insensitive start-of-word matching against the
  keyword sets in the CLAUDE.md "Proactive Context Loading" table. Substring
  matching across identifier camelcase boundaries is rejected (so "craft"
  inside "SpellCrafter" does NOT fire the Crafting domain).
- Emits a hookSpecificOutput.additionalContext payload listing matched domains,
  auto-memory search queries, and any Skills to load.

Boundaries:
- Never blocks. Always exits 0.
- Silent if no domains match, plan file is missing/stale, or plan is < 50 words.
- Skill suggestions are deduplicated across multiple matched domains.
- 5s file-find timeout via mtime check, not subprocess timeout.

Wired in: settings.json hooks.PostToolUse with matcher "ExitPlanMode".
"""

import json
import re
import sys
import time
from pathlib import Path

# Domain inference table — mirrors CLAUDE.md "Proactive Context Loading" table
# and extends it with project-specific domains that map to existing Skills.
#
# PROJECT-CONFIG: add your game's content domains at the top of this table
# (e.g., for a spell-crafting game: ("Spells", ["spell", "trait", "synergy"],
# ["spell"], ["architecture_philosophy", "your_authoring_skill"])). The entries
# below are stack-generic and apply to any Godot + C# + Jmodot project.
#
# Each entry: (display_name, [trigger_keyword_substrings], [memory_search_keywords], [skills_to_load])
# Trigger matches are case-insensitive substring; word-boundary not enforced
# (false-positives are cheap, false-negatives are expensive).
DOMAINS = [
    ("HSM/States",
     ["HSM", "transition", "state machine", "compoundstate", "statebase"],
     ["HSM", "transition"],
     []),

    ("AI/Critters",
     ["AI", "critter", "BT", "perception", "steering", "behavior tree", "blackboard"],
     ["critter", "steering"],
     []),

    ("VFX",
     ["VFX", "particle", "Modulate", "tint", "sprite3d", "visualeffect"],
     ["VFX", "Modulate"],
     ["vfx_patterns"]),

    # Note: "test" intentionally absent — too broad (matches "diagnostic test",
    # "test sequence", "smoke test" in any plan). The remaining triggers are
    # specific enough to require no minimum-hits gate. CLAUDE.md "Forbidden as
    # primary search keywords" rule applies to triggers, not just memory_keys.
    ("Testing",
     ["GdUnit4", "TestSuite", "ISceneRunner", "[TestCase]", "fixture"],
     ["testing", "GdUnit4"],
     ["testing"]),

    ("Jmodot/Framework",
     ["Jmodot", "submodule", "framework boundary", "jmodot.core"],
     ["Jmodot"],
     ["jmodot"]),

    ("Physics",
     ["physics", "collision", "hitbox", "Area3D", "CollisionShape", "RigidBody"],
     ["physics", "collision"],
     []),

    ("Pooling",
     ["pool", "spawn", "acquire", "return"],
     ["pool", "spawn"],
     []),

    ("Status Effects",
     ["status", "stun", "freeze", "burn", "DoT", "tickrunner", "tickeffect"],
     ["status"],
     ["jmodot", "status_effect_authoring"]),

    ("Refactoring",
     ["refactor", "deprecate", "migrate", "rename", "extract", "consolidate"],
     ["refactor"],
     ["refactor_procedure"]),

    ("Data Files",
     [".tres", ".tscn", "UID", "ext_resource", "ScriptClass", "sub_resource"],
     ["UID"],
     ["architecture_philosophy"]),

    ("Design Philosophy",
     ["modifier", "stat", "affinity", "design"],
     ["modifier"],
     ["architecture_philosophy"]),

    ("Obsidian/Docs",
     ["Obsidian", "design doc", "lore", "vault"],
     ["Obsidian"],
     ["worklog_reference"]),
]

# Tunables
MIN_WORDS = 50  # Skip trivially small plans
MAX_PLAN_AGE_SECONDS = 60  # Plan file must have been modified within this window


def find_recent_plan() -> str | None:
    """
    Locate the most-recently-modified plan file under ~/.claude/plans/.
    Returns the file content as a string, or None if no fresh plan exists.

    ExitPlanMode just wrote the plan, so the freshest file with mtime within
    MAX_PLAN_AGE_SECONDS is almost certainly the active one.
    """
    plans_dir = Path.home() / ".claude" / "plans"
    if not plans_dir.is_dir():
        return None

    now = time.time()
    candidates = []
    for path in plans_dir.glob("*.md"):
        try:
            mtime = path.stat().st_mtime
            if now - mtime <= MAX_PLAN_AGE_SECONDS:
                candidates.append((mtime, path))
        except OSError:
            continue

    if not candidates:
        return None

    # Most recently modified wins
    candidates.sort(reverse=True)
    try:
        return candidates[0][1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _trigger_pattern(trigger: str) -> re.Pattern:
    """
    Build a case-insensitive matcher for a trigger keyword.

    Short or all-uppercase triggers (acronyms like "AI", "BT", "VFX", "UID")
    use full \\b word boundaries on both sides to prevent matching inside
    ordinary words ("AI" inside "available", "BT" inside "doubt").

    Longer mixed-case triggers ("spell", "trait", "spellbehavior") use \\b
    prefix only — start-of-word match. This lets "spell" match "SpellCrafter"
    (correctly tagging spell-domain) while preventing "craft" from also
    matching "SpellCrafter" (the 'C' is preceded by a word char, no \\b
    there). Identifier camelcase boundaries are not regex word boundaries.

    Heuristic: full bilateral boundary if len <= 4 OR all letters uppercase;
    start-of-word only otherwise.
    """
    needs_strict_boundary = len(trigger) <= 4 or trigger.isupper()
    if needs_strict_boundary:
        return re.compile(rf"\b{re.escape(trigger)}\b", re.IGNORECASE)
    return re.compile(rf"\b{re.escape(trigger)}", re.IGNORECASE)


# Pre-compiled noise-stripping patterns
_OUT_OF_SCOPE_RE = re.compile(
    r"^#{2,}\s*(out of scope|skip|not in scope|excluded|out-of-scope)[^\n]*\n.*?(?=^#{2,}\s|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_FENCED_CODE_RE = re.compile(r"```[^\n]*\n.*?\n```", re.DOTALL)
_CRITICAL_FILES_RE = re.compile(
    r"^#{2,}\s*(critical files|files to modify|files changed|files affected|bounded file list)[^\n]*\n(.+?)(?=^#{2,}\s|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)



def _strip_noise(text: str) -> str:
    """
    Remove sections that don't reflect plan scope:
    - "Out of scope" / "Skip" / "Not in scope" / "Excluded" headings
      (and their content, until the next heading or EOF)
    - Fenced code blocks (triple-backtick) — usually examples or shell
      snippets, not scope statements

    Preserves inline-code spans (single backticks) — those frequently carry
    the actual subject of small plans.
    """
    text = _OUT_OF_SCOPE_RE.sub("", text)
    text = _FENCED_CODE_RE.sub("", text)
    return text


def _extract_critical_files_section(text: str) -> str | None:
    """
    Return the body of a "Critical files" / "Files to modify" / "Files changed"
    / "Files affected" / "Bounded file list" section if present, else None. The
    section body is the most authoritative scope statement when authors include
    it — restricting domain inference to it eliminates false positives from
    prose mentions of out-of-scope symbols.
    """
    m = _CRITICAL_FILES_RE.search(text)
    return m.group(2) if m else None


# Compile triggers once at import time
_DOMAIN_PATTERNS = [
    (name, [_trigger_pattern(t) for t in triggers], memory_keys, skills)
    for name, triggers, memory_keys, skills in DOMAINS
]


def infer_domains(plan_text: str) -> list[tuple[str, list[str], list[str]]]:
    """
    Return list of (domain_name, memory_keywords, skills) for matched domains.
    A plan can match multiple domains. Order preserves DOMAINS table order.
    """
    matched = []
    for domain_name, patterns, memory_keys, skills in _DOMAIN_PATTERNS:
        for pat in patterns:
            if pat.search(plan_text):
                matched.append((domain_name, memory_keys, skills))
                break
    return matched


def build_reminder(matches: list[tuple[str, list[str], list[str]]]) -> str:
    """
    Compose the additionalContext message from matched domains.
    Deduplicates memory keywords and skill names across overlapping domains.
    """
    domain_names = [m[0] for m in matches]
    # Deduplicate while preserving order
    memory_keys: list[str] = []
    skills: list[str] = []
    for _, mk, sk in matches:
        for k in mk:
            if k not in memory_keys:
                memory_keys.append(k)
        for s in sk:
            if s not in skills:
                skills.append(s)

    domains_str = ", ".join(domain_names)
    memory_query = " / ".join(memory_keys)

    parts = [
        f"Plan touches: {domains_str}. Before implementing:",
        "",
        f"• Search auto-memory (semantic-search over .claude/auto-memory) for: {memory_query}.",
    ]

    if skills:
        skills_str = ", ".join(skills)
        parts.append(f"• Load relevant Skills: {skills_str}.")
    else:
        parts.append(
            "• No Skill explicitly keyed to these domain(s) in CLAUDE.md — "
            "auto-memory entries are the primary source."
        )

    parts.append("")
    parts.append(
        "Per CLAUDE.md: if an unexpected result contradicts expected domain "
        "behavior, search Memory before changing approach."
    )

    return "\n".join(parts)


def main() -> None:
    # Read hook stdin
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # Only fire on ExitPlanMode
    if input_data.get("tool_name") != "ExitPlanMode":
        sys.exit(0)

    # Locate the freshest plan file
    plan_text = find_recent_plan()
    if not plan_text:
        sys.exit(0)

    # Skip trivially small plans (likely a quick intent statement, not a real plan)
    word_count = len(re.findall(r"\b\w+\b", plan_text))
    if word_count < MIN_WORDS:
        sys.exit(0)

    # Strip out-of-scope sections + fenced code blocks before matching
    cleaned_text = _strip_noise(plan_text)

    # When a "Critical files" section exists, it IS the authoritative scope —
    # restrict matching to it. Otherwise fall back to the cleaned whole-text.
    target_text = _extract_critical_files_section(cleaned_text) or cleaned_text

    matches = infer_domains(target_text)
    if not matches:
        sys.exit(0)

    # Emit additionalContext payload
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": build_reminder(matches),
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


if __name__ == "__main__":
    main()
