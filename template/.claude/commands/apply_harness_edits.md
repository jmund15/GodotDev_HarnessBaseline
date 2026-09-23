---
description: Apply queued harness edits after checking target drift and preserving unresolved entries
argument-hint: [--check-baseline]
---

Apply `.claude/pending_harness_edits.md`. No argument runs the normal queue; `--check-baseline` also surfaces baseline drift. Unknown arguments → report the accepted form and stop.

## Procedure

1. **Read the queue.** Absent or empty below the header → report `No pending harness edits.` and stop.
2. **Check targets and ownership.** Read each target and its current diff. A conflicting peer edit, active audit freeze, missing anchor or changed premise leaves that entry pending with its reason; continue independent entries. Do not overwrite drift or treat it as completed work.
3. **Apply exact entries** with `Edit`. Verify the changed decision, inbound references and relevant tests.
4. **Reconcile the queue.** Remove only successfully applied entries. Keep unresolved entries and their evidence. Delete an empty queue only after verifying it still contains no peer additions.
5. **Close within the user's Git authority.** Propose a commit unless already authorized; stage only these changes. Run the gate required by the actual staged file classes in CLAUDE.md §Build & Test Commands. With `--check-baseline`, surface `/sync_baseline` drift without publishing it.

## Queue-entry contract (for sessions WRITING the queue)

Each entry names the target, a durable anchor, exact replacement text and the decision it fixes. Merge into the existing queue without dropping another session's entries.

**Loading and timing:** queue non-load-bearing edits to likely startup-loaded surfaces when they can wait for a clean boundary: user/project CLAUDE.md, `MEMORY.md`, and registered catalog metadata. Apply corrections needed for the current task now when ownership permits. Follow the same-turn memory-index rule when saving a memory.

Skill/command bodies, path-scoped rules and reference files load through their triggers; inspect the actual registration before calling them deferred. Their text costs context when loaded. Hooks execute on matching events; emitted instructions are separate transcript content. Disk edits, catalog refresh and prompt-cache reuse are different events, and their timing depends on the client. Do not infer token savings from file bytes or promise cache-free edits.
