# Guard: review — review / audit / critique lenses

Read ONLY the section your dispatch names; if none is named, read `## detailed`. `any.md`'s section of the same tier binds you too and arrives with this file. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## detailed

- Every finding requires `agent`, `action` (FIX|ASK|PLAN), `category` (bug|rule|improvement), `description`, `rationale`. Give `file` as `"path/to/file.cs:line"`: an anchor for the reader, never finding identity, so two findings may share it. [review_fanout.js FINDINGS_SCHEMA]
- Keep each field to its job: `description` states the defect and never restates the rationale, `rationale` cites the rule or invariant and never re-derives it, and `old`/`new` hold the smallest span that makes the edit unambiguous, never a whole method or file. For FIX, `new` is the literal replacement text; a fix you can only describe is a PLAN with `scope` and `options`. [orchestrator_action_protocol §Finding Schema]
- Apply orchestration §8's substantive-finding filter, whatever the severity; never cap your own list. A brief that asks for fewer ("only the top finding") is a rail/brief conflict: put its pick first, list the rest after it, and name the conflict. [orchestration §8]
- Verify actual call sites, not just a type's public surface — a surface that reads as safe can still be misused where it is called. [orchestration §8]
- Never fabricate. Every finding names a `file:line` you actually read — that is rung 2 and is the floor for an unqualified finding. A finding you traced, matched by analogy to a sibling, or reached by grep is rung 3–5: report it and label the rung in `rationale`. A suspicion you could not verify is reported as unverified, or not at all. [reference/claim_confidence.md]

## condensed

- `file` goes in as `path:line`, an anchor, never finding identity. `description` names the defect without the rationale, `rationale` says why it is wrong and what breaks, and `old`/`new` is the smallest unambiguous span. For FIX, `new` is the literal replacement text; a fix you can only describe is a PLAN with `scope` and `options`. [orchestrator_action_protocol §Finding Schema]
- Apply orchestration §8's substantive-finding filter, whatever the severity. A brief that caps the count is a conflict: put its pick first, list the rest, and name the conflict.
- Verify call sites, not just the public surface: orchestration §8.
- An unqualified finding is rung 2 — a `file:line` you read. Traced / analogy / grep findings ship with the rung named in `rationale`: `reference/claim_confidence.md`.

## minimal

Read this file's `## condensed` section.
