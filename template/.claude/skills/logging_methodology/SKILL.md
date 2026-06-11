---
name: Logging Methodology
description: >-
  Producer-side contract for JmoLogger call sites (Info/Debug levels, mandatory [Subsystem]
  prefix, [DIAG-<id>] composition). Pairs with /analyze_godot_logs consumer. Triggers:
  "JmoLogger", "log prefix", "Info vs Debug", "[Tag]", "log methodology", "log discipline",
  "log call site", "analyze_godot_logs producer". Also fires when reviewing a `.cs` change
  that adds log calls. SKIP for `/analyze_godot_logs` invocation (consumer-side) and
  `[DIAG-]` cleanup post-diagnosis (use debugging Phase 6).
user-invocable: false
---

# Logging Methodology

The producer-side contract for `JmoLogger` calls. Pairs with `/analyze_godot_logs` — the consumer relies on every convention in this doc to slice the corpus surgically.

> **Why this skill exists:** logs were drifting in two directions at once — `DebugEnabled=false` left the agent under-informed, `DebugEnabled=true` flooded context. Audit (2026-05-11) found Debug at 7% of calls and `[Tag]` prefix discipline at ~55% on Info. The fix is producer-side: a stable, filterable corpus that lets `/analyze_godot_logs --target [Tag]` do the slicing, instead of the binary toggle doing it.

## Level Rules — assign by category, not by gut

| Level | Use for | Examples |
|---|---|---|
| **`Error`** | Invariant violation. Production-impossible state. | `[Caster] holder component null after Initialize` |
| **`Warning`** | Recoverable issue. Designer/data oversight that has a defensible default. | `[Pool] cap exceeded, allocating fresh instance` |
| **`Info`** | State transitions. Discrete game events. Cross-system signals. **One Info ≈ one user-visible thing happened.** | `[HSM] WizardSM Idle→Casting`, `[Match] wave 3 started`, `[Crafter] cast fizzled — mana depleted` |
| **`Debug`** | Decision branches inside a system. Numeric tunables. Per-collision / per-event detail. **Disabled by default** — gated by `debug/jmodot/debug_logging_enabled`. | `[EscapeCheck] no threats in perception → ESCAPE`, `[Collision] periodic prune: removed 4 dead refs`, `[Pool] request hit, reused id=17` |

**Hard rule on `Error`:** `JmoLogger.Error(...)` fails GdUnit4 tests at the call site, before any assertion (see `archive_jmologger_gotcha.md` in auto-memory). Two consequences:

- Logic-Domain tests cannot cover Error-path guards — those are integration/playtest verified only.
- Never demote a Warning to Error to "make it louder." That turns every test exercising the path into a failure.

**Litmus when unsure Info vs Debug:** *"Will this still be useful in 50 plays at the current frequency, or will it become wallpaper?"* Wallpaper → Debug. Signal → Info.

## Mandatory `[Subsystem]` prefix

Every `Info` and `Debug` call site MUST begin with a bracketed subsystem tag:

```csharp
JmoLogger.Info(this, $"[Crafter] cast fizzled — mana depleted at release");
JmoLogger.Debug(this, $"[Collision] sibling registered: {newBody.Name} (count={_active.Count})");
```

Why mandatory: `/analyze_godot_logs --target Crafter` and `--mode tags` both rely on the analyzer's `\[(\w+)\]` regex. Untagged calls are invisible to subsystem slicing and pollute the summary mode.

### Canonical tags

Use these. Don't invent new ones unless the subsystem genuinely has no fit — and if you do, append it to this list in the same commit.

**Combat & spells:** `[Crafter]` `[Pool]` `[Spawn]` `[Collision]` `[Cast]` `[Reaction]` `[Spell]` `[Impact]` `[SpawnEffect]` `[Status]`
**State machines:** `[HSM]` `[BT]` `[BasicRespawnState]` `[KOState]` `[ChargeState]` (state-named tags acceptable when the state IS the subsystem)
**AI / perception:** `[Perception]` `[EscapeCheck]` `[Steering]` `[Navigator]` `[Critter]`
**Game flow:** `[Match]` `[Wave]` `[Wizard]` `[Player]` `[Registry]`
**Systems:** `[Inventory]` `[Crafting]` `[Throw]` `[Holder]`

