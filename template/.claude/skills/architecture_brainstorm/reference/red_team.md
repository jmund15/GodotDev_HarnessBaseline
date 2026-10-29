# Red-team hooks (`--red_team` / `--auto` only)

Read at [`SKILL.md`](../SKILL.md) §4 when the skill is invoked with `--red_team` or `--auto`. Without either flag, the default flow is unchanged and the Step 7 Mode A pass ([`doc_and_review.md`](doc_and_review.md)) is the only red-team touchpoint.

## `--red_team`

When invoked as `/architecture_brainstorm <topic> --red_team`, an adversarial [`/architecture_brainstorm_redteam`](../../../commands/architecture_brainstorm_redteam.md) **Mode B** pass fires at each Socratic boundary (Steps 2/4/5) on a *phase-scoped* lens subset — sharpening the next question with surfaced taste-forks and folding design-holes into the live design before it hardens. It is **opt-in** and **never decides taste** ([`_brainstorm_shared/appetite_invariant.md`](../../_brainstorm_shared/appetite_invariant.md)) — an interleaved design-hole adjusts the current approach, but a taste-fork becomes the *next* Socratic question.

## `--auto`, alone or combined

`--auto` governs **only the Step-7 Mode A drafted-doc pass** (its bounded generate↔critique↔revise loop). It NEVER drives the Mode B interleaving — auto-advancing the live Socratic dialogue would auto-answer taste-forks and fabricate your vision (the appetite-invariant violation). So `--red_team --auto` = human-gated interleaving through Steps 2/5 **plus** an auto-hardened Step-7 pass; the two-step taste-batch → reconcile → approve gate still governs the result. (`--auto` without `--red_team` is also valid — it just auto-runs the Step-7 pass with no interleaving.)

## Step 2 hook

After the user answers a clarifying question, before composing the next one, dispatch the redteam in **Mode B** on the tentative pick (Step-2 lens subset: `rt-boundary` + `rt-failuremode` + `rt-yagni-scope` — the three that can fire pre-commitment). Fold any design-hole into the pick; turn each taste-fork into the *next* Socratic question. The critic never picks a taste-fork for the user.

## Step 4 hook

Once the approaches are drafted, dispatch **Mode B** on them (Step-4 subset: adds `rt-abstraction`, plus `rt-testability` if an approach already names test surface). This is the richest interleave seam — approaches are the first concrete shapes the abstraction/boundary lenses can attack. Fold design-holes into the trade-off comparison; surface taste-forks as the choice the user makes *between* approaches.

## Step 5 hook

After a section is drafted, before owner approval of its commitments, dispatch the **full 5-lens** Mode B panel on that section (it now has committed shape — `rt-abstraction` and `rt-testability` can finally bite). Design-holes → revise the section before presenting; taste-forks → fold into the section's approval decision; a dead-end halts the brainstorm for a reframe.
