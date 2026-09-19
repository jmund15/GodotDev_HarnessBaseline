#!/usr/bin/env python3
"""
Hook: PostToolUse on Write/Edit of a plan file —
Inject memory + skill + rule reminders for inferred domains.

Why:
- CLAUDE.md mandates "search auto-memory and load relevant Skills before planning"
  but enforcement is self-discipline. The /plan_check command covers high-stakes
  plans; this hook covers the routine plans below /plan_check's litmus.
- The plan FILE is the one artifact every planning path produces, so it is the
  only reliable trigger. (Plan Mode is retired — see
  feedback_plan_mode_retired_from_planning_flow — so the former ExitPlanMode
  matcher is gone and this is the sole registration.)

What it does:
- Triggers on a Write/Edit whose file_path is under .claude/plans/ with a .md
  suffix (once per plan file per session), using the written text and falling
  back to the plan file on disk when the edit fragment is too small to infer from.
- Pre-processes plan text: strips "Out of scope" sections and fenced code
  blocks (their content is examples/exclusions, not scope statements).
- Resolves the "Critical files" / "Files to modify" / "Files changed" section from
  the WHOLE plan file first and the edit fragment only as a fallback, then restricts
  domain inference to that section (highest-precision scope signal). A section-by-
  section edit of a harness plan otherwise sees prose alone and infers game domains
  from incidental words.
- If the authoritative files section contains only `.claude/` paths, treats the
  plan as explicit harness/meta scope and emits no game-domain reminder. A mixed
  files section still receives normal domain inference. With no files section at
  all, an all-`.claude/` path set is the same proof.
- Infers domains by case-insensitive start-of-word matching against the
  keyword sets in .claude/reference/memory_domains.md, the documented home for
  this table (a hook enforces, it never legislates). Substring
  matching across identifier camelcase boundaries is rejected (so "craft"
  inside "AbilityBuilder" does NOT fire the Crafting domain).
- Emits a hookSpecificOutput.additionalContext payload listing matched domains,
  auto-memory search queries, any Skills to load, and any rules/ files to read.

Boundaries:
- Never blocks. Always exits 0.
- Silent if no domains match, plan file is missing/stale, or plan is < 50 words.
- Skill suggestions are deduplicated across multiple matched domains.
- 5s file-find timeout via mtime check, not subprocess timeout.

Wired in: settings.json hooks.PostToolUse with matcher "Write|Edit".
"""

import json
import os
import re
import sys
from pathlib import Path

from _hook_state import state_path, update_json_locked

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
import adaptation  # noqa: E402

# Domain inference table. The entries below are the domain-agnostic floor; a project's own content
# domains are appended from `adaptation.json` `memory_domains` (Design Doc §8) rather than edited into
# this list. `.claude/reference/memory_domains.md` renders the same rows for readers.
#
# Each entry: (display_name, [trigger_keywords], [memory_search_keywords],
#              [skills_to_load], [rule_paths])   — [rule_paths] is optional and
# may be omitted entirely (treated as []), so 4-tuple entries stay valid for any
# external reader of this table (e.g. /plan_check Phase 1c mirrors it).
# Trigger matching is case-insensitive and starts at a word boundary; short or
# uppercase acronyms also require an ending boundary.
DOMAINS = [
    ("Refactoring",
     ["refactor", "deprecate", "migrate", "rename", "extract", "consolidate"],
     ["refactor"],
     []),

    ("Obsidian/Docs",
     ["Obsidian", "design doc", "vault"],
     ["Obsidian"],
     ["worklog_reference"]),
]


def _append_memory_domains(domains: list) -> None:
    """Merge `adaptation.json` `memory_domains` rows -- each `{name, triggers,
    memory_keywords, skills, rules}` -- into `DOMAINS` in place, as
    `(name, triggers, memory_keywords, skills, rules)` 5-tuples. A row whose `name` matches an
    existing floor entry REPLACES it in place, rather than adding a second entry with the same
    name that would double-count a match; a new name appends. A row missing `name` or
    `triggers`, or whose list fields are not lists, is skipped with one stderr line; this
    hook must never crash on a malformed seed."""
    claude_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for row in adaptation.get(claude_dir, "memory_domains"):
        if not isinstance(row, dict) or "name" not in row or "triggers" not in row:
            print(f"plan_memory_reminder: memory_domains row {row!r} is missing "
                  "'name' or 'triggers' -- skipped", file=sys.stderr)
            continue
        triggers = row.get("triggers")
        memory_keywords = row.get("memory_keywords", [])
        skills = row.get("skills", [])
        rules = row.get("rules", [])
        if not all(isinstance(v, list) for v in (triggers, memory_keywords, skills, rules)):
            print(f"plan_memory_reminder: memory_domains row {row['name']!r} has a "
                  "non-list field -- skipped", file=sys.stderr)
            continue
        entry = (row["name"], triggers, memory_keywords, skills, rules)
        for i, existing in enumerate(domains):
            if existing[0] == row["name"]:
                domains[i] = entry
                break
        else:
            domains.append(entry)


