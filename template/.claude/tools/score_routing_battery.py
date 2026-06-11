#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score_routing_battery.py — scoring helper for the 40-test routing-compliance
battery (.claude/tests/routing_compliance.md).

Designed for the v2 paired-comparison methodology: run the battery twice (once
with all routing hooks active via settings.json, once with them disabled via
settings.local.json override or commented-out matchers), score each run, then
compare per-test deltas to distinguish hook-driven improvement from doctrine-
loaded improvement.

Usage:
    python3 score_routing_battery.py <responses.json> [<agent_ids.json>] [--baseline <baseline.json>]

Input format (responses.json):
    {
      "A1": "<full subagent response text including [TOOLS-USED: ...] header>",
      "A2": "...",
      ...
      "M4": "..."
    }

Optional agent_ids.json (per /routing_battery capture):
    {
      "A1": {"agent_id": "a3c1609323939f093", "session_id": "8e953228-..."},
      "A2": {"agent_id": "a1e242d9d03a21203", "session_id": "8e953228-..."},
      ...
    }

The scorer is **transcript-only**. agent_ids.json is required for
meaningful grades; without it every row scores NO-TOOL-CALLS (since the
scorer has no signal channel to verify what tools were actually called).

Rows graded NO-TOOL-CALLS require **orchestrator audit** — the human/agent
running the battery reads the response prose for those tests and applies
judgment to classify each as PASS (planning-style M/N answer named the
right tool), PASS-O (override-justified), FLAG (subagent toolkit was
incomplete — environmental issue, not a doctrine failure), or FAIL.

The orchestrator audit step is intentionally outside the scorer because
the nuance requires reading the agent's reasoning, not just its tool list.

