---
description: Sweep auto-memory for hygiene drift across seven lenses
disable-model-invocation: true
---

# /memory_audit — Memory Retroactive-Hygiene Pass

Retroactive sibling to `/autolearn`'s save-time gates. `/autolearn` stops *new* low-quality entries at save time; this pass sweeps the *existing* backlog under `.claude/auto-memory/` (hot `*.md` + cold `archive/`) across seven lenses: **per-entry lenses 1–4, which share Step 1's one-time enumeration**, **lens 7, one tool pass over every entry in Step 1a**, and **index-level lenses 5–6, which run in Step 0 on `MEMORY.md` alone**.

Per-entry (Step 1):

1. **Mechanism-claim verification** — isolate cause vs symptom (the original core; *the core discipline* below). Retroactive sibling of `/autolearn`'s question-5 gate.
2. **Overfit-to-Specific** — does the *rule itself* name a specific file/PR/ability/SHA/session-date? (check definition: `instruction_quality` §3b *Overfit-to-Specific*).
3. **Documentation-drift** — is this a gotcha/rule, or feature-doc that belongs in a code `<summary>`? (litmus: the save filter in `CLAUDE.core.md` §2 Memory).
4. **Decision-time placement** — at what moment is this rule needed, and does its destination match? (litmus: CLAUDE.md §2 *Memory (One Store, Two Tiers)* and `instruction_quality` §5, the SoTs).
7. **Citation currency** — does every path, command, flag and symbol the entry cites still exist, and does its `How to apply:` point at the file that owns the procedure? (save-time rule: `commands/reference/codify_memory.md` §Memory landing).

Index-level (Step 0), on `MEMORY.md` alone:

5. **Fragmentation** — should these N entries be ONE entry? Lenses 1–4 judge an entry in isolation and pass it; this one is the only **cross-entry** lens, and the only one that can explain an index that is over budget while every entry in it is individually correct.
6. **Injection duplication** — is this hook already saying it at SessionStart? (`instruction_quality` §5 A4).

Placement, fragmentation and injection duplication act on `MEMORY.md` size, which auto-loads at SessionStart and is budgeted per `instruction_quality` §5 A1; a clean per-entry report on an over-budget index is the fragmentation signal, not an all-clear.

No command owns the *dedup / durable-vs-dated* pass — do it by hand inside a memory-consolidation session, applying the SSOT test in `.claude/commands/reference/codify_memory.md` §Memory landing. It is not lens 5: a cluster is N *distinct, non-duplicate* rules on one topic, so dedup finds nothing there.

**Why it exists.** A saved causal claim can mislead future work if its evidence proves only the symptom.

## The core discipline (do not skip)

A cited incident proves a **symptom was witnessed** — it does NOT prove the **stated cause** is correct. "Changing Y fixed the failure, therefore Y caused it." has only the *symptom* pinned; the causal step is an un-isolated inference. That inference is the risk class.

For every mechanism claim, the bar is: **does the cited evidence ISOLATE the mechanism, or only the symptom?**

- **Mechanism-isolated** (→ confirmed): a code anchor showing the exact mechanism in source; an isolating test that varied one variable; a controlled reproduction; a doc/spec that states the behavior verbatim.
- **Symptom-only / inferred-cause** (→ the risk class): an incident/date/"fixed by Y" where the stated cause was assumed, not isolated. Re-check if cheap; flag with the exact isolating test if expensive. **Never auto-confirm from the citation alone.**

Practice what you audit: name the single command/test that would FALSIFY each claim, then run it. A fix that "worked" or a single observation is NOT verification.

## Procedure

### Step 0 — Measure the index (lenses 5 and 6; cheap, runs first)

Both read `MEMORY.md` alone — not the entries — so they cost two commands and gate whether the
expensive enumeration is even the right work. **Measure; do not eyeball.** Report bytes, total
links, and links-per-line, per `instruction_quality` §5 A1.

**Per-cluster census + delta (`instruction_quality` §5 A5).** Absolute bytes cannot distinguish an
index stable for a month from one that just absorbed 4 KB, so run the census at both the current
tree and the previous `/memory_audit` anchor and report the per-cluster difference:

