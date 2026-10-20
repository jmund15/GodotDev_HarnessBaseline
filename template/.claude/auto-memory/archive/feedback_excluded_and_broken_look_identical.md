---
name: feedback-excluded-and-broken-look-identical
description: "An opt-out annotation makes a broken check indistinguishable from a skipped one — an excluded check needs a trigger, or it bought nothing"
metadata: 
  node_type: memory
  type: feedback
  modified: 2026-08-18T05:55:09.771Z
---

Marking a check "deliberately excluded" fully explains its silence. From then on a check that
CANNOT run is evidence-identical to one you chose not to run, so the annotation written to explain
the absence is what hides the defect.

**Why:** absence of failure reports is the only signal a check emits when healthy, and an exclusion
note pre-empts the one question that would surface a break ("why have we never seen this run?").
Deliberate exclusion is often correct on cost grounds — slow batteries do not belong in a
pre-commit gate — but exclusion without a replacement trigger means the check never executes, so
authoring it bought nothing while reading as coverage.

Measured: a stress battery authored 2026-07-24 with "gate-excluded" in its own commit title, and a
plan file spelling out "gate-unreachable, compile-verify explicitly". It threw on a missing autoload
requirement that predated it by two months — it had never passed once, and nothing reported that in
25 days.

**How to apply:** an excluded check needs a named trigger it actually fires under (a command, a
periodic run, a different gate) recorded beside the exclusion; "verify manually" is not a trigger.
Better, make the exclusion itself detectable — assert that every test namespace is selected by some
filter, so the next excluded surface cannot go unnoticed. When a delegate or a doc calls something
"pre-existing" or "excluded", that is where to look, not where to stop. Related:
[[feedback_leak_sweep_without_adjudication_reads_as_clean]], [[feedback_fix_the_class_not_the_instance]].
