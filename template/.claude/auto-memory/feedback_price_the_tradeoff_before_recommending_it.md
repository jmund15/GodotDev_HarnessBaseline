---
name: feedback_price_the_tradeoff_before_recommending_it
description: "A recommendation that trades A for B must measure B's cost BEFORE it is offered — withdrawing it after the user approves reads as flailing, and is"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 40a30e2c-3efb-4fd4-ad4d-3a6ee32ba28f
  modified: 2026-08-21T03:06:59.129Z
---

# Measure the cost side before you recommend the trade, not after approval

**Signal (2026-08-20):** recommended swapping the pinned local model to a sibling with a lower
observed failure rate. The user approved. The first check afterwards — GPU residency — showed the
replacement never fully fits the card and runs **33% slower on every call**, against a failure-rate
difference that was not even statistically resolved (p=0.21). The recommendation was withdrawn one
message after it was accepted.

The user's response was the correct one: *"you just gave me recommendations and then removed them
— are you just flailing around?"*

**Why:** a recommendation is a claim that the benefit exceeds the cost. Presenting one with only
the benefit measured is presenting half a claim as a whole one. The user then spends a decision on
it, and reversing costs their trust in every *other* recommendation in the session — including the
correct ones. Withdrawing on new evidence is right; needing to withdraw was avoidable, because the
gating measurement took two minutes and could have run before the sentence was written.

**How to apply:**
- **Name the cost axis before drafting the recommendation.** Swapping a component: what does the
  replacement cost in speed, memory, fit, API surface, migration? Removing a guard: what does it
  cost in coverage? If you cannot name the axis, you do not yet understand the trade.
- **Measure it, or state it as unmeasured IN the recommendation.** "Swap to X" and "Swap to X if it
  holds the window — unverified, 2 minutes to check" are different claims. The second is honest and
  costs nothing.
- **Do not exclude the evidence that favours the incumbent.** The quality comparison offered cut the
  one axis the incumbent had been selected on, which made the case look one-sided when it was not.
  When a component was chosen deliberately, find *why* before proposing to replace it — the
  selection rationale is usually written down next to the pin.
- **An unresolved benefit against a measured cost is not a close call.** p=0.21 against a certain
  33% is a decision, not a judgement call.

Related: [[feedback_recommended_fix_means_implement]] — once a recommendation IS made and accepted,
it is work to do, which is exactly why the bar for making it is this high.
[[feedback_controlled_contrast_or_it_proves_nothing]] governs whether the benefit number is real
in the first place.
