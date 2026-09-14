---
name: arch_rule_event_not_magnitude_gates_behavior
description: "A behavior that responds to an EVENT must be gated on the event, never on a magnitude the event happens to produce — otherwise the tuning dial for how-much secretly doubles as an on/off switch for whether-at-all, and crossing the line is silent."
metadata:
  node_type: memory
  type: project
  tier: hot
---

When a behavior is the response to something *happening*, gate its entry on the happening. Gating it
on a magnitude that thing produces fuses two independent questions — **whether** the behavior runs and
**how much** of it runs — into one number, and the fusion is invisible until someone tunes past the
threshold.

**Why it hides.** The magnitude is usually a feel value, so it gets retuned casually and often. Nothing
reports the crossing: the event still fires, the impulse still applies, no error is logged, and the
behavior simply stops existing. It reads as "that feature never worked" or "that state is flaky", which
sends the next session hunting in the state machine rather than in a `.tres` number.

**Litmus at authoring time:** *is there a value of this tuning knob that makes the behavior not happen
at all?* Yes → the gate is on the wrong fact. Split it: one condition reads the event and decides
whether; the magnitude scales the response and decides how far/how hard/how long.

**Worked case (2026-08-16).** A rider shed off a host entered its launch/recovery state only when the
resulting knockback cleared a force threshold. A tuning pass dropped the fling scale below it, so shed
entities were *dropped instead of thrown* and the whole flung-then-recovers behavior silently no-oped —
while every unit of the shed pipeline still worked. Fixed by giving the shed its own event-reading
condition beside the magnitude-gated one, both targeting the same state, so force decides only distance.

**Corollary — among urgent transitions, order IS the gate.** Adding the event route is not enough if a
broader magnitude-driven transition sits earlier in the array and matches the same frame; it wins and
the specific route never fires. Rank the specific cause above the generic responder, and keep the
truly-overriding ones (death, freeze) above both. A transition that is present but out-ranked is
indistinguishable from one that was never added.

Related: [[arch_rule_graded_scale_over_binary_gate]], [[arch_rule_hysteresis_for_analog_hsm_boundaries]].
