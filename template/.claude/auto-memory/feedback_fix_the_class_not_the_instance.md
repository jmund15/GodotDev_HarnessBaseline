---
name: feedback-fix-the-class-not-the-instance
description: "After fixing a defect, sweep its siblings before declaring done — the symptom names one instance, the cause names a class"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a0b867f5-6e84-40a0-a60c-e348977c3fd3
  modified: 2026-08-18T05:54:48.498Z
retire_when:
  - review-by: 2027-01-03
---

A reported symptom points at one instance. Fixing that instance and stopping ships the rest of
the class, and the next report is the same bug wearing a different file name.

**Why:** the reporter can only describe what they saw. The defect's actual boundary is the set of
places that share its cause — a value with the same provenance, a predicate at every call site, a
default across a family. That boundary is invisible from the symptom and cheap to enumerate from
the cause, so the sweep costs one grep and skipping it costs another whole round trip.

Measured in one session, three times:
- Floor surface HEIGHT derived from the floor; its EXTENT left as a constant three lines below in
  the same resource. Second playtest, same subsystem, same class.
- A stale singleton predicate replaced in each system's `Initialize` and left in each system's
  `_EnterTree` — two files, two survivors, found later by an audit.
- One behaviour-selecting export's wrong default corrected while a sibling export in the same
  config kept the same defect.

**How to apply:** when a fix lands, state the cause in one sentence, then grep for the cause rather
than the symptom — the old predicate, the sibling call site, the same-provenance constant, the other
members of the config — and either fix or explicitly record each hit. "The reported case is fixed"
is not a completion claim; "every site sharing this cause is fixed or listed" is. Related:
[[feedback_symmetric_guards_across_siblings]], [[gotcha_behavior_selecting_export_default_ships_inert]].
