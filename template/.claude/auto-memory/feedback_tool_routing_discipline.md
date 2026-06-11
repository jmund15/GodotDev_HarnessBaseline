---
name: Tool routing discipline — specialized MCP > default Read/Grep
description: When a specialized MCP exists (ai-worker for bulk prose, LSP for C# symbols, semantic-search for NL code discovery), the harness defaults to Read/Grep/direct-MCP-read out of habit. Three concrete triggers + the meta-rule applied BEFORE any read/search tool call.
type: feedback
originSessionId: 448f3ac4-02e4-4d56-a395-39d847f5a6ae
---
Default tool selection on read/search calls is wrong by habit. Three recurring shapes — same root cause:

| Pre-call cue | Wrong default | Right tool | Why |
|---|---|---|---|
| Reading 3+ files OR a single file >400 lines for **synthesis** | `Read` / `obsidian_read_note` (loads into MY context) | `mcp__ai-worker__read_files(paths=[...], question=...)` | I'm paying Opus tokens to ingest prose I'm only going to summarize. The cheap model reads; I get the digest. |
| Finding a C# symbol (declaration / callers / implementers) | `Grep("BareSymbolName")` on `.cs` | `LSP workspaceSymbol` / `findReferences` | LSP resolves by type semantics. Grep returns the declaration + every doc-comment + every string literal mention with no disambiguation. |
| Finding a typed name on `.tscn`/`.tres`/`.gd`/`.md` (or unrestricted) — Resource subclass, scene class_name, doc-headed name | `Grep("BareSymbolName")` | `mcp__plugin_semantic-search_semantic-search__search` | Semantic-search indexes those file types per CLAUDE.md §9 with symbol-match ranking. Grep on a single PascalCase identifier in indexed files is a semantic-search-bypass smell, not a "Grep is correct here" zone. |
| Finding code I can describe but can't name | `Grep("approximate phrase")` | `mcp__plugin_semantic-search_semantic-search__search` | Embedding + BM25 + symbol-match ranks by meaning. Grep needs a literal anchor I don't have. |

**Why this rule exists (concrete failure 2026-05-03):** During /brainstorming Step 1 for Wind Blast, burned ~120 KB of context on `obsidian_read_note` (52 KB doc), 4× `obsidian_global_search` (~30-40 KB), and chained semantic-search results. A single `read_files` call with the doc + code paths and a focused question would have returned a 1-2 KB digest. **Enforcement gap**: the existing `Read >400 lines` PostToolUse nudge does NOT see `obsidian_read_note` / chained `obsidian_global_search` — those are MCP-tool reads, not `Read` reads.

**How to apply (litmus before any read/search tool call):**
1. Am I loading this content for **synthesis** (summarize, compare, decide)? → `read_files` with paths.
2. Am I loading this content for **direct citation, edit, or surgical line-precision read**? → `Read` is correct.
3. Am I about to `Grep` with a single PascalCase identifier (no regex metachars) on `.cs`? → stop, use `LSP workspaceSymbol`.
4. Am I about to `Grep` with a single PascalCase identifier on `.tscn`/`.tres`/`.gd`/`.md` (or unrestricted)? → stop, use `semantic-search` (those file types ARE indexed per CLAUDE.md §9; "Grep is correct outside `.cs`" is *over-stated* — it's only correct for literal values, UIDs, regex alternation, attribute markers).
5. Am I about to `Grep` with a fuzzy NL phrase? → stop, use `semantic-search`.
6. Am I about to chain ≥3 `obsidian_global_search` / `semantic-search` calls? → bundle into one `read_files` query.

**Audit/debug exception (NARROWED 2026-05-03 after H1 over-firing):** the synthesis-bundling rule does NOT apply only when the user explicitly framed the task as one of: **"audit X for Y", "security review", "fact-check line-by-line", "verify against the spec", "review the changed lines for safety", "confirm the patch fixes CVE-X"**. The agent needs line-precision frontier-model engagement with primary source — a cheap-model summary can silently miss the bug, leading to false sign-off.

**Critical narrowing:** the exception is **NOT triggered by**:
- *"Investigate why X is broken"* → debugging task; bundle into `read_files`. Today's H1 failure: agent read 5 files individually because the prompt opened with "Investigate", scoring the cumulative cascade. Word "investigate" alone does NOT trigger the audit exemption.
- *"Look at files A, B, C, D, E and report"* → multi-file synthesis with explicit file enumeration; bundle.
- *"How does X work / what does Y do"* → explanation, not audit; bundle.
- *"Find every X"* → single-tool query; per the routing table.

Sharpened litmus: *"Could a cheap-model summary **silently miss a defect** that a line-precision human read would catch?"* For routine bug-hunts, multi-file pattern surveys, and "what's going on here" investigations, the answer is no — bundle. For genuine pre-merge security review or patch-verification, yes — read directly.

The cumulative hook (`tool_routing_cumulative.py`) detects audit-intent cue words in the user's prompt (`audit`, `debug this`, `code review`, `security review`, `step through`, `fact-check`, `inspect the code`, etc.) and suppresses the cascade nudge automatically — but the cue-word match is intentionally narrower than the doctrine allows, to err on the side of nudging when ambiguous.

**Architectural limit — Phase 1 v2 finding (2026-05-03):** Claude Code's hook stdin does NOT expose any per-subagent identifier. `session_id`, `transcript_path`, and `cwd` all carry the **parent's** values when subagent tool calls fire hooks. Confirmed empirically by dumping `input_data` across 11 calls (3 main-session + 8 parallel subagents) — all 11 had identical `session_id` and `transcript_path`; only `tool_use_id` varied (per-call unique, useless for state isolation). **Implication:** per-subagent state isolation in PostToolUse hooks is impossible. The cumulative-counter design works correctly for main-session cascades but produces false-signal nudges in parallel-subagent contexts (one of N subagents receives a nudge attributed to the aggregate count). Mitigation: the cumulative hook detects parallel-burst via call-rate timing (≥4 calls within 3 seconds = burst) and suppresses nudges in that case. The structural answer for parallel-subagent routing compliance is **pre-injection at Agent dispatch** (modify the subagent's prompt before spawn) — a separate hook surface tracked as Phase 6 in plans/routing-compliance-rework-v2.md.

**Cross-references:** Global CLAUDE.md "Worker Model Delegation" section; project CLAUDE.md §9 Tool Routing — Pre-Call Litmus (the harness-wide enforcement entry); §7 (LSP); §9 (Semantic Search); `feedback_lsp_default_for_csharp.md`.

**Hooks (3 layers, advisory only — never block):**
- `.claude/hooks/tool_routing_nudge.py` — **PreToolUse** stderr nudge on `Grep` / `mcp__obsidian__obsidian_read_note` / `mcp__obsidian__obsidian_global_search`. Catches single-call shape mismatches at call time.
- `.claude/hooks/tool_routing_cumulative.py` — **PostToolUse** `additionalContext` nudge after a per-turn cumulative threshold is crossed. Matchers include `Read` and `Glob` (which the PreToolUse hook does NOT see), closing the death-by-thousand-cuts gap. Diversity-gated: focused investigations (≤2 distinct dirs) or single-tool streaks (≥3 same-tool calls in a row) at count ≥ 4 trigger the soft nudge; count ≥ 7 always triggers the hard nudge regardless of diversity. Per-turn dedupe via `nudges_fired_this_turn`.
- `.claude/hooks/tool_routing_post_grep.py` — **PostToolUse** `additionalContext` nudge after a `Grep` call where pattern is bare PascalCase + indexed file family + ≥1 hit. Phrased as "for next time" — does NOT pressure a re-do (per first-call-recovery rule). K1-style legitimate overrides suppressed by cue-word check (`literal`, `verbatim`, `comment`, `audit`, etc.) against the user's prompt text.
- Companion: `.claude/hooks/tool_routing_cumulative_reset.py` — **UserPromptSubmit** `touch`es per-session state file at turn start so the cumulative counter knows the boundary. Stashes user prompt text for K1 cue-word check. Runs a 24h stale-sweep on state files.

State files at `~/.claude/.routing_state/<sid_short>.json`. Atomic writes via tempfile+rename. State-write failures emit one stderr line then continue (visible failure beats silent degradation).

---

## Worked examples (anchor cases from the 2026-05-03 routing-compliance battery)

**Example 1 — H1-style cascade (cumulative anti-pattern).**
- *Prompt*: "Investigate why icicles aren't shattering on hit when a fire spell hits a frozen target. Look at the icicle scene, the spell behavior, the freeze status definition, the shatter reaction resource, and any related test fixtures."
- *Wrong*: 21 sequential tool calls — 4 Globs, 14 Reads, 3 Greps. Each Read individually under 400 lines so the per-call hook never fires; cumulatively burns ~80 KB of context loading code chunks the model is only going to summarize.
- *Right*: ONE `mcp__ai-worker__read_files(paths=[icicle_scene_path, IcicleBehavior.cs, freeze_effect.tres, shatter_reaction.tres, ShatterTests.cs], question="across these files, identify the most likely cause of fire-hits-frozen-target failing to trigger shatter")`. Returns a 1-2 KB digest with hypothesis ranking.
- *Why it failed in the test*: the death-by-thousand-cuts shape — no individual call tripped any threshold the per-call PreToolUse hook could see. The new `tool_routing_cumulative.py` PostToolUse hook fires a soft nudge at the 4th read in the same investigation, hard nudge at the 7th.

**Example 2 — D1-style PascalCase-on-indexed (semantic-search bypass).**
- *Prompt*: "Where in the codebase do we apply force impulses chain-reactively, where one entity hits another and the second entity also takes impact damage credited to the original spell caster?"
- *Wrong*: guessed at symbol names and Greped for them — `Grep("ForceChain")`, `Grep("ImpactSource")`, then a cascade of 9 follow-up Read+Grep calls to triangulate. Wastes calls on misses (the names didn't exist).
- *Right*: `mcp__plugin_semantic-search_semantic-search__search(query="chain force impulse collision source attribution")` first — embedding + BM25 ranking finds `ImpactCollisionStrategy.cs` and `PhysicsInteractionComponent.cs` in one shot. THEN, with names known, optional `LSP findReferences` for precise call-site enumeration.
- *Why it failed in the test*: the agent didn't know the symbol names, and `Grep` of a guessed PascalCase name returns either nothing (waste) or a non-canonical hit (misleading). Semantic-search is built for "I can describe but can't name it" — using it first costs one call and finds the seam.

**Example 3 — K1-style legitimate override (the carve-out preserved).**
- *Prompt*: "Show me every literal string occurrence of 'FireballBehavior' — including in comments, doc-comments, and string literals — across .cs files. I'm doing a documentation audit."
- *Right tool here is `Grep`*, despite the bare-PascalCase-on-`.cs` shape. LSP filters comment mentions out; the user explicitly asked for them. The override-justification template: state in your response why the bypass is correct (e.g., "Using Grep here because the audit needs comment-mention coverage that LSP filters out").
- *Why this matters for the hook design*: the `tool_routing_post_grep.py` hook checks the user prompt for literal-intent cue words (`literal`, `verbatim`, `comment`, `string literal`, `audit`, `documentation audit`, `every occurrence`) and suppresses the retroactive nudge when present. Adding a cue word list isn't a doctrine change — it's mechanically translating the existing override carve-out into hook behavior.

**Example 4 — C2-style silent-fallback LSP UX trap (added 2026-05-03 from focused-diagnostic re-run).**
- *Prompt*: "Find every place that calls SpellCrafter.ApplySynergies."
- *Wrong*: agent loaded LSP schema, called `LSP(operation="workspaceSymbol", filePath=".", line=1, character=1)` with `.` as a placeholder, got `Path is not a file: .`, then silently fell through to Glob+Grep+Read. The rubric scored PASS because the *intent* was LSP, but the functional outcome equalled never reaching for LSP at all.
- *Right*: `LSP(operation="documentSymbol", filePath="SpellArchitecture/SpellCrafter.cs", line=1, character=1)` to get the line:column of `ApplySynergies` (line 403), THEN `LSP(operation="findReferences", filePath="SpellArchitecture/SpellCrafter.cs", line=403, character=29)` for callers.
- **Why this is a documented trap**: (a) `filePath` is required on every LSP operation, even workspace-wide ones — it's a language-server routing hint (extension picks the server), not a query target; passing `.` or `""` always fails. (b) MCP LSP `workspaceSymbol` does NOT take a query parameter — it returns 100 unfiltered alphabetical-by-path symbols, useless for targeted lookup. The right "find symbol by name" workflow is **anchor-then-navigate**: Grep on `class FooBar`/`interface FooBar` (legitimate Grep, not the bypass smell) OR `semantic-search("FooBar")` to find the file → `documentSymbol` for the line → `findReferences`/`hover`/`incomingCalls` from the anchored position.
- **Doctrine update propagated to**: `csharp_lsp.md` worked-example section, `lsp-early-load` SessionStart reminder, CLAUDE.md §9 "PascalCase on .cs" row, `tool_routing_nudge.py` PreToolUse text, `tool_routing_post_grep.py` PostToolUse text — all five reference points now say anchor-then-navigate, not `workspaceSymbol(query)`.

**Example 5 — J1-style first-call-recovery misinterpretation (added 2026-05-03 from focused-diagnostic re-run).**
- *Prompt*: "Plan a refactor to consolidate WireSpread duplication across the four *Effect.Apply() methods..."
- *Wrong*: agent did Glob × 4 → memory_search × 2 → Read × 4 → Grep → Glob (14 calls), prefixing the response with *"Acknowledged — first-call recovery applies, proceeding with what I have."* The cumulative SOFT nudge fired after Glob × 4 and the agent **read it** but interpreted "first-call recovery" as a license to keep using the wrong-shape tools for the rest of the turn.
- *Right*: after the SOFT nudge fires, switch to the suggested tool for the NEXT call — bundle remaining file reads into `read_files`, or use `LSP findReferences` from a Grep anchor for the duplicate-detection step. The recovery rule is *per-query*, not *per-turn*.
- **Doctrine sharpened to**: "First-call recovery is per-query, not per-turn. Don't redo *this specific query* in the suggested tool, but ALL FURTHER lookups of the same shape in this turn must route to the suggested tool. The recovery rule is not a license to continue using the wrong tool." (Both `tool_routing_post_grep.py` and `tool_routing_cumulative.py` HARD-tier text now state this explicitly.)

**First-call recovery rule (sharpened 2026-05-03):** if you've already made the wrong tool call (e.g., Grep on PascalCase that should have been LSP), do NOT chase it with a second call to the "right" tool to redo *that specific query* — accept the result and proceed. The cost is sunk; redoing the same query in the right tool is wasteful. **HOWEVER**: this rule is **per-query, not per-turn**. All FURTHER calls in the same turn that match the same wrong-shape pattern MUST route to the right tool. The retroactive PostToolUse nudge exists to inform the *next* call, not pressure a re-do of the *previous* one. Treating "first-call recovery applies" as blanket forgiveness for the entire turn (J1-style) is a doctrine misinterpretation.

---

**Example 6 — `documentSymbol` position-window gotcha (added 2026-05-03 from C2 empirical re-verification with LSP probes):**

- *Setup*: `LSP(documentSymbol, SpellCrafter.cs, 1, 1)` reports `ApplySynergies` at Line 403. Actual identifier is at Line 413 column 39–52. The MCP wrapper surfaces `range.start` (start of full leading-trivia span — every blank line + every `///` doc-comment + every `[Attribute]` line between the previous `}` and the next non-whitespace token) instead of `selectionRange.start` (the identifier itself).
- *Empirical position-window probe* (findReferences on `SpellCrafter.ApplySynergies`):
  - `(403, 36)` → "No references" (blank line above doc-comment block)
  - `(408, 1)` → "No references" (start of `///` doc-comment)
  - `(413, 1)` → "No references" (indent whitespace before `internal`)
  - `(413, 14)` → "No references" (return-type `HashSet<T>` — csharp-ls doesn't track BCL types)
  - `(413, 40)` → 9 references ✅ (canonical identifier position)
  - `(413, 53)` → **216 references across 93 files** ⚠️ (one column off — bound to a different symbol; results look authoritative but are completely wrong)
  - `(413, 54)` → "No references"
  - `(414, 5)` → 216 references ⚠️ (same wrong-symbol bind from inside method body)
- *Worst-case failure mode*: an off-by-one column doesn't return empty — it returns a **wrong-symbol caller list** that's structurally identical to a correct result but refers to an entirely different symbol. There is no diagnostic that distinguishes "right symbol, no callers" from "wrong symbol, 200 callers". A refactor planned against the wrong list will break unrelated code.
- *Diagnostic for the empty case only*: `LSP hover(line, character)` returns "the cursor is not on a symbol" — confirms whitespace position. Does NOT catch the wrong-symbol case.
- *Only fully-safe workflow*: anchor the precise identifier line+column with `Grep("(public|private|protected|internal|static).*MethodName\\(" -g "<file>")`, parse the column from the Grep match, then `findReferences(line=<grepped>, character=<grepped>)`. Documented in `csharp_lsp.md` "documentSymbol line-coordinate gotcha" section.
- *Doctrine update propagated*: `csharp_lsp.md` (worked example + position-window warning), this feedback doc (Example 6).
