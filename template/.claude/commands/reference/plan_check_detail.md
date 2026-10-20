---
disable-model-invocation: true
---

# `/plan_check` — Phase 1 context and Phase 3 rounds

Read at `/plan_check` §1f–1g (Phase 1 context) and at Phase 3 when the verdict calls for a revision round. Entrypoint: [`../plan_check.md`](../plan_check.md).

## Phase 1 context

### 1f. Code surfaces
For code plans, enumerate introduced types and authored fields/parameters/flags that the dossier's owner and consumer claims do not already cover. Find existing owners by name and behavior; include relevant callers, scene/resource references and generated abstraction-family rows. Use LSP when available; state search-only gaps otherwise. A new name does not prove a new concern. Two `[GlobalClass]` Resources sharing a simple name collide regardless of namespace.

### 1f-meta. Inbound references
A meta plan lists each renamed, rewritten or deleted harness rule, path, anchor and key in its `surfaces` block ([`plan_file_format.md`](../../skills/_brainstorm_shared/plan_file_format.md) §Surfaces touched). [`lens_briefs.py`](../../tools/lens_briefs.py) is the one producer of `INBOUND_REFS`: for each `--subject-plan meta:<plan>` it runs [`citers.py`](../../tools/citers.py) and embeds counts, coverage flags and a full sweep artifact pointer in each `citers: plan` lens's brief; it refuses a meta plan with no block. The parent and seats retrieve the relevant citer, duty and per-root coverage records from that artifact before judgment. Inspect the active callers and fixtures those records list; a vault root it reports `absent` or `unattempted` was not searched. A rename must not leave a matcher or test silently on the retired contract. Distinguish intentional policy migration from accidental contradiction.

### 1g. Lens-specific context
Give each seat the full plan plus the evidence its mandate declares, never a shared megabundle of every skill (`orchestration` §4). Inject facts (surfaces, sibling counts, structure), never conclusions. Label inherited claims unverified until checked. The seat's brief inlines its lens's declared doctrine ([`lens_briefs.py`](../../tools/lens_briefs.py)), so CONTEXT does not repeat it. Code design lenses receive the authored-data litmus the project's stack layer names and the matching family rows; test readiness receives the TDD contract; memory alignment receives the `/explore` dossier path the plan's Constraints section names, because the evidence behind each cited claim id lives there; doctrine consistency reads the actual changed owners. On a meta or mixed plan, before the first round and whenever the plan text changes, run `python3 .claude/tools/extract_numbers.py list <plan>` and `python3 .claude/tools/restate_scan.py pairs --text <plan> --json` over the current plan text. Pass both outputs to `plc-instruction-quality` and `plc-doctrine-consistency`. For tool-behavior claims, the parent saves `python3 .claude/tools/extract_numbers.py check --claims behavior --paths <claim-surfaces> --json` to scratch and includes its full artifact path in `plc-evidence-grounding`'s context; that lens's process owns the implementation and outcome reading.

## Phase 3 rounds

Revise critical architecture findings and reconverge per `orchestration` §2 *Later rounds*, which owns how each changed unit is checked. The post-check's `nextRound` block reports the facts that choice reads: `nextRound.uncovered` (per lens, the mandates and files no reviewer covered since the change) and `nextRound.change` (the measure). A seat, when the choice calls for one, merges mandates by Phase 2's merge order. The report states the measure, the check chosen per unit and why. Your own design edits sit outside any fold record: measure them with `python3 .claude/tools/fold_check.py --measure <reviewed copy> <plan> --out <file>`, where the reviewed copy is a scratch file holding the plan's snapshot blob (`git cat-file blob <sha> > <scratch file>`, the sha from the manifest's `files`), and add their units to the round. A plan halted under `drive_spine.md` §Halts H5 goes to the owner to split or re-plan. Scope added during a check is its own plan with its own first round, never a later round of this one. For split plans, a round re-sends only the plans holding its units.
