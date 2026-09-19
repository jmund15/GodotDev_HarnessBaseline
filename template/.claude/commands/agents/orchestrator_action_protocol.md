---
disable-model-invocation: true
---

# Orchestrator Action Protocol

One owner for consuming review findings. Keep original evidence, verify the claim, then act under the caller's authority.

## Finding Schema

Use `.claude/schemas/review_findings.json`; do not maintain a second schema example here. A finding names its agent, action, category, location, claim and rationale. The schema leaves `old`/`new`/`question`/`options`/`scope` optional, but a lens must still supply the pair its action needs: FIX carries `old`/`new`, ASK carries `question`/`options` ranked best guess first, PLAN carries `scope`. A downstream consumer such as `pr_pipeline` applies FIX and resolves ASK/PLAN from those fields, so a finding missing them is not actionable even though it validates.

### Action Tiers

| Action | Meaning |
|---|---|
| FIX | A supported, concrete correction; verify any edit pair against current source |
| ASK | A genuine owner choice; give clear options and a recommendation when defensible |
| PLAN | An unresolved design problem whose scope/approach needs work |

Finite actionable alternatives normally use ASK rather than PLAN. Severity is triage, not permission to discard a valid improvement. `NOTE` is parent synthesis, not an agent escape tier.

### Category Tags

`bug` changes correctness; `rule` violates an explicit obligation; `improvement` makes the result better without claiming either. Cite the real obligation or consequence.

### Critical Flag

Use `critical` for material correctness, safety or silent-failure exposure. Missing means false, not unreviewed. Do not turn an unverified hypothesis into a critical fact.

## Orchestrator Action Protocol

### Step 1: Merge & Deduplicate

Preserve every original finding with an engine-owned source ID before sorting/grouping. A location is an anchor, never identity. Distinct defects at one line survive; duplicate contributors retain provenance.

`review_fanout.js` returns immutable source records plus a derived view. Its optional merger groups source IDs; original facts and edit proposals remain authoritative. Invalid grouping falls back to originals. Report received/valid/grouped counts and missing coverage separately. Grouping is not verification or proof that every member has been fixed.

### Step 1.5: Verify FIX Findings Against Actual File Content

For each original FIX:
1. Locate the current file and relevant declaration/region. A cited line is a hint; do not manufacture a Read continuation offset from a line number.
2. Verify the claimed defect and applicable runtime/authored inputs, not merely that a quote exists.
3. Before Edit, confirm the exact `old` text and intended target are unique. Whitespace-normalized similarity is orientation, not an exact-edit verification.
4. If it does not match, check drift and quoting before calling it fabricated. Keep the finding unverified until resolved; never auto-apply a guessed replacement.

For grouped views, resolve the original source IDs. Different edit pairs are separate actions; do not concatenate snippets or treat one applied patch as completion of the entire group.

### Step 2: Present Unified Report

Start with a bounded overview: outcome, counts, unresolved/critical IDs (`delivery.criticalFindingIds` / `delivery.askFindingIds` carry every one, even those not inline) and the full artifact. Unverified findings are visible first. Order: critical first, then FIX → ASK → PLAN, then bug → rule → improvement. Preserve all original evidence and account for every item before acceptance. A display limit never drops a finding.

Each displayed item states the defect, evidence, consequence and next action plainly. Fetch exact old/new/source details when acting; do not duplicate every full block in the parent merely to report it.

### Step 3: NOTE Synthesis (Orchestrator-Only)

Reconcile cross-lens patterns and contradictions against source evidence, not majority vote. Notes may connect findings; they do not replace required dispositions.

### Step 4: Execute Actions

Use the caller's existing approval or explicit unattended authority; do not add a second confirmation ritual. Otherwise obtain approval for the proposed actions before applying them.

- FIX: apply verified corrections and run the relevant checks. Preserve peer work.
- ASK: obtain the real owner decision; do not infer an ambiguous answer. Under `/overnight`, make defensible reversible choices and park actual blockers.
- PLAN: resolve the design and scope, then follow the planning contract. Do not quietly defer an in-scope, addressable item.

Record each source finding as fixed, rejected with evidence, or blocked by a named fact. A grouped view or successful tool exit is not its disposition.

## Claims to Refuse

Do not predict passing tests, claim an unread artifact is correct, or use “should work” as verification. Cite actual results or state what remains to run. A completed decision is re-checked at its premise before a clean verdict; `tools/task_record.py check --rerun` names the stale and unrepeated ones. A located quote proves presence, not the claim built from it. Keep observed, inferred and unknown separate.

## Verdict Logic

| Verdict | Criteria |
|---|---|
| APPROVE | No unresolved critical or scope gap; at most two noncritical notes |
| APPROVE WITH NOTES | No unresolved critical; remaining findings have explicit dispositions |
| REQUEST CHANGES | Unresolved critical, missing required coverage or a blocking owner/design decision |

The calling command may define a stricter gate. Preserve that contract without replacing it with an arbitrary score or a count of agreeing agents.
