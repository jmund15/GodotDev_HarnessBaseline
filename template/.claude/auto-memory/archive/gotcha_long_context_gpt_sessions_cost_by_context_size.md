---
name: gotcha-long-context-gpt-sessions-cost-by-context-size
description: "GPT -1m sessions cost ~2.9x the standard launcher for the same work: OpenAI bills every request over 272K input tokens at 2x input, 2x cached input and 1.5x output, and cache reads grow with context; launch standard and compact after the turn that needed the room"
metadata:
  node_type: memory
  type: project
  originSessionId: fda900c8-fd6a-47da-a5ad-4d19c68e3402
  modified: 2026-09-23T00:37:34.349Z
  retire_when: "review-by: 2027-03-22"
---

**Verdict:** launch GPT sessions at the standard window. `-1m` pays only for a named turn that needs more than ~258K of live context; `/compact` after it. Concurrency is not a fixed count: the codex band in `[budget-posture]` gates new sessions. Policy home: `reference/codex_session_runbook.md` §Launch.

**The rate tier (first-party, read 2026-09-22).** The tier is keyed to each request's actual prompt size, not the declared window:
- API. `developers.openai.com/api/docs/models/gpt-5.6-sol` (the same line is on the luna and terra pages): "Prompts with >272K input tokens are priced at 2x input and 1.5x output for the full request." The gpt-6-astra page says "2x input and cache rates". The gpt-5.5 page says "for the full session". The pricing page tooltip reads "Long context: >272K input tokens", and its table doubles cached input (gpt-5.6-sol $0.40 → $0.80).
- Codex credits. `help.openai.com/en/articles/20001415` (Enterprise token-based rate card): "Long context >272K input tokens — Input: 2x, Cached input: 2x, Output: 1.5x". It also says: "GPT-6 Astra usage in Codex does not incur additional long-context multipliers above 272K input tokens."
- Plus/Pro plan allowance. **Unknown for GPT-5.6/GPT-6.** `learn.chatgpt.com/docs/pricing` gives flat credit rates and says "Credit prices alone don't determine included subscription usage." The only plan-limit statement is GPT-5.4's launch post: "Requests that exceed the standard 272K context window count against usage limits at 2x the normal rate."
- Local `~/.codex/models_cache.json` has no price fields. Its `context_window` is 272000 (the standard window, equal to the tier threshold) and its `max_context_window` is 872000; gpt-5.5 is 272000/272000.

**Re-cost** (`.claude/scratch/session_lc_cost.py`, main loop only, Codex credits):

| session | requests >272K | flat | tiered | delta |
|---|---|---|---|---|
| 4abc5366 | 70% | 1,682 | 3,079 | +83% |
| 8201d879 | 55% | 2,413 | 4,089 | +69% |
| ee494ba6 | 72% | 2,573 | 4,770 | +85% |
| f59891ea | 62% | 2,137 | 3,730 | +75% |
| **total** | | **8,805** | **15,668** | **+78%** |

API standard dollars show the same deltas: $352 flat, $627 tiered. The luna subagents add 4,525 flat / 4,932 tiered credits (with a haiku arm excluded). Cache reads were **749M** main plus 50M subagent, not 1.6B.

**Compaction replay** (the same growth under the four `sol56` (gpt-5.6-sol) main loops, tiered credits; replay at 700K reproduces actual within 3%): 700K 16,003 · 400K 9,127 · **258K 5,609** · 200K 5,129 · 150K 4,799. At 258K the cost is 65% lower, and 42% lower even without the tier. Lower points buy 6–9% more at 1.5–2x the compactions, whose quality cost is unmeasured.

**Per model:** `sol56` (gpt-5.6-sol) emits ~300–420 output tokens per request, so it takes many small steps and each re-sends the context. Per edited file, its tool-call efficiency stayed inside the Anthropic sessions' range (10–16 vs 8–27 calls). Context size drove the plan exhaustion of 2026-09-20, not work efficiency: cache reads grow with context, and the tier adds to that if the plan applies it.

**The earlier analysis was wrong twice:**
- It priced a capacity question ("is the declared window billed?") instead of the rate question ("does the per-token rate tier by prompt size?"). Root cause: [[gotcha-rate-question-substituted-by-capacity-question]].
- `codex_session_credits.py` summed every transcript row, and Claude Code writes one row per content block, each repeating the message's usage. That doubled the totals, and the doubling roughly cancelled the missed tier. The script also carried terra at 25/2.5/125 against the card's 50/5/300. Deleted; use `session_lc_cost.py`.

Revisit before the review date if `external_models.json` changes codex `longContextTier`, OpenAI publishes a GPT-5.6/GPT-6 plan-usage long-context rule, or the ladder retires sol as a Codex session driver.

Related: [[gotcha-native-workflow-agent-compaction]].
