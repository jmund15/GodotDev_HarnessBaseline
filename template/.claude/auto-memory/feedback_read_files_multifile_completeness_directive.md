---
name: read-files-multifile-completeness-directive
description: "ai-worker read_files multi-file extraction silently omits files (returns N-1 of N) without an explicit completeness directive in the question. Fix is the prompt instruction, NOT a cap change, chaining, or size reduction. Verified 2026-05-19."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: df2b3626-2692-4176-8ae7-e5953e99385a
  modified: 2026-07-27T22:36:20.828Z
---

When using `mcp__ai-worker__read_files` to extract structured data across multiple files (N≥4), the question MUST include an explicit completeness directive: *"You MUST return one entry per input path (N total). Do NOT silently omit any file. If a file can't be parsed, include an entry with a 'reason' field rather than omitting."* Then verify output shape post-call: count returned entries against `len(paths)`; retry missed entries individually.

**Why:** 2026-05-19 — a 4-roadmap bundled full-Parts extraction (for `/roadmap_next`) returned complete JSON for 3 roadmaps and silently omitted the 4th. No truncation marker, `finish_reason="stop"`, output only ~4K tokens (far under the 32K `max_tokens_reader` cap). I misdiagnosed it twice — first as a cap-hit (wrong: cap was 32K, not the 8K I assumed; no truncation marker means finish_reason≠length), then as structural confusion (wrong: the 4th file's Parts table parsed cleanly on retry). **The actual cause: the question lacked a completeness directive, so the model defaulted to "synthesize what fits comfortably" rather than "produce N entries."** Retry with the SAME inputs + SAME output schema + the completeness directive succeeded completely (all 4, ~25KB).

**How to apply:**
- The completeness directive is the DOMINANT lever — not size reduction, not chaining, not a cap change.
- A large output (25KB+) is NOT bloat if every field feeds downstream logic. Two axes: minimize *prose verbosity* always (append "Return ONLY the JSON, no commentary"); size *data completeness* to what the caller consumes. Don't have the worker digest/interpret to shrink output — that violates Claude=thinking / worker=I/O.
- Chaining narrow calls or `model="kimi"` is the last resort, only when output legitimately approaches the cap (>16K tokens) — NOT a fix for the silent-omission failure.

**The directive is necessary but NOT sufficient — verify the count against ground truth, not the worker's own total (2026-07-27).** A SINGLE-file digest of `Worklog.md` (636 lines) carrying the full directive *and* an explicit "state the total row count as `TOTAL: <n>`" returned 97 rows and confidently declared `TOTAL: 97`. The file actually held **111** Active items — a silent 13% omission with a self-reported total that agreed with the truncated output. It surfaced only because a later count of the source disagreed. So:

- **The failure mode is not multi-file-specific.** One large file with many repeated records fails the same way; N≥4 is a trigger, not the boundary.
- **A self-reported total is not verification** — the model counts what it emitted, not what existed. Verify against something the worker did not produce: `grep -c` the source, or a count derived from a separate read.
- Consequence when unverified: any ranking, triage, or "top N" selection drawn from the digest is silently drawn from an incomplete pool.

Full reference: `ai_worker_model_guide.md` → "read_files — Output Discipline". Sibling gotcha: [[feedback_read_files_enumerate_first]] (glob to concrete paths before bundling).
