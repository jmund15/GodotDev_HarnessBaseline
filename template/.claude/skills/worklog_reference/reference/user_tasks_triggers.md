# User-Tasks triggers — the signal catalog

Read from `SKILL.md` §User-Tasks when a candidate item may be owner-only. The threshold, the when-in-doubt rule and the `add` or `user-add` litmus stay in `SKILL.md`.

#### Production art / animation / shader / VFX
- **Agent says:** "needs visuals for X", "needs the X sprite", "needs the rise animation", "art pass on X", "shader work for X", "this scene needs proper visuals"
- **Owner says:** "I'll handle the visuals for X", "art is on me", "I'll make the sprite"
- **Litmus:** does the work need an art tool, an animation timeline or a shader graph? Yes → User-Tasks. A `feature` item whose output is production art is one, so `/worklog triage` proposes it for `to-user-tasks`.

#### Feel-driven tuning / playtest passes
- **Agent says:** "needs a playtest pass to dial in X", "calibrate by playing", "tune by feel", "values feel off but only you can tell"
- **Owner says:** "I'll playtest this and tune the numbers", "let me feel this out", "I need to play it"
- **Litmus:** is the missing input "what feels good" rather than "what is correct"? Yes → User-Tasks. A balance question with measurable criteria stays a `design` worklog item.

#### Open-ended brainstorms without a technical output
- **Agent says:** "brainstorm combinations of X and Y" when the output is a creative direction; "design the flavor of X"; "decide what the lore around X should be"
- **Owner says:** "I want to brainstorm X", "let me think on X combinations"
- **Litmus:** does brainstorming it with Claude produce a design Claude can implement? Yes → a `design` worklog item, scope 4, with a plan doc. No, the output is the owner's preference → User-Tasks.
- **Not a trigger:** "brainstorm a unified event schema" is technical; Claude takes it through `/plan`.

#### Design audits against the owner's vision
- **Agent says:** "the vision changed — the design docs need an audit pass", "the lore and system docs disagree on X — needs your reconciliation"
- **Owner says:** "audit my design docs for X", "reconcile my docs against the new vision"
- **Litmus:** is the missing input the owner's intent or taste? Yes → User-Tasks. An audit for inconsistencies stays a `docs` worklog item; an audit for vision alignment needs the owner's vision as input.

#### User-executed roadmap Parts (not User-Tasks)

A roadmap Part whose execution is the owner's (spatial design, manual content authoring, taste-driven tuning) but which has roadmap dependencies and dependents, such as "design 10–15 static prototype scenes". Route it to roadmap State `user-owned` (`_brainstorm_shared/roadmap_schema.md` §6.3) through `/update_roadmap`, never to `User-Tasks.md`: the Part stays on the roadmap to keep the dependency graph.

#### Anti-triggers (never User-Tasks)
- Technical work that touches art ("wire the visuals component to the new feature"): the art existing is a prerequisite, not the work.
- A bug fix that blocks feel tuning ("fix the shake parameter ignoring amplitude"): the fix is Claude's; only the tuning after it is the owner's.
- A brainstorm with a technical output ("brainstorm the provider-interface migration order"): Claude takes it through `/plan`.
