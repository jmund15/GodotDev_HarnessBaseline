# Doc production — spot-check, offline policy, review gate (§2–§4)

> Detail body of the brainstorming shared surface, read at the step named in [`common.md`](common.md).
> Section numbers are stable across the split; a § cited here that is not in this file is mapped to its file by the `common.md` index.

---

## §2. Rationale spot-check

After `write_doc` returns the structure digest, read load-bearing sections and cited artifacts in bounded calls until their claims, preserved blocks and links can be checked. A single read is sufficient only when it covers them.

- [ ] Each rejected approach names a SPECIFIC reason for rejection (not "less suitable" / "didn't fit").
- [ ] Each architectural commitment cites a concrete invariant, file path, or class name.
- [ ] No conversational provenance markers ("user surfaced", "per user direction") appear inline — those belong in Revision History.
- [ ] Idea pools state the target count and hold the complete pool.

If any check fails on a worker-written doc, refine the spec with explicit Must-include facts addressing the gap and re-run `write_doc`; do not edit that doc directly, so the worker stays the single author for voice consistency. A directly authored doc (§3, or the design doc per `flow_and_frontier.md` §8 Consumption) is corrected by editing it.

**Why this step exists:** pure structure-digest verification (per the global Documentation Delegation Rule) cannot detect rationale-density drift — compressed worker output passes structural checks while losing rejected-approach rationale.

---

## §3. write_doc Offline Policy

Brainstorm-doc reads (`Read`) and saves (`Write`/`Edit`) hit the vault filesystem directly. Full convention: CLAUDE.md §3 / `obsidian_conventions` skill.

**`write_doc` offline:** the substitute executor follows CLAUDE.core.md §9 *Offline fallback* and `orchestration` §5. Author directly with a native `Write` from the calling skill's template; the §2 rationale checks still apply. Provenance and per-machine availability: `environment_bootstrap`.

---

## §4. User review gate

After the calling skill's spec-review loop passes, ask the user to review the written spec before proceeding:

> "Spec saved to `<path>`. Changed: <what>. Recommended next step: <continue / hand off to the next skill>. Review it and tell me any changes."

If they request changes, make them and re-run the spec review loop. Only proceed once the user approves.
