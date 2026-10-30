# User-Tasks triggers — the signal catalog

Read from SKILL.md §User-Tasks triggers when a candidate item may be user-only. Moved verbatim from `SKILL.md`; the threshold, the propose-line and the when-in-doubt rules stay there.

#### Production art / animation / shader / VFX
- **My-side:** "needs visuals for X", "needs the X sprite", "needs the rise animation", "art pass on X", "shader work for X", "this scene needs proper visuals"
- **Your-side:** "I'll handle the visuals for X", "art is on me", "I'll make the sprite"
- **Litmus:** does the work require an art tool / animation timeline / shader graph? Yes → User-Tasks.
- **Triage:** a `vfx`-domain `feature` item is production art, so `/worklog triage` proposes it for `to-user-tasks`.

#### Feel-driven tuning / playtest passes
- **My-side:** "needs a playtest pass to dial in X", "calibrate by playing", "tune by feel", "values feel off but only you can tell"
- **Your-side:** "I'll playtest this and tune the numbers", "let me feel this out", "I need to play it"
- **Litmus:** is the missing input "what feels good" rather than "what is correct"? Yes → User-Tasks. (Note: "deterministic stat balance" with measurable criteria stays a `design` worklog item, not User-Tasks.)

#### Open-ended brainstorms without a Claude-anchor
- **My-side:** "brainstorm synergies between X and Y" *when* the output is a creative direction, not technical alternatives; "design the flavor of X", "decide what the lore around X should be"
- **Your-side:** "I want to brainstorm X", "let me think on X synergies"
- **Litmus:** does "brainstorming this with Claude" produce a design Claude can implement? Yes → `design` worklog item, scope 4, paired Plan doc. No (output is *your* preference, not technical) → User-Tasks.
- **Anti-trigger:** "brainstorm a unified status-effect blackboard schema" is technical — Claude can run `architecture_brainstorm` and produce a design doc. → `design` worklog, NOT User-Tasks.

#### Cross-doc design audits driven by user vision
- **My-side:** "your game vision changed — your design docs need an audit pass", "the lore and spell docs disagree on X — needs your reconciliation"
- **Your-side:** "audit my design docs for X", "reconcile my docs against the new vision"
- **Litmus:** is the missing input *your* intent / *your* taste? Yes → User-Tasks. (Claude can audit *for inconsistencies* — that stays `docs` worklog. But audit *for vision-alignment* requires the user's vision as input.)

#### User-executed Parts (roadmap-resident, distinct from User-Tasks)

Parts on a brainstorm `roadmap.md` whose execution is inherently user-domain (spatial design, manual content authoring, taste-driven tuning) but carry roadmap deps and dependents. Examples: "design 10–15 static prototype floor scenes." Route to roadmap State=`user-owned` (see `_brainstorm_shared/roadmap_schema.md §6.3`), NOT `User-Tasks.md` — the Part must stay on the roadmap to preserve the dependency graph.

#### Anti-triggers (do NOT route to User-Tasks on these)
- Technical tasks that *touch* art (e.g., "wire the visuals component to the new spell") — Claude can do this; the art existing is a prerequisite, not the work.
- Bug fixes that *block* feel-tuning (e.g., "fix the shake parameter not respecting amplitude") — the fix is Claude's; only the post-fix tuning is yours.
- Brainstorms with defined technical output ("brainstorm the IBlackboardProvider migration order") — Claude tackles those via `architecture_brainstorm`.
