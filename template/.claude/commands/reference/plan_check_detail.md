---
disable-model-invocation: true
---

# `/plan_check` — Phase 1 context and Phase 3 rounds

Read at `/plan_check` §1f–1g (Phase 1 context) and at Phase 3 when the verdict calls for a revision round. Entrypoint: [`../plan_check.md`](../plan_check.md).

## Phase 1 context

### 1f. Code surfaces
For code plans, enumerate introduced types and authored fields/parameters/flags that the dossier's owner and consumer claims do not already cover. Find existing owners by name and behavior; include relevant callers, scene/resource references and generated abstraction-family rows. Use LSP when available; state search-only gaps otherwise. A new name does not prove a new concern. Two `[GlobalClass]` Resources sharing a simple name collide regardless of namespace.

### 1f-meta. Inbound references
A meta plan lists each renamed, rewritten or deleted harness rule, path, anchor and key in its `surfaces` block ([`plan_file_format.md`](../../skills/_brainstorm_shared/plan_file_format.md) §Surfaces touched). The parent and seats retrieve the relevant citer, duty and per-root coverage records from the sweep artifact that each `citers: plan` lens's brief points to, before judgment. Inspect the active callers and fixtures those records list; a vault root the sweep reports `absent` or `unattempted` was not searched. A rename must not leave a matcher or test silently on the retired contract. Distinguish intentional policy migration from accidental contradiction.

### 1g. Lens-specific context
Give each seat the full plan plus the evidence its mandate declares, never a shared megabundle of every skill (`orchestration` §4). Inject facts (surfaces, sibling counts, structure), never conclusions. Label inherited claims unverified until checked. The seat's brief inlines its lens's declared doctrine ([`lens_briefs.py`](../../tools/lens_briefs.py)), so CONTEXT does not repeat it. A seat declaring `fixes: plan` (`plc-whole-plan`) gets a `FIX_HISTORY` section there: each runtime harness file the subject plan touches, its counted fixes in the census window, and `k of n` since listing; the full history sits in the fixes file the section names. Code design lenses receive the authored-data litmus the project's stack layer names and the matching family rows; test readiness receives the TDD contract; memory alignment receives the `/explore` dossier path the plan's Constraints section names, because the evidence behind each cited claim id lives there; doctrine consistency reads the actual changed owners. On a meta or mixed plan, before the first round and whenever the plan text changes, run `python3 .claude/tools/extract_numbers.py list <plan>` and `python3 .claude/tools/restate_scan.py pairs --text <plan> --json` over the current plan text. Pass both outputs to `plc-instruction-quality` and `plc-doctrine-consistency`. For tool-behavior claims, the parent saves `python3 .claude/tools/extract_numbers.py check --claims behavior --paths <claim-surfaces> --json` to scratch and includes its full artifact path in `plc-evidence-grounding`'s context; that lens's process owns the implementation and outcome reading.

## Phase 3 rounds

Revise critical architecture findings, then size the next round before writing its `spec.json`. That spec starts empty, never from the earlier round's seats, pins or efforts.
1. Read `orchestration` §2 *Later rounds* and the revised diff against the plan's snapshot blob (`git cat-file blob <sha>`, the sha from the manifest's `files`).
2. For each changed unit, either read it against its finding and record it inline (`review_ledger.py record --inline <who> --mandate <m> --delta <prepare's delta artifact> --accounted <the hunk ids read>`), or name the mandate that must judge it.
3. The seats are the named mandates, merged by Phase 2's merge order and pinned per Phase 2 for the units they check. A revision that names no mandate runs no round.

`review_ledger.py plan --stage plan-check --subject <subject> --roster <run dir>/roster.json --files <plan>` names the mandates the revised bytes leave uncovered: a ceiling on the seats, never the seat list. The report states the check chosen per unit and why. A plan halted under `drive_spine.md` §Halts H5 goes to the owner to split or re-plan. Scope added during a check is its own plan with its own first round, never a later round of this one. For split plans, a round re-sends only the plans holding its units.
