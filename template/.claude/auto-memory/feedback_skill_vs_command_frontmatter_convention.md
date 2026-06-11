---
name: Skill vs command frontmatter convention
description: Custom commands and skills are the SAME mechanism post-merge — both model-invokable by default. Project convention uses `disable-model-invocation: true` on commands plus single-line `description:`; skills use multi-line `description: >-`.
type: feedback
originSessionId: 10c65425-68c7-4266-a24a-b35e9a15e00d
---

**The mechanism (per current Claude Code docs, `https://code.claude.com/docs/en/skills.md`):**

> "Custom commands have been merged into skills. A file at `.claude/commands/deploy.md` and a skill at `.claude/skills/deploy/SKILL.md` both create `/deploy` and work the same way."

Both file types support the same frontmatter spec. **Both are model-invokable by default** based on their `description:`. The only thing that prevents auto-invocation is `disable-model-invocation: true` (default `false`).

**Why:** prior versions of this memory claimed "commands are slash-invoked by definition; description doesn't drive auto-trigger." That was wrong post-merge — every command file in `.claude/commands/` was silently in the model's auto-invoke surface area until `disable-model-invocation: true` was added universally (2026-05-11 bulk update across 42 top-level commands + 11 `agents/` subdir files; `checklists/` retained as auto-invokable reference content).

**How to apply:**

1. **`disable-model-invocation: true` is the gate that makes a command slash-only.** Without it (or with no frontmatter at all), the command defaults to `false` and is model-invocable. NOTE (verified 2026-05-20): the claimed "universal bulk update across 42 commands" did NOT stick — `self_evaluate`, `eval_dashboard`, `autolearn`, `worklog` all shipped with NO frontmatter (hence default-invocable). Don't assume the gate is present; check the file. To explicitly enable auto-invoke on a frontmatter-less command, prepend a block with `disable-model-invocation: false` (omitting `description:` keeps the first-paragraph fallback intact).
2. **Skills (auto-invocable) need rich descriptions.** Multi-line `description: >-` block scalar; keyword-surface enumeration for trigger coverage. The matcher reads the description to decide when to load the skill.
3. **Commands (slash-only) need terse descriptions.** Single-line `description:`, ~90 chars line 1, action-verb-first. See `feedback_command_descriptions_one_line.md`. Long descriptions are pure context tax once auto-invoke is disabled.
4. **The skill/command split is functional, not categorical.** Both produce `/name` invocations. The meaningful axis is **procedure (executes step-by-step) vs reference (loads as context)**. Procedure → command file; reference → skill. Example: `instruction_quality` is a principle checklist consumed by `/instruction_audit` — stays as skill. `spell_balance_audit` was structured as a Step 1 / Step 2 / ... procedure — migrated to command 2026-05-11.
5. **Invalid frontmatter to avoid:** the `triggers:` YAML field (e.g., as seen in pre-migration `spell_balance_audit/SKILL.md`) is NOT in the documented frontmatter spec. Dead metadata; strip on migration.

**Litmus test:** before applying a frontmatter "fix" surfaced by an audit, check the matching artifact type's analog file. Skills compare to skills; commands to commands. And remember the merge: a command "missing" model-invocation control isn't broken — it just inherits the default `false`, which for this project is the wrong default.

**Source:** original observation 2026-04-29 (Batch B superpowers-cherry-pick adoption); rewritten 2026-05-11 after discovering the commands-merged-into-skills doc reality and applying universal `disable-model-invocation: true`.

**Verified (partial):** 2026-06-04 memory-claim audit — the *field-absence* half is mechanism-confirmed (grep: `worklog.md` has 0 occurrences of `disable-model-invocation`, consistent with the "didn't stick" note above). The *behavioral* half — "absence ⇒ model-invocable by default" — rests on the cited Claude Code docs, not an isolating auto-invocation test; left as doc-cited, not behaviorally isolated. To fully verify: add a frontmatter-less command with a distinctive description and confirm it auto-triggers.