```
awk '/^#{2,4} /{if(h)printf "%6d B  %s\n",b,h; h=$0; b=0} {b+=length($0)+1} END{printf "%6d B  %s\n",b,h}' .claude/auto-memory/MEMORY.md | sort -rn
```

Anchor = the most recent commit touching `.claude/auto-memory/MEMORY.md` that came from a
`/memory_audit` pass (`git log --oneline -- .claude/auto-memory/MEMORY.md`) — re-derive it each run.
Run the same `awk` over `git show <anchor>:.claude/auto-memory/MEMORY.md` for the baseline column.

**An index line whose prefix names one topic and which carries multiple links is a fragmentation
candidate** (`Concurrent sessions:`, `Lifecycle:`, `Exports:`). The merge litmus below decides.
Rank candidates by bytes, not link count.

**Merge litmus — is the CLUSTER NAME a reliable trigger on its own?**
- **Yes → merge.** You know when you are in a concurrent session, dispatching a delegate, or doing
  node-lifecycle work. One pointer suffices; you will open the file.
- **No → keep inline.** For ambient cross-cutting rules the inline hook text *is* the trigger, and
  collapsing it silently demotes the rule to search-only. This is the real cost of a merge and the
  reason it is not applied blanket.

If the index is inside budget and no line is a fragmentation candidate, lens 5 reports "no
fragmentation" and the pass proceeds on the per-entry lenses as normal.

**Lens 6 — injection duplication (`instruction_quality` §5 A4).** For each index hook, `Grep`
`hooks/*.py` for unconditional stdout carrying the same rule. Report `DUP | PARTIAL | UNIQUE`, and
**name the emitting hook** on every `DUP` and `PARTIAL`. Two rules bound the cut:

- A `PARTIAL` is a cut candidate only when the injection carries the same **imperative**, not merely
  the same data. Same subject with no instruction leaves the index line load-bearing.
- Before cutting, confirm the injection is **not model-gated**. A hook that fires for only some
  session models leaves the exempt tier relying on `MEMORY.md` alone, so a cut there strips the rule
  from exactly that tier. Read the hook's own gating condition.

### Step 1 — Classify every entry (parallelizable)
Enumerate all files (`ls .claude/auto-memory/*.md` + `archive/*.md`). Fan out classification over batches sized so each batch's files and per-file output fit the reader's verified context (hand each an **explicit path list**, per `gotcha_workflow_fanout_search_false_absence.md`). In the one read of each file, tag it for all four lenses:

