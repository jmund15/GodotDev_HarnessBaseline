---
name: Hysteresis at .tres layer for analog-input HSM boundaries
description: When an HSM transition pair is gated by a single analog signal crossing a threshold, asymmetric thresholds (deadband) at the .tres layer prevent noise-driven state flicker.
type: feedback
originSessionId: 8892bf33-068a-4265-ad0e-ad954ac0de36
---
When two HSM states are gated by a single analog signal (movement intent magnitude, charge progress, velocity band, perception confidence) crossing a threshold, the bidirectional transition pair MUST have asymmetric thresholds at the .tres layer — never a single shared threshold value.

**Why:** A single-threshold check produces frame-to-frame state churn when the signal hovers near the boundary (analog stick noise ±0.02 around 0.9 → 30-frame Walk↔Run oscillation). The HSM ping-pongs every other frame; ActiveStatContext push/pop fires repeatedly; animation orchestrator restarts loops; downstream observers (UI, VFX, BB key consumers) see flapping state. None of these are bugs the consumers can be expected to absorb — the gate is wrong.

**How to apply:** Author asymmetric upgrade vs downgrade thresholds in the per-direction `.tres` files (one transition's Min ≠ the reverse transition's Max). Initial deadband: ~10 % of the upgrade threshold. Example for Walk↔Run keyed near 0.9:
- Walk → Run upgrades at magnitude ≥ 0.9 (inclusive Min)
- Run → Walk downgrades at magnitude < 0.8 (exclusive Max) — 0.1 deadband

For Idle↔Walk, a smaller deadband (e.g., 0.05) keeps a "wakes up easily, stays awake easily" feel without sub-perceptual churn. Tighten if "sticky"; widen if "flickery." The choice belongs in the `.tres` so designers tune without code changes.

**Litmus:** *"Could a noise of ±2 % on the analog input cause this transition pair to fire repeatedly within 1 second?"* Yes → asymmetric thresholds needed.

**Distinct from** `feedback_no_magnitude_as_type_discriminator.md`: that rule rejects magnitude-as-semantic-category-discriminator (mouse vs stick). This rule covers magnitude-as-genuine-signal-strength with single-threshold gating between two valid states.

Witnessed 2026-05-13 during Wizard locomotion HSM refactor — /plan_check flagged the original 0.9 single-threshold design (Walk↔Run boundary) as ASK-tier improvement. Author Walk→Run at 0.9 and Run→Walk at 0.8 in two separate `InputIntentVector2MagnitudeRangeCondition` `.tres` files.
