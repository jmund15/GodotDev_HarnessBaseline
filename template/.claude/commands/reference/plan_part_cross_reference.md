# Phase 3 — Cross-reference against codebase

For every concrete claim in the loaded design surface, verify against current code. **Use the right tool per claim shape** (per CLAUDE.md §9):

| Design claim | Verification |
|---|---|
| `extends <Base>` / `implements <Iface>` | LSP `findReferences` on `<Base>`/`<Iface>` → confirm class exists AND has 2+ subclasses/impls if design called it a family extension |
| New file path `Foo/Bar/Baz.cs` | `Glob("Foo/Bar/Baz.cs")` → confirm no existing file at that path |
| Named existing file `Foo/Bar/Existing.cs` | `Glob` → confirm still exists at named path |
| Named method `ExistingClass.MethodName(...)` | LSP `documentSymbol` on the file → confirm method exists with assumed signature |
| **`<Type>.Instance.<Member>(...)` autoload call** | Enumerate EVERY `*.Instance` call in design pseudocode (don't skip any). For each `<Type>`: `Grep` `project.godot` `[autoload]` block for `<Type>=`. **If absent, rule out the scene-nested-singleton pattern BEFORE declaring drift** — a singleton's `.Instance` is frequently set by a node *inside* an autoload scene (e.g. `InputProfileDatabase` is a child of `global.tscn`, itself the `Global` autoload), so it never appears as a top-level `[autoload]` line yet is populated at boot. Two checks: (a) grep the autoload `.tscn` scenes (esp. `global.tscn`) for a node instancing `<Type>`, AND (b) grep for `Instance = this` in `<Type>`'s `_EnterTree`/`_Ready` and confirm that node is reachable from an autoload. Only if BOTH miss is it genuine autoload-missing drift (executor hits `JmoLogger.Error` at runtime = test failure) → resolve at plan-time per `feedback_resolve_questions_in_plan_not_execution`: add `<Type>` to `[autoload]` (publisher-before-consumer order per `gotcha_autoload_to_autoload_subscription_order`) OR pivot to a non-autoload access pattern. Do NOT defer to write-time as a "verify at code time" caveat, and do NOT add a redundant top-level autoload for a type already wired via a scene child. |
| Integration touch point in subsystem `<X>` | Read `project_subsystems` registry entry for `<X>` → confirm subsystem still exists and the named seam is reachable |
| New `BBDataSig.<Key>` | `Grep("public static readonly StringName <Key>")` on Jmodot's BBDataSig file → confirm key doesn't already exist (collision) AND that the namespace expects it |
| Test-suite path `Tests/Logic/<X>Test.cs` | `Glob` → confirm test folder exists (creation of the file itself is plan-session work) |

**Drift classification — three tiers:**

| Macro drift (→ arch-rework, HARD-STOP) | Micro drift (→ surface as briefing item for the drafting agent to resolve) | Glossary note (→ briefing footnote, NOT drift) |
|---|---|---|
| Named base class / interface no longer exists | Named file moved to a sibling folder; type intact | Informal naming in design pseudocode where the actual codebase entity differs in name but not shape (e.g., "Player.tscn" → real entity is `[Export] PackedScene _playerPrefab`) |
| Extended-family inventory collapsed below 2 subclasses (extend-not-parallel premise broken) | Named method gained/lost a parameter; intent preserved | Sibling-pattern reference where the design says "analogous to X" but X doesn't share the same contract (shape analogy only, not contract conformity) |
| New-file path collides with non-trivial existing content (parallel work happened) | Test fixture base class was renamed but extension surface is the same | Design-doc shorthand for a multi-step process the codebase formalizes via discrete API (e.g., "LoadAsync" → `RequestLoad` / `GetLoadStatus` / `CompleteLoad`) |
| Named subsystem in Integration touch points was removed / merged into another | Adjacent file added/removed in a touch-point subsystem (additive context) | Design-pseudocode method names that the executor will translate to canonical API at write-time |
| Base class became `sealed`, or `abstract` → concrete, or constructor reshape that invalidates the extension | New `BBDataSig` key exists adjacent to the planned one (additive) | |
| Jmodot ↔ {{PROJECT_NAME}} boundary the design assumed was re-drawn | Comment / whitespace / formatting drift in linked design-doc section | |
| Source-cell wikilink anchor no longer resolves in the design doc | An extra subclass exists in the family beyond the design's enumeration | |

**Rule of thumb (litmus when in doubt):**
- *"Could the drafting agent address this in plan-drafting without changing the design's load-bearing claims?"* Yes → micro. No → macro.
- *"Is the divergence purely a naming/shorthand issue with the executor writing the canonical name at code-time?"* Yes → glossary note, NOT drift. Glossary notes go in the briefing's footer ("Design-doc glossary translations") rather than the drift count, to avoid diluting the actionable micro-drift signal.

**Drift count discipline:** the briefing's `Drift: macro=N | micro=M` header counts ONLY drift items, not glossary notes. A briefing with 4 micro + 2 glossary reads as `macro=0 | micro=4`. Glossary notes are listed below the verification block under a `─── Design-Doc Glossary ───` section so the drafting agent has the translations without inflating the action count.

**Scope-inflation advisory (distinct from drift).** While running the verification above, capture cheap size proxies for any named *modify* / *migrate* / *refactor* target — reuse the SAME `findReferences` / `documentSymbol` / `Glob` calls already issued; do NOT dispatch agents or widen the search (open-ended blast-radius discovery belongs to [`/explore`](../explore.md), not this command). Two proxies: (a) `findReferences` count + distinct-file count for a type the Part migrates/refactors; (b) current LOC for a file the design treats as a light edit. When a proxy materially exceeds what the design's prose implies (e.g. a "migrate existing X" one-liner against a type with 20+ call sites across 6+ files), record a **scope signal**. This catches *post-authoring scope inflation* — a Part bounded at arch Step 5 that grew via later code drift — which neither arch Step 5 (too early) nor the plan's file-list gate (catches *unbounded*, not *bounded-but-large*) covers. The signal is **advisory, NOT drift**: it does not gate, does not kick to `arch-rework`, and is NOT counted in the `macro=N | micro=M` header. The proxy count is evidence for a split *recommendation* the user weighs — never a verdict (a 30-reference type may be a mechanical rename; a 3-reference one a deep rework). The authoritative bound check remains the plan's file-list bound gate (`/part_drive` valve (b)).
