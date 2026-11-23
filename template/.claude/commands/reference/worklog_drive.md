---
disable-model-invocation: true
---

# `/worklog drive`

Read at a `/worklog drive` and run from these steps. This file owns choosing which logged items to ship; `/drive` ships them, with its slices, gates and close, and nothing here restates them. `commands/worklog.md` owns the files and tiers.

**Forms**, after stripping `--plan-only`; first match wins:

1. No argument: **survey**. Propose the batches the evidence supports and let the owner pick; nothing ships until a pick.
2. The whole argument matches `^(\d+|scope:\d+|items:\d+)(\s+(scope:\d+|items:\d+))*$`: **budget**. A bare `<N>` means `scope:N`. Fill until the scope sum reaches `scope:N` or the count reaches `items:N`, whichever binds first. The limits bound the set; they never rank it.
3. Otherwise: **named**. Comma-split the argument and match each name against the ready items in Active and Ledger. Several matches ask; none stops, never substituting a near miss. The owner's names are the set, unranked.

The invocation is the order to ship: a budget or named set ships without a further ask, and a survey ships the batch the owner picks. `--plan-only` stops at Step 4 with the plan written.

**Scope 4 never ships here.** A scope-4 item, found or named, is listed apart with its `Plan doc:` and routed to `/plan`. A `class: design` item routes to `/plan` the same way. A named item that waits (`When: after`) prints its condition and offers `/worklog unblock`; Future Scope is never a candidate.

## Step 1 — Shortlist

Read the mirror for the ready items' titles, classes and scopes, then read the full Worklog.md block of every shortlisted or contested item before choosing: the mirror is an index, and a title can mislead about scope. A named set skips to Step 4.

## Step 2 — Evidence

For each shortlisted item, record with its source:

- overlap of current uncommitted work with its `Where:` paths; an item already in progress ships as a continuation of that work;
- commits touching its `Where:` or `Source:` since its recorded state, with the range searched;
- whether a named blocker cleared, checked at the change that would clear it;
- a source, owner, dependency or expected behaviour that changed under it (staleness; age alone is not);
- a change seam or verification surface it shares with another item (a batch candidate; a shared domain label alone is not);
- an earlier attempt recorded in its block (a re-scope note or blocker): it is eligible again only when that blocker or its diagnosis changed.

## Step 3 — Choose

Compare the shortlisted ready items by readiness, evidence, dependencies, write-set fit, owner intent and effort; state what each choice displaces, and print no scores. A `Quick-win:` flag is owner intent and never outranks a live blocker.

- **Survey:** print each supported batch with its items and the evidence for grouping them, then the scope-4 items, then the waiting items with their conditions. One clear set is shown as one set. The owner's pick enters Step 4 with its blocks already read.
- **Budget:** fill to the limit. When an equally supported alternative changes the set at its boundary, ask one packet with the recommended set as the default. No ready item: print `Worklog clear: no ready items in Active or Ledger.` with the waiting count, and stop.

## Step 4 — Plan and ship

1. Write one plan for the set, in `/plan`'s plan format, from each item's full block: its Context and Where verbatim, the steps, and how each item's completion is verified. Partition a set that holds two plan shapes into one plan each. An audit-shaped item (Audit, Verify, Review, Check, or a read-and-decide Context) runs its reads while planning and plans only the changes its verdict needs; a compliant verdict completes the item with the verdict as the ref.
2. `--plan-only`: stop here and report the plan path.
3. Run `/drive <plan path>`. Each item ends in one state: completed through `/worklog complete` with its commit ref, or left `[ ]` with a note on its block naming the attempt and its blocker, which is the record the next drive's Step 2 reads. When the diff leaves an item's scope, rewrite its `scope` value and leave it open; a budget set continues only while the remaining budget covers the new value, and a new scope 4 routes to `/plan`.
4. Report every item with its end state; an unlisted item means the drive is not done.
