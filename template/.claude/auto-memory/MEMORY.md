# Memory Index

> Auto-loaded at SessionStart (first 200 lines OR 25KB).
> One line per memory file, ≤150 chars — the hook TRIGGERS recall; mechanism lives in the topic file. Add a pointer when writing a new file.
> Organized by topic — file each new entry under its section.

## Communication & process discipline
- [Honor execution directive](feedback_honor_execution_directive.md) — "execute here" is final; don't re-ask continue-vs-handoff. Safety gates still pause.
- [User distress lexicon — STOP signal](feedback_user_distress_lexicon.md) — ALL CAPS / 'WHAT' / '????' → STOP; acknowledge + ask, no fix same turn.
- [No performative agreement](feedback_no_performative_agreement.md) — don't open with sycophantic agreement; restate, verify, or fix it.
- [No unilateral condensation](feedback_no_unilateral_condensation.md) — chat content IS the file spec; port 1:1, never silently digest.
- [Don't unilaterally reduce planned scope](feedback_dont_unilaterally_reduce_planned_scope.md) — plan is the contract; scope cuts need explicit user re-authorization.
- [Doc revision in place](feedback_doc_revision_in_place.md) — rewrite affected sections in body; never bury corrections as v1.1 addendums.
- ["Recommended fix" means implement](feedback_recommended_fix_means_implement.md) — default to shipping in-session; deferral needs explicit justification.
- [Don't defer immediately-addressable work](feedback_dont_defer_immediately_addressable.md) — scope-1 + harmless → DO IT NOW; worklog is for derail/judgment/later-info.
- [Plan worklog items from source, not the mirror](feedback_plan_worklog_items_from_source_not_mirror.md) — read the item's Context block; title-only mirror misleads on scope.
- [Slash command naming](feedback_slash_command_naming.md) — scan existing prefixes; convention-aligned names first.

## Planning, brainstorm & handoff discipline
- [Resolve plan questions at plan-time](feedback_resolve_questions_in_plan_not_execution.md) — deferred decisions stall the executor or ship as guesses; Verify > ASK.
- [Plan Mode is a Claude Code built-in](feedback_plan_mode_is_claude_code_built_in.md) — local skills/commands describe handoff, never internals.
- [Spec-Doc Coverage subsection before plan-mode exit](process_rule_spec_doc_coverage.md) — map design-doc mechanics → plan section; missing rows = defer or pull in.

## Architectural & design rules
- [Inspect existing abstractions first](feedback_inspect_existing_abstractions_first.md) — extending a 2+ subclass family beats inventing parallel types.

## Tool routing & workflow
- [semantic-search restrictToDir is posix](gotcha_semantic_search_restricttodir_posix.md) — repo-relative posix path; absolute silently returns 0. Verify absence unrestricted.
- [read_files enumerate first, no directory paths](feedback_read_files_enumerate_first.md) — glob to concrete paths before bundling; directory passing fails silently.
- [read_files N≥4 needs completeness directive](feedback_read_files_multifile_completeness_directive.md) — extraction silently omits files without "return one per input path".
- [read_files output volume governs spill](feedback_read_files_output_volume_governs_spill.md) — OUTPUT chars drive spill (≠ truncation); manifest is truncation-aware; cap is global per call.
- [Verify Explore agent empirical claims](feedback_verify_explore_agent_empirical_claims.md) — 1-grep prior-art check before an agent claim shapes decisions; agents err confidently.
- [Send verbatim content to review agents](feedback_verbatim_content_to_review_agents.md) — never abbreviate code in an audit CONTEXT (agents flag it as defects); shared `contextPrefix` for size.
- [Invoke the named slash-command](feedback_invoke_named_skill_not_manual_equivalent.md) — canonical artifacts (verdict header, tiered findings) matter; manual subs lose them.
- [Session-end command over passive nudge](feedback_session_end_command_over_passive_nudge.md) — registry-drift: /session_end-conditional + Step 0 git-diff gate, not PostToolUse stderr.
- [Separate pre-existing changes before commit](feedback_separate_preexisting_changes_before_commit.md) — bulk-mechanical commits: edit-signature detector isolates your changes.
