# Brainstorming — Shared Procedures (index)

> Shared procedure surface for the [`idea_brainstorm`](../idea_brainstorm/SKILL.md) and [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) skills, and for every command that cites *"`_brainstorm_shared/common.md` §X"*.
>
> This file is the index: the section bodies live in the sibling files below. The map below resolves each `common.md §X` citation.

---

## Resolve a `common.md §X` citation

| Cited section | Topic | Read this file |
|---|---|---|
| §1, §1.1, §1.2 | Existing-doc check; resume-from-there; stale-roadmap remediation | [`existing_doc_check.md`](existing_doc_check.md) |
| §2, §3, §4 | Rationale spot-check; MCP/worker-offline policy; user review gate | [`doc_production.md`](doc_production.md) |
| §5, §5.1 | Doc path, folder-per-topic, frontmatter; spawn placement | [`doc_conventions.md`](doc_conventions.md) |
| §6, §6.1–§6.9 | `roadmap.md` frontmatter, Parts table, State vocabulary, Mermaid, derived views, revision log, cross-folder deps, hierarchical roadmaps | [`roadmap_schema.md`](roadmap_schema.md) |
| §6.10–§6.13 | Trigger content semantics, MVP Checkpoints, cross-roadmap dependencies, Roadmap Atlas | [`roadmap_triggers_mvp.md`](roadmap_triggers_mvp.md) |
| §7, §8, §9 | Workflow phase cardinality; `decisions.md` frontier; interview rounds | [`flow_and_frontier.md`](flow_and_frontier.md) |

## Constraints that hold across every phase

- Existing-doc check runs through `/explore`, never hand-rolled — [`existing_doc_check.md`](existing_doc_check.md) §1.
- A fork is logged to `decisions.md` in the turn it is classified — [`flow_and_frontier.md`](flow_and_frontier.md) §8.
- Every `roadmap.md` at every depth is authored by [`/update_roadmap`](../../commands/update_roadmap.md), which owns the single-executor rule; a brainstorm save step saves the design doc only.
- A saved doc is revised in place — [`doc_conventions.md`](doc_conventions.md) §5.
- A parent roadmap copies no per-state breakdown of a child — [`roadmap_triggers_mvp.md`](roadmap_triggers_mvp.md) §6.10.
- Every factual question in a round is resolved with tools before the round reaches the user — [`flow_and_frontier.md`](flow_and_frontier.md) §9.

## Sibling files in this folder (separate owners, not part of the §-numbering)

- [`design_contract.md`](design_contract.md) — invariants for autonomous design work; consumed by `/design_drive`.
- [`plan_file_format.md`](plan_file_format.md) — what an approval-ready plan file must read like.
- [`appetite_invariant.md`](appetite_invariant.md) — the rigor-hole vs taste-fork classification rule.
- [`execution_depth.md`](execution_depth.md) — the work triage owner for the drive commands: fields, admission table, re-triage.
