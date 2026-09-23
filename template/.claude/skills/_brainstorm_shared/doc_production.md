# Doc production — spot-check, offline policy, review gate (§2–§4)

> Detail body of the brainstorming shared surface, read at the step named in [`common.md`](common.md).
> Section numbers are stable across the split; a § cited here that is not in this file is mapped to its file by the `common.md` index.

---

## §2. Rationale spot-check (one-shot)

After `write_doc` returns the structure digest, do ONE bounded `Read(file_path=..., offset=N, limit=M)` covering the architecturally load-bearing sections — typically the Approach Selected paragraphs, the Recommended-starting Part rationale (architecture skill only), and any leading-hypothesis flags. Verify:

- [ ] Each rejected approach names a SPECIFIC reason for rejection (not "less suitable" / "didn't fit").
- [ ] Each architectural commitment cites a concrete invariant, file path, or class name.
- [ ] No conversational provenance markers ("user surfaced", "per user direction") appear inline — those belong in Revision History.
- [ ] Idea pools are sampled (target count + 5–10 entries), not exhaustively enumerated.

If any check fails, refine the spec with explicit Must-include facts addressing the gap and re-run `write_doc`. Do NOT edit the doc directly — keep the worker as single author for voice consistency.

**Why this step exists:** pure structure-digest verification (per the global Documentation Delegation Rule) cannot detect rationale-density drift — compressed worker output passes structural checks while losing rejected-approach rationale.

---

## §3. write_doc Offline Policy

Brainstorm-doc reads (`Read`) and saves (`Write`/`Edit`) hit the vault filesystem directly. Full convention: CLAUDE.md §3 / `obsidian_conventions` skill.

**ai-worker / `write_doc` offline → substitute the executor, keep the routing contract.** Per CLAUDE.md §Tool Routing *Offline fallback*, a subagent takes the bundling role (scout tier for copyable synthesis, fan-out tier when it must be derived; `orchestration` §5 picks the role). `write_doc` offline → author directly with native `Write` using the calling skill's template/frontmatter (§2 rationale checks still apply). Provenance + per-machine availability: `environment_bootstrap`.

---

## §4. User review gate

After the calling skill's spec-review loop passes, ask the user to review the written spec before proceeding:

> "Spec written and committed to `<path>`. Please review it and let me know if you want to make any changes before we [continue / hand off to the next skill]."

If they request changes, make them and re-run the spec review loop. Only proceed once the user approves.
