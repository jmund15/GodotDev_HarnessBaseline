# `[Tool]` Attribute Policy

Read from SKILL.md §`[Tool]` Attribute Policy when adding or reviewing a `[GlobalClass]` Resource, an `[Export]` of a Resource type, or an editor-time warning.

**Rule (chosen in the `Tool Attribute Audit` charter):** **Blanket `[Tool]` on every `[GlobalClass]` Resource; selective on Nodes.** A Node carries `[Tool]` only if it has editor-time code (`Engine.IsEditorHint`, `_ValidateProperty`, an editor plugin / `[ExportToolButton]`) **or** extends a framework convention Node type (Jmodot `State` / `BehaviorTask` / `BTState` / …). `[Tool]` is NOT required on every `[GlobalClass]`.

**The cascade rule (why):** if a `[Tool]` script has `[Export] TypedResource Foo` (or `Array<TypedResource>` / `Dictionary<_, TypedResource>`), then `TypedResource` AND every concrete subclass that can appear under that field MUST also be `[Tool]`. Otherwise the editor loads the instance as a bare `Godot.Resource` and the auto-generated setter throws `InvalidCastException` at load. Godot's C# source generator does NOT honor attribute inheritance — each concrete subclass needs its own `[Tool]`.

**What actually triggers it (verified empirically):** `[Tool]` gates whether a script's C# type is *instantiated in the editor at all*. A non-`[Tool]` Resource loads as a bare `Godot.Resource` in-editor **regardless of inline `[sub_resource]` vs external `[ext_resource]` reference** — so any `[Tool]` parent whose typed setter assigns it throws. External-ref does NOT avoid the cast (a tempting but false intuition). Only two things avoid it: the child being `[Tool]`, or the parent typing the field as base `Resource` (the escape hatch below). Caveat: `godot --headless --import` only fully deserializes `.tres` reachable from the import graph, so not every *latent* gap throws on import — but the ones that do are real, and a data edit can promote a latent gap to a live one at any time.

**Cost asymmetry — why blanket Resources but not Nodes:** `[Tool]` on a Resource is side-effect-free (no lifecycle; the editor only runs property setters). `[Tool]` on a Node makes the editor RUN its lifecycle (`_EnterTree`/`_Ready`/`_Process`) while a scene is open — unguarded game logic then fires in-editor (null-refs, churn, crashes). Blanket the free side (Resources), stay precise on the costly side (Nodes).

**Editor-only failure:** the cast fires in the EDITOR process; at runtime every script is its real type. **No GdUnit4 / runtime test can catch a cascade gap** — detection is static (the type graph) or headless-editor import.

**Escape hatch (typed-as-base):** type the `[Export]` as base `Resource`/`Node` and cast at runtime (`prop as ISomeInterface`). This breaks the cascade at the cost of Inspector drag-and-drop type hints. Static analysis cannot follow this path; the blanket-on-Resources policy and headless gate cover it.

**Jmodot is a black box to the consumer:** the framework blankets `[Tool]` across some families but not every Resource family. Jmodot-side gaps need a paired Jmodot-repo change, not a consumer edit. When a consumer `[Tool]` Resource must export a non-`[Tool]` Jmodot Resource, type the field as base `Resource` and cast at runtime; an external reference does not avoid the cast.

**Enforcement (three layers — the cascade is editor-only, so these replace the test that can't exist):**
- **Edit-time:** `pattern_enforcer.py` blocks writing a `[GlobalClass]` Resource without `[Tool]` and uses `tool_resource_classes.txt` to recognize indirect Resource bases.
- **Static gate:** `.claude/hooks/tool_cascade_audit.py` in `/regression_gate` builds the typed-`[Export]` graph, fails on any consumer `[GlobalClass]` Resource missing `[Tool]`, and emits `logs/tool_audit_inventory.md`. `apply_blanket_tool.py` fixes all flagged at once.
- **Headless gate:** `godot --headless --import` in `/regression_gate` (step 2b) — surfaces the actual `InvalidCastException`; catches Node, escape-hatch, and Jmodot-side gaps the static graph can't see.

**After any `[Tool]` edit, fully restart the editor** before concluding a gap is real — hot-reload can leave a stale BiMap script registration that MIMICS a cascade gap (`archive_godot_build_gotchas.md`, auto-memory).
