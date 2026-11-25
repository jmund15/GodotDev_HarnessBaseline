# Step 5: Present the design for review, approve taste-bearing or changed commitments

**Rule:** Do not present the full design as one wall of text. Present each load-bearing commitment in a form the owner can review, keeping related fields together (e.g., Data Model → Lifecycle → BB Keys → Tests → Integration Points). Obtain approval for taste-bearing or changed commitments before design-lock, and propagate any reopened decision to dependent commitments.

**Red-team hook (`--red_team` only):** [`red_team.md`](red_team.md) §Step 5 hook.

**Verbatim port discipline:** when sections are agreed in chat, port them **1:1** into the design doc. Do NOT silently digest the chat-form into a tighter file-form. See `feedback_no_unilateral_condensation.md` — *"chat content IS the spec when the user says 'save to file'."*

If the user pushes back on a commitment, treat it as a decision to make, not a clarification to dismiss. Update it, obtain approval when it changes a taste-bearing commitment, and propagate it to dependent commitments.

**Expect 1-2 substantive upstream reopenings per session.** Late-section content (Sections 4-5 — architecture detail, Parts decomposition) routinely surfaces assumptions baked into early-section (1-3) identity locks. Reopening Sections 1-3 from Section 5 feedback is correct behavior, not failure — the alternative is shipping a self-contradicting design. Update the upstream section, get re-approval, propagate downstream implications. If a session reopens 3+ times on the same Section 1 lock, the locked decision was wrong — escalate to a Socratic re-pass rather than continuing to patch.

**Checkpoint to scratch ledger.** **[Shared]** See [`_brainstorm_shared/common.md §8`](../../_brainstorm_shared/common.md) for path, append cadence, format rules, consumption, and lifecycle. Per-section bullet schema for this skill:

   ```markdown
   ## Section <name>
   - Approach chosen: <A/B/C — one-line rationale>
   - Key commitments: <2–4 bullets — concrete decisions made (file paths, class names, lifecycle choices)>
   - Open follow-ups: <Parts demoted to *-pending or *-rework state, if any>
   ```

   Append after each design section is approved. Worker reads it as `reference_files` in the final `write_doc` call (Step 6).

**Final mandatory phase: author Parts for the roadmap.** After all design sections are approved, read [`reference/part_authoring.md`](part_authoring.md) and run its sub-procedures in their stated order. Every architectural claim in the design body maps to exactly one of: {a Part on the roadmap, an explicit worklog deferral, abandoned with stated reason}. Get approval on the full Parts list before Step 8.