When --baseline is given, prints per-test deltas alongside.
"""

import json
import re
import sys
from pathlib import Path

# Allow importing extract_subagent_tools from same directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from extract_subagent_tools import extract_tool_names
    TRANSCRIPT_EXTRACTION_AVAILABLE = True
except ImportError:
    TRANSCRIPT_EXTRACTION_AVAILABLE = False

# --- Test rubric ----------------------------------------------------------

# Expected first substantive tool by test ID. Multiple acceptable tools
# expressed as set; agent gets PASS if first call is in the set.
#
# C-category note (updated 2026-05-03 with the anchor-then-navigate doctrine
# revision in CLAUDE.md §9 + csharp_lsp.md): for "find a C# symbol" tests,
# BOTH (a) `Grep("class FooBar\b")` as the legitimate anchor → LSP from there,
# AND (b) direct LSP with a known filePath are correct first moves. The bypass
# smell is *bare* `Grep("FooBar")` with no kind anchor — that's detected by
# `tool_routing_post_grep.py` via pattern inspection (which the scorer can't see
# from TOOLS-USED). At rubric level, accept either Grep or LSP for C-tests; the
# hook is the actual bare-vs-anchored gatekeeper.
#
# J1 follows the same rule (refactor planning includes a C# symbol-discovery
# step), so its set also includes Grep.
#
# Memory-recall note (updated 2026-05-31, Memory-MCP retirement): the Memory MCP
# server is gone — memory recall is now `semantic-search` over .claude/auto-memory
# (per CLAUDE.md §2/§9). So the memory tests (A2, A3, G1, G2) expect `semantic-search`,
# the SAME canonical label as code-discovery tests (D/E/I/J). The scorer is
# transcript-only and grades by tool NAME, so it cannot distinguish "searched
# memory" from "searched code" — the real signal is the `restrictToDir=.claude/auto-memory`
# ARG (arg-extraction is a worklog item, same as M4/N4). The legacy `memory_search`
# bucket is retained in _normalize() ONLY to re-score frozen pre-retirement capture
# JSONs; those historical rows now grade FAIL on memory tests, which correctly
# reflects that they used a now-defunct tool (do not "fix" by re-adding memory_search
# to EXPECTED — that would assert a retired tool is still correct routing).
EXPECTED = {
    "A1": {"read_files"},
    "A2": {"read_files", "semantic-search"},
    "A3": {"semantic-search", "read_files"},
    "B1": {"Read", "LSP"},
    "B2": {"obsidian_read_note"},
    "C1": {"LSP", "Grep"},
    "C2": {"LSP", "Grep"},
    "C3": {"LSP", "Grep"},
    "D1": {"semantic-search"},
    "D2": {"semantic-search"},
    "E1": {"semantic-search"},
    "E2": {"semantic-search"},
    "E3": {"semantic-search"},
    "F1": {"Grep"},
    "F2": {"Grep"},
    "F3": {"Grep"},
    "F4": {"Grep"},
    "G1": {"semantic-search"},
    "G2": {"read_files", "semantic-search"},
    "H1": {"read_files"},
    "H2": {"read_files"},
    "I1": {"semantic-search"},
    "J1": {"semantic-search", "LSP", "Grep"},
    "J2": {"semantic-search"},
    "K1": {"Grep"},
    "K2": {"obsidian_read_note"},
    # L-category: negative cases (where the dominant tool would be WRONG).
    # Each L-test verifies the agent recognizes the routing rule's boundary
    # and picks a tool the rule normally discourages. PASS = agent picked the
    # boundary-correct tool; FAIL = agent reflexively picked the dominant tool.
    "L1": {"Read", "LSP"},          # audit-line-by-line — Read direct, NOT read_files
    "L2": {"WebFetch"},              # single-URL — WebFetch, NOT read_web
    "L3": {"Glob", "Bash"},          # file-system count — Glob/Bash, NOT a semantic-search over .claude/auto-memory
    "L4": {"Grep"},                  # method-body literal — Grep, NOT LSP
    "L5": {"Grep", "LSP"},           # known-anchor lookup — anchor-Grep or LSP, NOT semantic-search
    "L6": {"Grep"},                  # verified-unique-name override — Grep with PASS-O

    # L7–L10: don't-delegate negatives (added 2026-05-03 alongside M-category).
    # CLAUDE.md Documentation Delegation Rule has carve-outs for .claude/ files,
    # one-line tweaks, and orchestrator-context-unique tasks. PASS = agent uses
    # direct Edit/Write rather than routing through write_doc/write_code.
    "L7": {"Edit"},                  # .claude/CLAUDE.md edit — Edit direct, NOT write_doc
    "L8": {"Edit"},                  # skill SKILL.md edit — Edit direct, NOT write_doc
    "L9": {"Edit"},                  # one-line typo — Edit direct, NOT write_doc (overhead carve-out)
    "L10": {"Write", "write_doc"},  # audit findings (markdown report = doc prose) — Write direct preferred; write_doc with explicit cost-justification = PASS-O

    # M-category: documentation delegation tests (positive — must DELEGATE).
    # Verifies CLAUDE.md Documentation Delegation Rule (HARD) is internalized.
    # Model selection (kimi vs deepseek) is soft-graded for v1 — tool-name match
    # only; arg-extraction for `model="kimi"` is a worklog item.
    #
    # M1 expected was {"write_code"} pre-2026-05-05 (legacy `write_model` mapping
    # treated new doc creation as boilerplate). Per current global CLAUDE.md the
    # split is: write_code = NEW boilerplate CODE/config; write_doc = creating OR
    # updating doc PROSE. Architecture docs are doc prose → write_doc.
    "M1": {"write_doc"},             # new architecture doc creation — write_doc (prose)
    "M2": {"write_doc"},             # mechanical doc update — write_doc, NOT Edit
    "M3": {"write_doc", "write_code"},  # cross-domain retrospective — either
    "M4": {"read_files", "Read"},    # doc update needing code interp — Path A (read_files→spec) or Path B (Read→spec)
                                     # NOTE: this expected set passes if the agent picks Read OR read_files FIRST,
                                     # before any write_doc call. The anti-pattern (write_doc with reference_files=[.cs])
                                     # would surface write_doc as the FIRST call without prior code-interpretation —
                                     # graded FAIL. Strict M4 grading would parse write_doc args for reference_files
                                     # containing .cs paths; that's a worklog item.

    # N-category: code-generation delegation tests (write_code boundaries).
    # Parallel structure to M-category but for the code surface. N1+N2 positive
    # (delegate boilerplate); N3+N4 negative (don't delegate when orchestrator
    # holds session-unique judgment, or when worker would need to interpret
    # existing code). Same prose-extraction signal channel as M.
    "N1": {"write_code"},            # new test stub scaffold — write_code (canonical positive)
    "N2": {"write_code"},            # new .runsettings — write_code with legitimate reference_files=[exemplar]
    "N3": {"Write", "Edit"},         # production logic with project invariants — direct Write, NOT write_code
                                     # PASS-O if agent describes write_code for the test stub specifically
                                     # (TDD discipline) but Write for the production class.
    "N4": {"read_files", "Read"},    # stub for existing class — Read/digest BEFORE write_code (mirrors M4)
                                     # Anti-pattern: write_code(reference_files=[.cs]) — pushes API interpretation
                                     # onto the worker. Same arg-extraction worklog item as M4.
}


def _category(tid: str) -> str:
    return tid[0]


# Tools to skip when finding the "first substantive call". ToolSearch is
# Claude Code's deferred-tool-schema-loader (infrastructure, not work);
# TodoWrite is task tracking, not investigation work.
SKIP_TOOLS = {"TodoWrite", "ToolSearch"}

# Discovery-shape tools — used to *find* a file/symbol before routing to the
# correct synthesis/navigation tool. We don't skip these by default (an agent
# that *only* uses Glob is genuinely failing to bundle), but we DO skip them
# when followed within 1 call by the EXPECTED tool — that pattern indicates
# "agent had to discover the path first, then routed correctly," which the
# rubric should not penalize as a routing failure (e.g., A1 with a corrupted
# path in the prompt forced obsidian_global_search before read_files).
DISCOVERY_TOOLS = {"Glob", "obsidian_global_search"}

# Error-substring patterns that indicate a tool call was made with correct
# intent but failed at the call layer (schema error, file not found, empty
# result). Used to grade `PASS-NOMINAL` when first_call matches expected
# but the call returned an error and the agent silently fell through.
# Case-insensitive substring match against full response text.
ERROR_SUBSTRINGS = (
    "path is not a file",
    "no matching deferred tools found",
    "file not found",
    "no such file or directory",
    "0 results",
    "no results",
    "no symbols found",
    "tool error",
    "error: ",
    "no matches found",
)

# Override-justification cue phrases that elevate FAIL → PASS-O when the
# agent picked the "wrong" tool but cited the rule and explained the override.
# Match against full response text (case-insensitive substring).
OVERRIDE_JUSTIFICATION_PHRASES = (
    "per claude.md §10",
    "per claude.md section 10",
    "per claude.md §9",
    "per claude.md section 9",
    "per project claude.md",
    "anti-pattern shape",
    "documented anti-pattern",
    "documented override",
    "literal scan",
    "comment-mention coverage",
    "lsp filters comments out",
    "override-justification",
    "k1-style",
    "deliberately overrid",  # matches "overriding"/"overrode"
)


# --- Tool normalization --------------------------------------------------

def _normalize(tool: str) -> str:
    """
    Map TOOLS-USED tool labels to canonical scoring labels.

    Handles three name shapes that all refer to the same tool:
      1. Fully-qualified MCP form (`mcp__<server>__<tool>`) — what JSONL
         transcripts emit.
      2. Stripped-prefix form (`<server>__<tool>`) — what subagents
         sometimes emit in `[TOOLS-USED:]` headers (G1 failure mode).
      3. Bare-name form (`<tool>`) — when subagents abbreviate further
         (e.g. `semantic-search` for the full path).
    """
    t = tool.strip()
    if t in SKIP_TOOLS:
        return ""

    # LSP variants — multiple wrapper forms across the LSP MCP plugin
    if t == "LSP" or t.startswith("LSP ") or t.startswith("mcp__plugin_lsp") or t.startswith("mcp__lsp"):
        return "LSP"

    # ai-worker family (full + stripped + bare forms)
    #
    # Legacy aliases (`ask_model` / `fetch_and_ask` / `write_model` / `update_doc`)
    # are deliberately preserved here as historical-data matchers — sessions
    # logged before the 2026-05-04 rename used those names. They normalize to
    # the same canonical buckets as the current names so per-tool stats remain
    # comparable across the migration boundary. Do NOT remove the legacy entries
    # without re-classifying the entire historical session corpus.
    if t in ("mcp__ai-worker__read_files", "ai-worker__read_files", "read_files",
             "mcp__ai-worker__ask_model",  "ai-worker__ask_model",  "ask_model"):
        return "read_files"
    if t in ("mcp__ai-worker__write_code", "ai-worker__write_code", "write_code",
             "mcp__ai-worker__write_model", "ai-worker__write_model", "write_model"):
        return "write_code"
    if t in ("mcp__ai-worker__write_doc", "ai-worker__write_doc", "write_doc",
             "mcp__ai-worker__update_doc", "ai-worker__update_doc", "update_doc"):
        return "write_doc"
    if t in ("mcp__ai-worker__read_web", "ai-worker__read_web", "read_web",
             "mcp__ai-worker__fetch_and_ask", "ai-worker__fetch_and_ask", "fetch_and_ask"):
        return "read_web"

    # semantic-search (full + stripped + bare forms)
    if t in (
        "mcp__plugin_semantic-search_semantic-search__search",
        "plugin_semantic-search_semantic-search__search",
        "semantic-search__search",
        "semantic-search",
    ):
        return "semantic-search"

    # obsidian family (full + stripped forms)
    if t in ("mcp__obsidian__obsidian_read_note", "obsidian__obsidian_read_note", "obsidian_read_note"):
        return "obsidian_read_note"
    if t in ("mcp__obsidian__obsidian_global_search", "obsidian__obsidian_global_search", "obsidian_global_search"):
        return "obsidian_global_search"

    # memory family — HISTORICAL-ONLY (Memory MCP retired 2026-05-31).
    # These map old Memory-MCP tool names to the `memory_search` bucket purely so
    # frozen pre-retirement capture JSONs still parse. No current EXPECTED entry
    # references `memory_search` — modern memory recall is `semantic-search` over
    # .claude/auto-memory (see the Memory-recall note above EXPECTED). Keep these
    # for historical re-scoring; do NOT route any new test's expectation here.
    # (Original G1 failure mode: the stripped form `memory__search_nodes` not
    # normalizing — retained below.)
    if t in (
        "mcp__memory__search_nodes", "memory__search_nodes",
        "mcp__memory__open_nodes", "memory__open_nodes",
        "mcp__memory__read_graph", "memory__read_graph",
        "memory_search",
    ):
        return "memory_search"

    # Built-in tools that pass through unchanged
    if t in ("Read", "Grep", "Glob", "Bash", "Edit", "Write", "WebSearch", "WebFetch"):
        return t
    # Unknown — keep raw for visibility in the report
    return t


# --- Parsing ------------------------------------------------------------

def extract_tools_used(
    response: str,
    agent_info: dict | None = None,
) -> tuple[list[str], str]:
    """
    Return (tool_list, signal_source) where signal_source is one of:
      "transcript" — from the subagent's JSONL via extract_subagent_tools.
      "none"       — no transcript signal available (no agent_id captured,
                     or JSONL inaccessible, or agent made zero tool calls).

    The scorer is **transcript-only**. The agent's response prose IS the
    answer for planning-style categories (M/N) and the agent's named-but-
    not-called tools elsewhere — but those are nuance signals that require
    judgment. The scorer reports them as "none"; the orchestrator resolves
    them in the post-scoring audit step (see /routing_battery report flow).

    Why no header/prose extraction here: the `[TOOLS-USED:]` header conflated
    "tools I called" with "tools I think the right answer is" — agents
    reliably named the correct tool even when their toolkit prevented
    actually calling it, producing false-positive PASS grades. Pulling the
    nuance out of the scorer makes the deterministic grade honest and
    surfaces ambiguous cases for explicit human/orchestrator review.

    `agent_info` shape: {"agent_id": str, "session_id": str | None}
    """
    if (
        agent_info
        and TRANSCRIPT_EXTRACTION_AVAILABLE
        and agent_info.get("agent_id")
    ):
        transcript_tools = extract_tool_names(
            agent_id=agent_info["agent_id"],
            session_id=agent_info.get("session_id"),
        )
        if transcript_tools:
            return transcript_tools, "transcript"

    return [], "none"


def first_substantive(tools: list[str], expected: set[str] | None = None) -> str:
    """
    Pick the first non-skipped tool call.

    With `expected` provided, walks forward through ANY number of consecutive
    DISCOVERY_TOOLS (Glob, obsidian_global_search) and credits the first
    non-discovery call IF it lands in `expected`. Rationale: an agent that
    runs N path-discovery calls and THEN bundles into the expected synthesis
    tool is executing the rule's intended pattern (search → bundle), not
    failing it. The harm-model the rule targets is per-call orchestrator-
    context bloat from Read/read_files calls — discovery-shape tools
    (Glob/global_search) return paths/result lists, not file contents, so
    multiple of them do not cause that harm.

    Pre-2026-05-05: this only walked one step (substantive[0]→[1]), which
    graded H1 (6 Globs + read_files) and A2 (2 obsidian_global_search +
    read_files) as FAIL despite their correct terminal pattern. The forward
    walk corrects that.

    Without `expected`, returns the first non-skipped call as before
    (back-compat with any external callers).
    """
    substantive = []
    for t in tools:
        norm = _normalize(t)
        if norm:
            substantive.append(norm)

    if not substantive:
        return "(none)"

    # Discovery-skip — walk forward past consecutive discovery-shape tools.
    # Only credits the post-discovery call when it lands in `expected`; if
    # the agent never reached the expected tool, return the first call so
    # the FAIL grade reflects the real cascade.
    if expected and len(substantive) >= 2:
        i = 0
        while i < len(substantive) and substantive[i] in DISCOVERY_TOOLS:
            i += 1
        # i > 0 guard: if the first call is already non-discovery, no rescue
        # needed — fall through to the standard return below.
        if i > 0 and i < len(substantive) and substantive[i] in expected:
            return substantive[i]

    return substantive[0]


def has_override_justification(response: str) -> bool:
    lowered = response.lower()
    return any(phrase in lowered for phrase in OVERRIDE_JUSTIFICATION_PHRASES)


def has_error_substring(response: str) -> bool:
    """Detects whether any tool call in the response failed (schema error,
    empty result, file not found). Used to downgrade PASS → PASS-NOMINAL."""
    lowered = response.lower()
    return any(sub in lowered for sub in ERROR_SUBSTRINGS)


def has_tools_used_header(response: str) -> bool:
    """True iff the response contains a [TOOLS-USED: ...] header."""
    return bool(re.search(r"\[TOOLS-USED:\s*[^\]]*\]", response))


# --- Scoring ------------------------------------------------------------

def score_one(
    tid: str,
    response: str,
    agent_info: dict | None = None,
) -> dict:
    expected = EXPECTED.get(tid, set())
    tools, signal_source = extract_tools_used(response, agent_info)
    first = first_substantive(tools, expected)
    discovery_rescued = (
        len(tools) > 0
        and first != first_substantive(tools)  # i.e., discovery-skip activated
    )

    if signal_source == "none":
        # No transcript signal — either no agent_id provided, JSONL
        # inaccessible, or the agent made zero tool calls. Distinct from FAIL
        # (which means we DID see calls and they were wrong). NO-TOOL-CALLS
        # is the trigger for orchestrator audit: read the response prose to
        # decide PASS (planning-style M/N), PASS-O (override-justified),
        # FLAG (tool unavailable in subagent toolkit), or FAIL.
        grade = "NO-TOOL-CALLS"
    elif first in expected:
        # Routing intent matched. If the call returned an error and the agent
        # fell through (visible in response text), grade PASS-NOMINAL to flag
        # the silent-fallback gap to the reader without changing pass count.
        grade = "PASS-NOMINAL" if has_error_substring(response) else "PASS"
        if discovery_rescued:
            # Mark the rescue so reports show why this PASS isn't a strict
            # first-call match.
            grade = "PASS-D" if grade == "PASS" else "PASS-ND"
    elif has_override_justification(response):
        grade = "PASS-O"
    else:
        grade = "FAIL"

    return {
        "test": tid,
        "category": _category(tid),
        "first_call": first,
        "expected": " or ".join(sorted(expected)),
        "tool_count": len(tools),
        "grade": grade,
        "signal_source": signal_source,
    }


def score_all(
    responses: dict,
    agent_ids: dict | None = None,
) -> list[dict]:
    """
    `agent_ids` shape (optional): {test_id: {"agent_id": str, "session_id": str | None}}
    When provided, each test's scoring uses transcript extraction.
    Without it, every test grades NO-TOOL-CALLS pending orchestrator audit.
    """
    agent_ids = agent_ids or {}
    rows = [
        score_one(tid, resp, agent_ids.get(tid))
        for tid, resp in responses.items()
        if tid in EXPECTED
    ]
    rows.sort(key=lambda r: (r["category"], r["test"]))
    return rows


def render_report(rows: list[dict], baseline_rows: list[dict] | None = None) -> str:
    out = []
    baseline_by_id = {r["test"]: r for r in (baseline_rows or [])}
    has_baseline = baseline_rows is not None

    # Per-test table
    if has_baseline:
        out.append(f"{'Test':<6} {'Cat':<4} {'Grade':<8} {'Δ':<6} {'First call':<22} {'Expected':<28} {'Calls'}")
    else:
        out.append(f"{'Test':<6} {'Cat':<4} {'Grade':<8} {'First call':<22} {'Expected':<28} {'Calls'}")
    out.append("-" * (98 if has_baseline else 90))

    for r in rows:
        if has_baseline:
            b = baseline_by_id.get(r["test"], {})
            b_grade = b.get("grade", "?")
            delta = ""
            if b_grade != r["grade"]:
                delta = f"{b_grade}→{r['grade']}"[:5]
            out.append(
                f"{r['test']:<6} {r['category']:<4} {r['grade']:<8} {delta:<6} "
                f"{r['first_call']:<22} {r['expected']:<28} {r['tool_count']}"
            )
        else:
            out.append(
                f"{r['test']:<6} {r['category']:<4} {r['grade']:<8} "
                f"{r['first_call']:<22} {r['expected']:<28} {r['tool_count']}"
            )

    # Per-category breakdown — all PASS-flavored grades count as a pass.
    out.append("")
    out.append("Per-category pass rate (PASS + variants + PASS-O):")
    pass_grades = {"PASS", "PASS-NOMINAL", "PASS-D", "PASS-ND", "PASS-O"}
    by_cat: dict[str, list[str]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["grade"])
    for cat in sorted(by_cat.keys()):
        grades = by_cat[cat]
        passes = sum(1 for g in grades if g in pass_grades)
        total = len(grades)
        out.append(f"  {cat}: {passes}/{total} ({passes * 100 // total}%)")

    # Summary
    total = len(rows)
    by_grade: dict[str, int] = {}
    for r in rows:
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
    pass_total = sum(by_grade.get(g, 0) for g in pass_grades)
    summary = (
        f"Summary: {pass_total}/{total} PASS-flavored "
        f"(PASS={by_grade.get('PASS', 0)}, "
        f"PASS-NOMINAL={by_grade.get('PASS-NOMINAL', 0)}, "
        f"PASS-D={by_grade.get('PASS-D', 0)}, "
        f"PASS-ND={by_grade.get('PASS-ND', 0)}, "
        f"PASS-O={by_grade.get('PASS-O', 0)}), "
        f"FAIL={by_grade.get('FAIL', 0)}, "
        f"NO-TOOL-CALLS={by_grade.get('NO-TOOL-CALLS', 0)} / total {total}"
    )
    out.append("")
    out.append(summary)

    # Grade legend.
    out.append("")
    out.append("Grade legend:")
    out.append("  PASS          = first substantive call matched expected; call succeeded")
    out.append("  PASS-NOMINAL  = first call matched expected but returned an error (silent fallback) — investigate")
    out.append("  PASS-D        = discovery-skip rescue: N consecutive Glob/obsidian_global_search")
    out.append("                  preludes, then first non-discovery call routed correctly")
    out.append("                  (path-discovery before bundled synthesis, not a routing failure)")
    out.append("  PASS-ND       = PASS-D + PASS-NOMINAL (rescued AND the rescued call had an error)")
    out.append("  PASS-O        = wrong tool but agent cited CLAUDE.md §9 + override justification")
    out.append("  FAIL          = wrong tool, no override justification, no rescue")
    out.append("  NO-TOOL-CALLS = no transcript signal (zero tool calls OR JSONL inaccessible)")
    out.append("                  -> ORCHESTRATOR AUDIT required: read response prose to classify as")
    out.append("                  PASS (planning-style M/N answer), PASS-O (override-justified),")
    out.append("                  FLAG (tool-availability refusal -- not a failure), or FAIL.")

    # Signal-source breakdown (how the scorer obtained each row's tool list)
    sources: dict[str, int] = {}
    for r in rows:
        sources[r.get("signal_source", "?")] = sources.get(r.get("signal_source", "?"), 0) + 1
    if sources:
        out.append("")
        out.append("Signal source breakdown:")
        for src in ("transcript", "none"):
            n = sources.get(src, 0)
            label = {
                "transcript": "deterministic (subagent JSONL)",
                "none": "no transcript signal — orchestrator audit required",
            }[src]
            out.append(f"  {src:<11} {n:>3} — {label}")

    # Paired-delta summary
    if has_baseline:
        flips_to_pass = sum(
            1 for r in rows
            if r["grade"] in pass_grades
            and baseline_by_id.get(r["test"], {}).get("grade") == "FAIL"
        )
        flips_to_fail = sum(
            1 for r in rows
            if r["grade"] == "FAIL"
            and baseline_by_id.get(r["test"], {}).get("grade") in pass_grades
        )
        out.append("")
        out.append(
            f"Paired delta vs baseline: {flips_to_pass} flipped FAIL→PASS, "
            f"{flips_to_fail} regressed PASS→FAIL"
        )

    return "\n".join(out)


def _parse_args(argv: list[str]) -> tuple[str, str | None, str | None]:
    """
    Parse positional + flag args. Returns (responses_path, agent_ids_path, baseline_path).

    Supported forms:
      score_routing_battery.py <responses.json>
      score_routing_battery.py <responses.json> <agent_ids.json>
      score_routing_battery.py <responses.json> --baseline <baseline.json>
      score_routing_battery.py <responses.json> <agent_ids.json> --baseline <baseline.json>

    Heuristic: the second positional arg is treated as agent_ids.json IFF it
    is not the literal `--baseline`. This preserves back-compat with the
    old `<responses> --baseline <baseline>` form.
    """
    if len(argv) < 2:
        raise SystemExit(
            "usage: score_routing_battery.py <responses.json> "
            "[<agent_ids.json>] [--baseline <baseline.json>]"
        )

    responses_path = argv[1]
    agent_ids_path: str | None = None
    baseline_path: str | None = None

    i = 2
    if i < len(argv) and argv[i] != "--baseline":
        agent_ids_path = argv[i]
        i += 1
    if i < len(argv) and argv[i] == "--baseline":
        if i + 1 >= len(argv):
            raise SystemExit("--baseline requires a path argument")
        baseline_path = argv[i + 1]

    return responses_path, agent_ids_path, baseline_path


def main() -> None:
    responses_path, agent_ids_path, baseline_path = _parse_args(sys.argv)

    with open(responses_path, encoding="utf-8") as f:
        responses = json.load(f)

    agent_ids = None
    if agent_ids_path:
        with open(agent_ids_path, encoding="utf-8") as f:
            agent_ids = json.load(f)

    baseline_rows = None
    if baseline_path:
        with open(baseline_path, encoding="utf-8") as f:
            baseline_responses = json.load(f)
        # Baseline scored against responses-only; no agent_ids assumed for
        # historical baselines (which predate the transcript-extraction path).
        baseline_rows = score_all(baseline_responses)

    rows = score_all(responses, agent_ids)
    print(render_report(rows, baseline_rows))


if __name__ == "__main__":
    main()