Tags are flat (`[Spell]`), not nested (`[Spell.Crafter]`) — the analyzer's regex captures only the first word-token, so a nested tag would parse as `[Spell]` and lose its suffix.

### Typo discipline

Subsystem tags are **magic strings**, not constants. Constants would force PP taxonomy into Jmodot (violates the framework boundary rule in `jmodot_framework_boundary_rule.md`). Typo drift instead self-reports: `/analyze_godot_logs --mode tags` after any play session shows the histogram; a `[Colision]` outlier surfaces on first run and gets fixed at the call site.

### Exception — `InstrumentationTags` for cross-cutting hypothesis tags

`{{PROJECT_NAME}}.Global.InstrumentationTags` (`Global/InstrumentationTags.cs`) IS a constants class — but its scope is deliberately narrow. It holds tags for **MVP hypothesis tracking** (H2 craft completion, H3 spell-cast volume, H4 recipe switching, H5 wizard hit-while-wheel-open) where the same string must be referenced from multiple unrelated sites AND from external log-mining scripts that compare across playtests. Examples: `[Craft]`, `[Cast]`, `[RecipeSwitch]`, `[Hit]`.

Use the constants at call sites: `JmoLogger.Info(this, $"{InstrumentationTags.Hit} damage={dmg}, hp={hp}");`

This file's own docstring is the governance rule: **subsystem/debug tags do NOT belong in `InstrumentationTags`.** New hypothesis tags get a constant; new subsystem tags stay as magic strings. The litmus: *"Is this tag tracking a measurable hypothesis across playtests, or is it identifying which subsystem emitted the log?"* Hypothesis → constant; subsystem → magic string.

Note for analyzer-side reasoning: calls using `$"{InstrumentationTags.X} ..."` look untagged to a naïve regex (first char after `"` is `{`, not `[`) but ARE compliant — the constant value embeds the bracket. The `check_logger_tag_prefix.py` hook recognizes this pattern.

## `[DIAG-<id>]` discipline (active diagnosis only)

When using `JmoLogger.Debug` for short-lived diagnostic instrumentation during a debugging session (Phase 4 of `Debugging` skill), compose the diagnostic tag **after** the subsystem tag:

```csharp
JmoLogger.Debug(this, $"[Spell][DIAG-a4f2] cast state={state} target={target?.Name ?? "null"}");
```

**Composition rule:** `[Subsystem][DIAG-<id>]`, never `[DIAG-<id>]` alone. Without the subsystem prefix, `--target Spell` skips the diag log, defeating the filter that gets you to the right slice.

Pick four random hex chars for `<id>` per debugging session. Single grep `[DIAG-` removes all of a session's instrumentation at Phase 6 cleanup.

**Cleanup is owed.** See `archive_diagnostic_log_cleanup_discipline.md` (auto-memory) for the worklog-item rule: any `[DIAG-]` log without a same-session removal commit needs a worklog item tracking it, or the noise calcifies. `[DEBUG-]` is reserved — would collide with `JmoLogger.Debug` itself when grepping.

## Producer↔Consumer pairing

What the `/analyze_godot_logs` flag does depends on what the call site emits.

| `/analyze_godot_logs` flag | Producer convention it relies on | Failure mode if convention violated |
|---|---|---|
| `--target <Tag>` | Every Info/Debug carries `[<Subsystem>]` prefix | Untagged calls invisible to the filter |
| `--target-any A,B,C` | Same as above | Same |
| `--node <NodeName>` | `JmoLogger.X(this, ...)` or explicit `Node? owner` arg | Calls with `object` context only (not Node) won't carry node path |
| `--level <L>` | Level-rules table above | Demoted/promoted logs land in the wrong bucket |
| `--mode tags` | Tag prefix on every Info/Debug | Histogram undercounts; can't see subsystem distribution |
| `--mode entity` | Node `owner` parameter passed | Falls back on file path; loses cross-file entity continuity |
| `--mode timeline` (with filter) | All above | Timeline gaps where convention is broken |

