---
description: Retroactive auto-memory hygiene sweep — verify mechanism claims (cause vs symptom) + overfit-to-specific + gotcha-vs-feature-doc drift + hot/cold tier fit, sharing one enumeration.
disable-model-invocation: true
---

# /memory_audit — Memory Retroactive-Hygiene Pass

Retroactive sibling to `/autolearn`'s save-time gates. `/autolearn` stops *new* low-quality entries at save time; this pass sweeps the *existing* backlog under `.claude/auto-memory/` (hot `*.md` + cold `archive/`) across **five lenses that share Step 1's one-time enumeration**:

1. **Mechanism-claim verification** — isolate cause vs symptom (the original core; *the core discipline* below). Retroactive sibling of `/autolearn`'s question-5 gate.
2. **Overfit-to-Specific** — does the *rule itself* name a specific file/PR/spell/SHA/session-date? (gate definition: `/autolearn`'s *Anti-pattern: Overfit-to-Specific* is the single source of truth — this pass applies it retroactively, it does not redefine it).
3. **Documentation-drift** — is this a gotcha/rule, or feature-doc that belongs in a code `<summary>`? (litmus: CLAUDE.md Core Principle 8 save filter).
4. **Tier fit** — is it in the right tier? (litmus: CLAUDE.md §2 *Placement*, the SoT — this pass applies it retroactively).
5. **Fragmentation** — should these N entries be ONE entry? Lenses 1–4 judge an entry in isolation and pass it; this one is the only **cross-entry** lens, and the only one that can explain an index that is over budget while every entry in it is individually correct.

Lenses 4 and 5 are the two levers on `MEMORY.md` size (it auto-loads at SessionStart under a 25KB cap). They fail differently, so check both: tier fit moves a rule *out*; fragmentation merges rules *together*. **A clean lens-1–4 report on an over-budget index is the lens-5 signal, not an all-clear** — quality was never the problem.

The generic `anthropic-skills:consolidate-memory` skill is the *dedup / durable-vs-dated* sibling — it does **not** apply the overfit or tier gates (lenses 2 and 4 here own those), and it does **not** merge fragmented clusters (lens 5): a cluster is N *distinct, non-duplicate* rules on one topic, so dedup finds nothing there. Run standalone, or as a phase inside a memory-consolidation session.

**Why it exists.** Two memories were nearly saved as fact that were empirically false ("ISceneRunner isolates the scene in its own World3D"; "git pathspecs are case-sensitive"), each falsified in <30s once actually tested. Both were the same failure shape: a symptom was observed, a cause was *assumed*, and the assumed cause shipped as fact. This pass finds already-saved instances of that shape.

## The core discipline (do not skip)

A cited incident proves a **symptom was witnessed** — it does NOT prove the **stated cause** is correct. "Build broke on 2026-05-17, fixed by changing Y, therefore Y-mechanism" has only the *symptom* pinned; the causal step is an un-isolated inference. That inference is the risk class.

For every mechanism claim, the bar is: **does the cited evidence ISOLATE the mechanism, or only the symptom?**

- **Mechanism-isolated** (→ confirmed): a code anchor showing the exact mechanism in source; an isolating test that varied one variable; a controlled reproduction; a doc/spec that states the behavior verbatim.
- **Symptom-only / inferred-cause** (→ the risk class): an incident/date/"fixed by Y" where the stated cause was assumed, not isolated. Re-check if cheap; flag with the exact isolating test if expensive. **Never auto-confirm from the citation alone.**

Practice what you audit: name the single command/test that would FALSIFY each claim, then run it. A fix that "worked" or a single observation is NOT verification — those are the two traps that produced the false claims.

## Procedure

### Step 0 — Measure the index (lens 5; cheap, runs first)

Lens 5 reads `MEMORY.md` alone — not the entries — so it costs one command and gates whether the
expensive enumeration is even the right work. **Measure; do not eyeball.** Report bytes (the binding
cap is 25KB, not the 200-line budget — a dense index hits bytes at ~90 lines), total links, and
links-per-line.

**An index line carrying ≥4 links is a fragmentation candidate, and the line's own prefix already
names the topic** (`Concurrent sessions:`, `Lifecycle:`, `Exports:`). That prefix exists because a
past session recognised the cluster and grouped it inline instead of merging it. Rank candidates by
bytes, not link count.

**Merge litmus — is the CLUSTER NAME a reliable trigger on its own?**
- **Yes → merge.** You know when you are in a concurrent session, dispatching a delegate, or doing
  node-lifecycle work. One pointer suffices; you will open the file.
- **No → keep inline.** For ambient cross-cutting rules the inline hook text *is* the trigger, and
  collapsing it silently demotes the rule to search-only. This is the real cost of a merge and the
  reason it is not applied blanket.

If the index is inside budget with no ≥4-link clusters, lens 5 reports "no fragmentation" and the
pass proceeds on lenses 1–4 as normal.

### Step 1 — Classify every entry (parallelizable)
Enumerate all files (`ls .claude/auto-memory/*.md` + `archive/*.md`). Fan out classification over batches (one subagent per ~30 files; hand each an **explicit path list** — agents must not Glob/Grep to discover, per `gotcha_workflow_fanout_search_false_absence.md`). In the one read of each file, tag it for all three lenses:

- **Lens 1 — mechanism claim?** A **falsifiable mechanism claim** (testable statement about how a tool/engine/API/system *behaves*: "X causes Y", "Z is case-sensitive", "A bypasses B") vs **out-of-scope** (preference / process rule / event-fact / design recommendation / descriptive note). A design-rule file can carry one embedded mechanism claim — extract it. Archive files are multi-fact: extract each distinct claim. Per mechanism claim, report: the one-line claim, any evidence line quoted verbatim (`Verified:`/`Concrete:`/`Witnessed:`/`Evidence:`/`Source:`/code-anchor), and a proposed falsification check (or `EXPENSIVE: <why>`).
- **Lens 2 — overfit candidate?** Does the **rule itself** (the headline/principle, not the `Why:`/`Concrete:`/evidence line) name a specific file path, PR number, spell name, commit SHA, or session date? Flag as overfit-candidate with the offending name. **Carve-out:** `feedback_*` files are deliberately concrete corrections-to-future-self — do NOT flag them unless egregious; their job is to remind future-Claude of a specific user correction, not to be a transferable principle.
- **Lens 3 — drift candidate?** Is the entry feature-documentation (API signature, formula/budget description, feature narrative with no surprise element) rather than a gotcha/rule? Flag as drift-candidate. Litmus: *"Would forgetting this cause a bug or wasted time?"* No → it belongs in a code `<summary>`, not memory.
- **Lens 4 — tier fit?** Report `domain-trigger: <the CLAUDE.md search-term-table domain whose search would surface this> | none`. `none` = no domain to search from (process / planning / tool-routing / cross-cutting design / preference), so it must fire unprompted. A **hot** file with a domain trigger is a demote-candidate; a **cold** file with `none` is a promote-candidate.

### Step 2 — Triage each mechanism claim (serial, Claude-side)
Verification is bespoke and stays Claude-side — **never delegate the adjudication** (delegated guessing is the failure mode this whole pass exists to catch). For each claim, assess evidence quality per *the core discipline*:
- **Mechanism-isolated already** → confirmed; stamp `**Verified:**` pointing at the existing anchor (re-confirm the anchor with a quick grep/read if cheap).
- **Symptom-only / no-evidence + cheaply checkable** (a grep, a config read, a one-variable test, a code read) → run the check now. Then **confirm** (stamp), **correct** (rewrite to the true mechanism + stamp), or **quarantine** (mark the claim false).
- **Expensive / not cheaply verifiable** (needs the Godot engine at runtime, a second consuming project, parallel-subagent repro, session-timing) → flag with the EXACT check a human/agent must run. Do not guess.

### Step 2b — Overfit, drift + tier adjudication (serial, Claude-side)
Adjudication stays Claude-side (same reason as Step 2). These three lenses propose *edits*, not stamps — surface them for one-pass user review at report time; do not auto-apply destructive merges/deletes.

- **Overfit candidates (lens 2)** → one of three outcomes (per `/autolearn`'s *Anti-pattern: Overfit-to-Specific*, the SoT): **Rewrite** — promote the principle to the headline, demote the specific name to a `Concrete:` evidence line (most land here); **Merge** — if the principle already exists in another entry, fold this one's evidence in and delete; **Delete** — only if the rule was always one-shot situational with no transferable principle (rare). Honor the `feedback_*` carve-out from Step 1.
- **Drift candidates (lens 3)** → **Keep** (it carries a real surprise/bite), or **Relocate** — move feature-doc content to a code `<summary>` / cold `archive/` runbook, leaving at most a one-line `MEMORY.md` pointer if hot-tier surfacing is still warranted.
- **Fragmentation candidates (lens 5)** → **Merge** or **Keep split**. A merge writes ONE topic file whose numbered rules preserve every source rule's *mechanism* and every `Verified:`/`Concrete:` line — a merge that keeps conclusions and drops mechanisms destroys the entries it claims to preserve. Merging is delegable per cluster (bounded, enumerable file set); deleting the sources, patching `MEMORY.md`, and the inbound-reference sweep stay with the orchestrator. **Mandatory after any merge:** `Grep` the deleted names across `.claude/` and rewrite every `[[wikilink]]` and bare citation to the merged file — memory files are cited bare by name from skills/commands/hooks, so a merge without this sweep breaks citations exactly as a rename would (`feedback_memory_file_refs_no_markdown_links.md`, `instruction_quality` §4 inbound rot).
- **Tier candidates (lens 4)** → **Demote** (`git mv` hot → `archive/`, drop the `MEMORY.md` pointer) or **Promote** (reverse). **Never rename during a tier move** — memory files are cited bare by name from skills/commands/hooks (`feedback_memory_file_refs_no_markdown_links.md`), and a rename breaks every citation while the move alone breaks none. Batch demotions when the index is at/near the cap; otherwise report-only. Note the tier moves for `/sync_baseline` — auto-memory paths are baseline-tracked in both tiers, so a move registers as drift.

### Step 3 — Stamp + report
Stamp `**Verified:** <YYYY-MM-DD> memory-claim audit — <the check that isolated it>` on confirmed/corrected mechanism claims (greppable, so the next pass skips them). Keep hot-file stamps to one line (brevity budget). When a stamp records a partial result (e.g. field-absence confirmed but behavioral half doc-cited), say so explicitly — `**Verified (partial):**` — rather than overstating. (Overfit/drift/tier edits are not stamped — they change the entry itself.)

After any tier move, verify the index invariant — every `MEMORY.md` pointer resolves to a hot file AND every hot file has exactly one pointer — and report the new index byte size against the cap.

Output a report grouped by lens: **(1) mechanism** — per claim `{confirmed | corrected | quarantined | flagged}` with the check run or required; **(2) overfit** — per candidate `{rewrite | merge | delete}` proposal; **(3) drift** — per candidate `{keep | relocate}` proposal; **(4) tier** — per candidate `{demote | promote}` proposal; **(5) fragmentation** — measured index bytes/links, then per cluster `{merge | keep split}` with the projected byte delta. Summarize counts; list the FLAGGED mechanism claims and all proposed overfit/drift/tier edits (the actionable residue) for one-pass approval.

## Scope discipline
Lenses 1–4 are **surgical, not sweeping**; lens 5 is the exception — it judges the index's *shape*, so it looks at everything at once and costs almost nothing (Step 0 is one command). Most entries are preferences/process/event-facts (not a mechanism claim), already-correct concrete `feedback_*` corrections (not overfit), genuine gotchas (not drift), or already in the right tier — and many gotchas are already mechanism-isolated by a code anchor. The value is finding **the few** un-isolated claims, overfit headlines, and feature-doc strays — not re-validating everything, not rewriting sound entries, and not manufacturing findings. When a whole tier (e.g. cold `archive/`) is low-traffic, prefer report-only + targeted cheap checks over mass edits.