_append_memory_domains(DOMAINS)

# Tunables
MIN_WORDS = 50  # Skip trivially small plans
MAX_PLAN_AGE_SECONDS = 60  # Plan file must have been modified within this window

# Session dedupe for the Write/Edit plan-file branch. A drive command rewrites
# its plan file many times; the reminder is worth exactly one fire per plan.
# Shares the per-session routing-state file used by the other hooks
# (critical_analysis_reminder, prompt_memory_loader); other fields are
# preserved across the read-modify-write.
PLAN_FILES_FIELD = "plan_memory_reminder_files"
PLAN_FILES_CAP = 50  # bounded state — oldest entries drop off


def _state_path(session_id: str) -> str:
    return state_path(session_id)


def _claim_plan_file(session_id: str, plan_key: str) -> bool:
    """
    True the first time this session emits for `plan_key`, False afterwards.
    Best-effort: returns True on any state I/O failure (fail-open toward
    reminding — a duplicate nudge is cheaper than a silent miss).
    """
    def claim(state):
        seen = state.get(PLAN_FILES_FIELD)
        if not isinstance(seen, list):
            seen = []
        if plan_key in seen:
            return False
        seen.append(plan_key)
        state[PLAN_FILES_FIELD] = seen[-PLAN_FILES_CAP:]
        return True

    written, claimed = update_json_locked(_state_path(session_id), claim)
    return claimed if written else True