## Anti-patterns

1. **Every-frame logs** — never inside `_Process`, `_PhysicsProcess`, `Tick`, or update loops. Even Debug. Move to a transition edge or a periodic-sampling guard.
2. **State dumps** — `Info("current health is 30")` when health hasn't changed. Log the *transition* (`[Combat] {target} health 35 → 30 from {source}`), not the current value.
3. **Narrative / developer talk** — `Info("checking branch A because foo")`. Either it's a decision point (→ Debug with `[Subsystem]`) or it's noise (delete).
4. **Unprefixed Info** — discovered as ~45% of the corpus at audit time. The single biggest source of analyzer blind spots.
5. **Info catch-all for what should be Debug** — top audit offenders were `SpellCrafter` (28), `SpellPoolManager` (19), `MatchController` (17) emitting per-cast / per-pool-op narrative at Info. Demote to Debug; let `DebugEnabled=true` + `--target [Crafter]` retrieve when needed.
6. **Demoting Warning to Error to be louder** — fails tests. See `archive_jmologger_gotcha.md`.

## Workflow recipes

### "After manual playtest, what happened?"

```
/analyze_godot_logs                    # default --mode summary first
/analyze_godot_logs --mode tags        # see what subsystems were active
/analyze_godot_logs --target <Tag>     # drill into the one that matters
```

If summary is empty of the system you care about, the producer side is missing logs at the boundary — author them per Level Rules.

### "Debug=ON is drowning me"

Keep the toggle on. Narrow via the analyzer:

```
/analyze_godot_logs --target Spell --mode timeline --last 50
/analyze_godot_logs --node WizardA --mode entity
```

Don't flip Debug back off — you'll lose the diagnostic detail you just enabled. The analyzer's job is to slice; let it.

### "Debug=OFF is silent"

```
/analyze_godot_logs --mode tags        # first: see what corpus exists
/analyze_godot_logs --target <Tag>     # second: drill in
```

If `--mode tags` shows the subsystem you care about with zero hits, two possibilities — (a) producer side is missing log calls at the boundary; (b) the subsystem genuinely didn't run. Re-read the consumer-side rule `archive_debugging_discipline.md` (auto-memory) — *absence of evidence ≠ evidence of absence*. Verify the session exercised the feature before declaring it broken.

If (a), author the missing logs at the state boundary per Level Rules and re-run. If still silent under Debug, flip `DebugEnabled=true` for the next session — you've graduated from "what happened" to "why did the decision go that way" and need the Debug channel.

## When this skill loads

Auto-load on the triggers in the frontmatter description. Skip for:

- `/analyze_godot_logs` invocations (consumer-side; the command itself owns its inference rules).
- Phase 6 cleanup of `[DIAG-]` instrumentation (covered by `Debugging` Phase 6 + `archive_diagnostic_log_cleanup_discipline.md`).
- Pure log-reading tasks where no new instrumentation is being authored (load `Debugging` skill instead for the read-side discipline).

## Cross-references

- **`Debugging` skill** — Phase 4 references this skill for `[DIAG-<id>]` composition; Phase 6 references `archive_diagnostic_log_cleanup_discipline.md` (auto-memory) for cleanup tracking.
- **`archive_jmologger_gotcha.md` (auto-memory)** — Error-fails-test semantics; namespace clarification.
- **`archive_debugging_discipline.md` (auto-memory)** — consumer-side reading rules (verify-don't-speculate, absence ≠ negation, `/analyze_godot_logs` as distrust signal).
- **`archive_diagnostic_log_cleanup_discipline.md` (auto-memory)** — worklog enforcement for `[DIAG-]` removal.
- **`.claude/commands/analyze_godot_logs.md`** — consumer side. The flag table above mirrors its surface; if the command grows new flags, update the pairing table here.
- **`.claude/hooks/check_logger_tag_prefix.py`** — soft warning on Edit/Write that adds an untagged `JmoLogger.Info`/`Debug`. Soft, not blocking — false positives on multi-line / interpolated calls are tolerated.
