---
disable-model-invocation: true
---

# `/pr_ready` — Boot-warning correlation and warning budget

Read at `/pr_ready` Step 0b after the boot's `analyze_godot_logs.py` summary returns, to classify each warning group, and again to compare the groups with the warning baseline. Entrypoint: [`../pr_ready.md`](../pr_ready.md).

## Diff correlation — what makes a boot warning a finding

A pre-existing warning in a subsystem this PR never touched is noise for this PR; a warning naming something the PR changed is a finding. Build the correlation token set Claude-side from the frozen Step 0 diff:

- changed `.cs` basenames plus the type names declared in them
- changed `.tres`/`.tscn` basenames plus their `script_class` values
- the `[Subsystem]` log prefixes emitted by changed files: `git -C "$ROOT" grep -hoE "\[[A-Za-z]+\]" -- <changed .cs paths>`

| verdict | condition | tier |
|---|---|---|
| CORRELATED | a distinct group's text contains any correlation token | **critical** (`critical: true`): quote the group verbatim with its count and name the token that matched |
| UNCORRELATED | no token matches | `minRigor` thorough, in the roll-up only |
| NOT RUN | pin mismatch, launch failure, or empty log | a coverage gap, open at every rigor, naming which |

A CORRELATED group blocks even when the warning predates the PR: this is the last moment anyone will look at that subsystem with its code in hand.

## Warning budget — advisory

Distinct GROUPS are the health metric, not raw count — repeated-identical warnings collapse to one-with-a-count, which `--mode summary` already does. Compare this run's group signatures against `.claude/state/boot_warning_baseline.json` (`{"recorded_on": "<sha>", "groups": ["<signature>", ...]}`; create it on first run). Report the count delta and list the signatures new since the baseline. No baseline yet → record one from this run and report the raw count only. A run on `main` refreshes the baseline; a run on a branch never writes it.

**Advisory, never blocking**, and a fixed threshold is deliberately absent — it would be a number nobody re-tunes, and a rising count can come from an untouched subsystem or engine churn, so blocking would gate an author on what they cannot fix. Every group that matters is already critical by correlation. The budget's job is to give the noise floor an owner and a trend, so the log stays a readable instrument.