def _plan_file_key(file_path: str) -> str | None:
    """
    Posix-normalized path if `file_path` names a plan file under
    `.claude/plans/` with a .md suffix, else None.
    """
    if not file_path:
        return None
    normalized = str(file_path).replace("\\", "/")
    if ".claude/plans/" in normalized and normalized.lower().endswith(".md"):
        return normalized
    return None


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _trigger_pattern(trigger: str) -> re.Pattern:
    """
    Build a case-insensitive matcher for a trigger keyword.

    Short or all-uppercase triggers (acronyms like "AI", "BT", "VFX", "UID")
    use full \\b word boundaries on both sides to prevent matching inside
    ordinary words ("AI" inside "available", "BT" inside "doubt").

    Longer mixed-case triggers ("spell", "trait", "spellbehavior") use \\b
    prefix only — start-of-word match. This lets "spell" match "AbilityBuilder"
    (correctly tagging spell-domain) while preventing "craft" from also
    matching "AbilityBuilder" (the 'C' is preceded by a word char, no \\b
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
# A path is a slashed token ending in a file extension or a slash, or a bare file with a lettered
# extension. Prose such as `e.g.`, `read/write`, `0.1.36` and `3.5` is not a path.
_PATH_LIKE_RE = re.compile(
    r"(?<![\w.*{}~+@:/\\-])(?:[A-Za-z]:)?(?:"
    r"(?:[\w.*{}~+@:-]+[/\\])+[\w*{}~+@:-]*\.[A-Za-z][\w-]{0,15}"
    r"|(?:[\w.*{}~+@:-]+[/\\])+(?![\w.*{}~+@:-])"
    r"|[A-Za-z_][\w*{}~+@-]*\.[A-Za-z][A-Za-z0-9]{1,9}\b)",
    re.IGNORECASE,
)


def _is_harness_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith(".claude/") or "/.claude/" in lowered


def _is_explicit_meta_scope(files_section: str) -> bool:
    """True when an authoritative files section scopes work only to `.claude/`.

    A file name may contain game words (`status`, `return`, `AI`) without entering
    a game domain. Mixed sections remain eligible for normal inference.
    """
    normalized = files_section.replace("\\", "/")
    if ".claude/" not in normalized.lower():
        return False
    return all(_is_harness_path(path) for path in _PATH_LIKE_RE.findall(normalized))


def _paths_are_all_harness(text: str) -> bool:
    """True when the plan names at least one path and every one is under `.claude/`.

    Backstop for a plan with no files section: a harness plan's prose says "critter",
    "status" and "refactor" while nothing it will touch is game code, and the paths are
    the only scope statement such a plan carries.
    """
    paths = _PATH_LIKE_RE.findall(text.replace("\\", "/"))
    return bool(paths) and all(_is_harness_path(path) for path in paths)



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


# Compile triggers once at import time. Entries may omit the trailing rules
# list — normalize to a uniform 5-slot shape here.
_DOMAIN_PATTERNS = [
    (
        entry[0],
        [_trigger_pattern(t) for t in entry[1]],
        entry[2],
        entry[3],
        entry[4] if len(entry) > 4 else [],
    )
    for entry in DOMAINS
]


def infer_domains(plan_text: str) -> list[tuple[str, list[str], list[str], list[str]]]:
    """
    Return list of (domain_name, memory_keywords, skills, rules) for matched
    domains. A plan can match multiple domains. Order preserves DOMAINS order.
    """
    matched = []
    for domain_name, patterns, memory_keys, skills, rules in _DOMAIN_PATTERNS:
        for pat in patterns:
            if pat.search(plan_text):
                matched.append((domain_name, memory_keys, skills, rules))
                break
    return matched


def build_reminder(matches: list[tuple[str, list[str], list[str], list[str]]]) -> str:
    """
    Compose the additionalContext message from matched domains.
    Deduplicates memory keywords, skill names, and rule paths across
    overlapping domains.
    """
    domain_names = [m[0] for m in matches]
    # Deduplicate while preserving order
    memory_keys: list[str] = []
    skills: list[str] = []
    rules: list[str] = []
    for _, mk, sk, rl in matches:
        for k in mk:
            if k not in memory_keys:
                memory_keys.append(k)
        for s in sk:
            if s not in skills:
                skills.append(s)
        for r in rl:
            if r not in rules:
                rules.append(r)

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

    if rules:
        parts.append(f"• Read rule(s): {', '.join(rules)}.")

    parts.append("")
    parts.append(
        "Per CLAUDE.md: if an unexpected result contradicts expected domain "
        "behavior, search Memory before changing approach."
    )

    return "\n".join(parts)


def process(input_data):
    """Dispatcher entry: `{"context": reminder}` for a plan's inferred domains, else None.

    `post_edit_dispatch.py` calls this; `main()` keeps the standalone channel.
    """
    if input_data.get("tool_name") not in ("Write", "Edit"):
        return None

    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    # The plan file IS the trigger: every planning path writes .claude/plans/*.md.
    plan_key = _plan_file_key(tool_input.get("file_path", ""))
    if not plan_key:
        return None

    fragment = _strip_noise(tool_input.get("content") or tool_input.get("new_string") or "")
    try:
        whole = _strip_noise(Path(tool_input["file_path"]).read_text(
            encoding="utf-8", errors="replace"))
    except (OSError, KeyError):
        whole = ""

    # The plan's declared scope belongs to the WHOLE file, never to whichever section
    # this edit happened to rewrite: a harness plan edited section by section shows the
    # dispatcher only prose, and its authoritative files list sits outside the fragment.
    files_section = (_extract_critical_files_section(whole)
                     or _extract_critical_files_section(fragment))
    if files_section is not None and _is_explicit_meta_scope(files_section):
        return None
    if files_section is None and _paths_are_all_harness(whole or fragment):
        return None

    # A surgical Edit fragment is too thin to infer scope from — fall back to the file.
    body = fragment if _word_count(fragment) >= MIN_WORDS else whole
    if _word_count(body) < MIN_WORDS:
        return None

    matches = infer_domains(files_section or body)
    if not matches:
        return None

    # Claim only an emitted reminder. A silent meta draft may later become mixed.
    if not _claim_plan_file(input_data.get("session_id", "") or "", plan_key):
        return None

    return {"context": build_reminder(matches)}


def main() -> None:
    # Read hook stdin
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    result = process(input_data) or {}
    if result.get("context"):
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": result["context"],
            }
        }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # Advisory hook — never block on an internal error