- **Lens 1 — mechanism claim?** A **falsifiable mechanism claim** (testable statement about how a tool/engine/API/system *behaves*: "X causes Y", "Z is case-sensitive", "A bypasses B") vs **out-of-scope** (preference / process rule / event-fact / design recommendation / descriptive note). A design-rule file can carry one embedded mechanism claim — extract it. Archive files are multi-fact: extract each distinct claim. Per mechanism claim, report: the one-line claim, any evidence line quoted verbatim (`Verified:`/`Concrete:`/`Witnessed:`/`Evidence:`/`Source:`/code-anchor), and a proposed falsification check (or `EXPENSIVE: <why>`).
- **Lens 2 — overfit candidate?** Does the **rule itself** (the headline/principle, not the `Why:`/`Concrete:`/evidence line) name a specific file path, PR number, ability name, commit SHA, or session date? Flag as overfit-candidate with the offending name. **Carve-out:** `feedback_*` files are deliberately concrete corrections-to-future-self — do NOT flag them unless egregious; their job is to remind future-Claude of a specific user correction, not to be a transferable principle.
- **Lens 3 — drift candidate?** Is the entry feature-documentation (API signature, formula/budget description, feature narrative with no surprise element) rather than a gotcha/rule? Flag as drift-candidate. Litmus: *"Would forgetting this cause a bug or wasted time?"* No → it belongs in a code `<summary>`, not memory.
- **Lens 4 — decision-time placement?** Report `decision-time: pre-trigger | file-class <glob> | domain-entry`. Standard: `instruction_quality` §5; destination table: `/codify` §Step 4 (`.claude/commands/codify.md`) — read it there.
    - **pre-trigger** — the decision is made before any file is open (process, planning, tool-routing, cross-cutting design, preference), so it must fire unprompted.
    - **file-class `<glob>`** — the decision is made with a file of that class already open. Qualifies only when the glob is prefix-anchored below the repo root (`Tests/**/*.cs`, `DomainCore/**`, `HSM/**`). An extension-only glob (`**/*.<source-ext>`, `**/*.md`) matches nearly every session and defers the cost rather than removing it — report those as `pre-trigger`. Decide width by **reading the glob string**, never by counting files and never with `git ls-files` (it reports the git index, not the loader's filesystem glob, and returns 0 for `Jmodot/**`).
    - **domain-entry** — surfaced by a deliberate domain search.
  
  **The burden of proof is on hot** (`CLAUDE.core.md` §2 Memory). Apply it per file: a hot file that fails the test is a demote-candidate, and a `domain-entry` or `file-class` verdict is an automatic demote/split candidate. A **cold** file promotes only when it passes that same two-part test — rare by design; when in doubt it stays cold, because search is the recall path.

### Step 1a — Citation currency (lens 7; deterministic)
The full entry list overflows a command line, so pass it as a list file: `printf '%s\n' .claude/auto-memory/*.md .claude/auto-memory/archive/*.md > .claude/scratch/memory_files.txt`, then `python3 .claude/tools/verify_doc_citations.py --kinds all --json --only missing,unparseable @.claude/scratch/memory_files.txt > .claude/scratch/memory_citations.json`. Read each entry's rows from that file, and keep missing and unparseable citations as separate lists: an unparseable citation is neither found nor missing. A missing row is a candidate. Before choosing its outcome, confirm the citation names a repo artifact: a vault note, a file on another host or a git ref is outside the tool's reach, not stale. Then read each entry's `How to apply:` line and mark it when it restates a procedure a harness file owns, or cites an owner that is missing or no longer states the decision; find the owning file by `Grep` over `.claude/` for the decision's own terms.

### Step 2 — Triage each mechanism claim (serial, Claude-side)
Verification is bespoke and stays Claude-side — **never delegate the adjudication** (delegated guessing is the failure mode this whole pass exists to catch). For each claim, assess evidence quality per *the core discipline*:
- **Mechanism-isolated already** → confirmed; stamp `**Verified:**` pointing at the existing anchor (re-confirm the anchor with a quick grep/read if cheap).
- **Symptom-only / no-evidence + cheaply checkable** (a grep, a config read, a one-variable test, a code read) → run the check now. Then **confirm** (stamp), **correct** (rewrite to the true mechanism + stamp), or **quarantine** (mark the claim false).
- **Expensive / not cheaply verifiable** (needs the engine or runtime running, a second consuming project, parallel-subagent repro, session-timing) → flag with the EXACT check a human/agent must run. Do not guess.

### Step 2b — Overfit, drift, placement + injection adjudication (serial, Claude-side)
Adjudication stays Claude-side (same reason as Step 2). Lenses 2–7 propose *edits*, not stamps — surface them for one-pass user review at report time; do not auto-apply destructive merges/deletes.

- **Overfit candidates (lens 2)** → one of three outcomes (per `instruction_quality` §3b *Overfit-to-Specific*): **Rewrite** — promote the principle to the headline, demote the specific name to a `Concrete:` evidence line (most land here); **Merge** — if the principle already exists in another entry, fold this one's evidence in and delete; **Delete** — only if the rule was always one-shot situational with no transferable principle (rare). Honor the `feedback_*` carve-out from Step 1.
- **Drift candidates (lens 3)** → **Keep** (it carries a real surprise/bite), or **Relocate** — move feature-doc content to a code `<summary>` / cold `archive/` runbook, leaving at most a one-line `MEMORY.md` pointer if hot-tier surfacing is still warranted.
- **Fragmentation candidates (lens 5)** → **Merge** or **Keep split**. A merge writes ONE topic file whose numbered rules preserve every source rule's *mechanism* and every `Verified:`/`Concrete:` line — a merge that keeps conclusions and drops mechanisms destroys the entries it claims to preserve. Merging is delegable per cluster (bounded, enumerable file set); deleting the sources, patching `MEMORY.md`, and the inbound-reference sweep stay with the orchestrator. **Mandatory after any merge:** `Grep` the deleted names across `.claude/` and rewrite every `[[wikilink]]` and bare citation to the merged file — memory files are cited bare by name from skills/commands/hooks, so a merge without this sweep breaks citations exactly as a rename would (`feedback_memory_file_refs_no_markdown_links.md`, `instruction_quality` §4 inbound rot).
- **Placement candidates (lens 4)** → **Demote** (`git mv` hot → `archive/`, drop the `MEMORY.md` pointer), **Promote** (reverse), or **Split** for a `file-class` verdict: the one-line rule goes to `rules/<name>.md` with a `paths:` glob, the evidence file stays under `auto-memory/`, the pointer drops. **Demotion executes regardless of index headroom** — an unjustified hot pointer is wrong at any index size, and demotion is cheap: cold stays searchable, so the move carries no claim re-verification. **Never delete or rename during a tier move** — the move alone breaks nothing. Note tier moves for `/sync_baseline` — auto-memory paths are baseline-tracked in both tiers, so a move registers as drift.
- **Citation findings (lens 7)** → **Repoint** (rewrite `How to apply:` as a pointer to the owning file and section), **Fix owner** (cite the decision's current home), **Fix citation** (a renamed path, command, flag or symbol), or **Retire** (the decision no longer exists anywhere: delete the entry and its pointer after the inbound-reference sweep lens 5 prescribes).
- **Injection duplicates (lens 6)** → **Cut** the index line (naming the hook that keeps emitting the rule) or **Keep** it. `DUP` with an unconditional, non-model-gated injection cuts; `PARTIAL` cuts only when the imperative matches; `UNIQUE` and every model-gated injection keep.

### Step 3 — Stamp + report
Stamp `**Verified:** <YYYY-MM-DD> memory-claim audit — <the check that isolated it>` on confirmed/corrected mechanism claims (greppable, so the next pass skips them). Keep hot-file stamps to one line — the next audit greps the `**Verified:**` line, and a line-oriented grep loses a multi-line stamp's tail. When a stamp records a partial result (e.g. field-absence confirmed but behavioral half doc-cited), say so explicitly — `**Verified (partial):**` — rather than overstating. (Overfit/drift/tier edits are not stamped — they change the entry itself.)

After any tier move, verify the index invariant: every `MEMORY.md` pointer resolves to a hot file, and no pointer repeats another. A top-level file with no pointer is allowed, because the index holds hooks only for rules that must fire unprompted and search recalls the rest. Report the new index byte size against the cap.

Output a report in two groups. **Per-entry lenses:** **(1) mechanism** — per claim `{confirmed | corrected | quarantined | flagged}` with the check run or required; **(2) overfit** — per candidate `{rewrite | merge | delete}` proposal; **(3) drift** — per candidate `{keep | relocate}` proposal; **(4) placement** — per candidate `{demote | promote | split}` proposal with its `decision-time` verdict; **(7) citations** — the missing and unparseable counts, then per entry `{repoint | fix owner | fix citation | retire}`. **Index-level lenses:** **(5) fragmentation** — measured index bytes/links plus the per-cluster delta against the anchor, then per cluster `{merge | keep split}` with the projected byte delta; **(6) injection duplication** — per index hook `{DUP | PARTIAL | UNIQUE}` with the emitting hook named, then `{cut | keep}`. Summarize counts; list the FLAGGED mechanism claims and all proposed edits (the actionable residue) for one-pass approval.

## Scope discipline
Report the few un-isolated claims, overfit headlines and feature-doc strays, and add lens 7's missing and unparseable citation counts as separate lists. The per-entry lenses are surgical; only the index-level lenses judge the whole index together. Most entries are preferences/process/event-facts, already-correct `feedback_*` corrections, genuine gotchas, or already in the right tier — so report-only, with targeted cheap checks, is a valid outcome, and re-validating sound entries or manufacturing findings is not the work.
