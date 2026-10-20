# Memory Index

> Auto-loaded at SessionStart (first 200 lines OR 25KB).
> One line per memory file, ≤150 chars — the hook TRIGGERS recall; mechanism lives in the topic file. Add a pointer when writing a new file.
> Organized by topic — file each new entry under its section.

## Communication & process discipline
- [Honor execution directive](feedback_honor_execution_directive.md) — "execute here" is final; don't re-ask continue-vs-handoff. Safety gates still pause.
- [No unilateral condensation](feedback_no_unilateral_condensation.md) — chat content IS the file spec; port 1:1, never silently digest.
- [Don't unilaterally reduce planned scope](feedback_dont_unilaterally_reduce_planned_scope.md) — plan is the contract; scope cuts need explicit user re-authorization.
- [Doc revision in place](feedback_doc_revision_in_place.md) — rewrite affected sections in body; never bury corrections as v1.1 addendums.
- ["Recommended fix" means implement](feedback_recommended_fix_means_implement.md) — default to shipping in-session; deferral needs explicit justification.
- [Plan worklog items from source, not the mirror](feedback_plan_worklog_items_from_source_not_mirror.md) — read the item's Context block; title-only mirror misleads on scope.

## Planning, brainstorm & handoff discipline
- [Resolve plan questions at plan-time](feedback_resolve_questions_in_plan_not_execution.md) — deferred decisions stall the executor or ship as guesses; Verify > ASK.
- [Spec-Doc Coverage subsection before plan-mode exit](process_rule_spec_doc_coverage.md) — map design-doc mechanics → plan section; missing rows = defer or pull in.

## Architectural & design rules
- [Inspect existing abstractions first](feedback_inspect_existing_abstractions_first.md) — extending a 2+ subclass family beats inventing parallel types.

## Tool routing & workflow
- [semantic-search restrictToDir is posix](gotcha_semantic_search_restricttodir_posix.md) — repo-relative posix path; absolute silently returns 0. Verify absence unrestricted.
- [read_files N≥4 needs completeness directive](feedback_read_files_multifile_completeness_directive.md) — extraction silently omits files without "return one per input path".
- [Verify Explore agent empirical claims](feedback_verify_explore_agent_empirical_claims.md) — 1-grep prior-art check before an agent claim shapes decisions; agents err confidently.
- [Send verbatim content to review agents](feedback_verbatim_content_to_review_agents.md) — never abbreviate code in an audit CONTEXT (agents flag it as defects); shared `contextPrefix` for size.
- [Separate pre-existing changes before commit](feedback_separate_preexisting_changes_before_commit.md) — bulk-mechanical commits: edit-signature detector isolates your changes.
