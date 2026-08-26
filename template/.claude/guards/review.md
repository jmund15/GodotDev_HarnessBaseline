# Guard: review — review / audit / critique lenses

Read ONLY the section matching your model tier: `terse` if you are an opus-class model, `strict` otherwise. `any.md`'s section of the same tier binds you too and arrives with this file. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## strict

- Every finding requires `agent`, `action` (FIX|ASK|PLAN), `category` (bug|rule|improvement), `description`, `rationale`. Give `file` as `"path/to/file.cs:line"` — the line is what disambiguates two findings in one file at dedup time. [review_fanout.js FINDINGS_SCHEMA]
- Field budgets: `description` ~400 chars (the defect, no restatement of rationale), `rationale` ~500 chars (cite the rule or invariant, do not re-derive it), `old`/`new` = the smallest span that makes the edit unambiguous, never a whole method or file. [review_fanout.js FINDINGS_SCHEMA]
- Severity is triage, not a correctness filter. Report every finding you found; never drop one for being minor and never cap your own list. [feedback_exhaust_review_findings_before_locking.md]
- Verify actual call sites, not just a type's public surface — a surface that reads as safe can still be misused where it is called. [feedback_exhaust_review_findings_before_locking.md]
- Never fabricate. Every finding names a `file:line` you actually read — that is rung 2 and is the floor for an unqualified finding. A finding you traced, matched by analogy to a sibling, or reached by grep is rung 3–5: report it and label the rung in `rationale`. A suspicion you could not verify is reported as unverified, or not at all. [reference/claim_confidence.md; feedback_delegate_output_trust.md]

## terse

- `file` goes in as `path:line` — it is the dedup key. Field budgets: `description` ~400 chars, `rationale` ~500, `old`/`new` the smallest unambiguous span.
- Severity is triage, not a correctness filter; verify call sites, not just the public surface: `feedback_exhaust_review_findings_before_locking.md`.
- An unqualified finding is rung 2 — a `file:line` you read. Traced / analogy / grep findings ship with the rung named in `rationale`: `reference/claim_confidence.md`.
