---
name: Logging Methodology
description: >-
  Auto-load for the producer-side contract on JmoLogger call sites — Info-vs-Debug level
  choice, the mandatory [Subsystem] prefix, [DIAG-<id>] composition — and when reviewing a
  `.cs` change that adds log calls. Pairs with `/analyze_godot_logs` (consumer-side). SKIP
  for `/analyze_godot_logs` invocation itself and `[DIAG-]` cleanup post-diagnosis (use
  debugging Phase 6).
user-invocable: false
---

# Logging Methodology

The producer-side contract for `JmoLogger` calls. Pairs with `/analyze_godot_logs` — the consumer relies on every convention in this doc to slice the corpus surgically.

> **Why this skill exists:** the level threshold and `--target` slicing can only be as good as the call sites. A corpus whose levels and tags are assigned by gut cannot be sliced by anything.

## Level Rules — assign by category, not by gut

| Level | Use for | Examples |
|---|---|---|
| **`Error`** | Invariant violation. Production-impossible state. | `[Controller] required component null after Initialize` |
| **`Warning`** | Recoverable issue. Designer/data oversight that has a defensible default. | `[Pool] cap exceeded, allocating fresh instance` |
| **`Info`** | State transitions. Discrete game events. Cross-system signals. **One Info ≈ one user-visible thing happened.** | `[HSM] AgentSM Idle→Active`, `[Match] round 3 started`, `[Inventory] operation rejected — capacity reached` |
| **`Debug`** | Decision branches inside a system. Numeric tunables. Per-collision / per-event detail. **Off by default** — emitted only when `JmoLogger.MinimumLevel` reaches `Debug`. | `[Decision] no valid targets`, `[Collision] periodic prune: removed 4 dead refs`, `[Pool] request hit, reused id=17` |

**Every level is silenceable.** `JmoLogger.MinimumLevel` (project setting `debug/jmodot/minimum_log_level`, default `Info`) is the emission threshold; only `Error` is ungated. `DebugEnabled` still works as a derived accessor. Two consequences for call-site choice:

- **`Info` is not free.** It is what a normal play session pays for, so the Info/Debug litmus below is the one that governs runtime cost — not the debug toggle.
- **A `Debug` call site costs nothing at the default threshold.** Diagnostics no longer have to be deleted to stop being load; demoting is a legitimate terminal state for instrumentation worth keeping. What must never ship is a diagnostic at `Info`, which the debug toggle cannot reach and no default silences.

**Hard rule on `Error`:** `JmoLogger.Error(...)` fails GdUnit4 tests at the call site, before any assertion (see `archive_jmologger_gotcha.md` in auto-memory). Two consequences:

- Logic-Domain tests cannot cover Error-path guards — those are integration/playtest verified only.
- Never demote a Warning to Error to "make it louder." That turns every test exercising the path into a failure.

**Litmus when unsure Info vs Debug:** *"Will this still be useful in 50 plays at the current frequency, or will it become wallpaper?"* Wallpaper → Debug. Signal → Info.

## Mandatory `[Subsystem]` prefix

Every `Info` and `Debug` call site MUST begin with a bracketed subsystem tag:

```csharp
JmoLogger.Info(this, $"[Inventory] operation rejected — capacity reached");
JmoLogger.Debug(this, $"[Collision] sibling registered: {newBody.Name} (count={_active.Count})");
```

Why mandatory: `/analyze_godot_logs --target Inventory` and `--mode tags` both rely on the analyzer's `\[(\w+)\]` regex. Untagged calls are invisible to subsystem slicing and pollute the summary mode.

### Project-owned tags

Keep the canonical tag list with the project's subsystem registry or logging configuration; declare that owner in `skills/project_subsystems/SKILL.md`. Use one stable tag per subsystem, such as `[Inventory]`, `[Movement]`, `[Pool]`, or `[Match]`. Add a new tag to the project-owned list in the same commit.

Tags are flat (`[Inventory]`), never qualified (`[Inventory.Actions]`, `[Inventory:Add]`). The analyzer matches `\[(\w+)\]`; a `.` or `:` inside the brackets makes the line invisible to `--target` and `--mode tags`. Put the qualifier in a field: `[Inventory] action=Add`.

### Typo discipline

Subsystem tags are **magic strings**, not constants. Constants would force {{PROJECT_NAME}} taxonomy into Jmodot (violates the framework boundary rule in `jmodot_framework_boundary_rule.md`). Typo drift instead self-reports: `/analyze_godot_logs --mode tags` after any play session shows the histogram; a `[Colision]` outlier surfaces on first run and gets fixed at the call site.

### Exception — project-owned constants for cross-cutting measurement tags

A project may keep constants for product-hypothesis tags when the same string must be referenced from unrelated call sites and external log-mining scripts. Declare the constants file in `skills/project_subsystems/SKILL.md`; the baseline does not prescribe its namespace or path. Examples: `[OnboardingComplete]`, `[FeatureUsed]`, `[ModeChanged]`.

Use the constants at call sites: `JmoLogger.Info(this, $"{InstrumentationTags.FeatureUsed} feature={featureId}");`

Subsystem/debug tags do not belong in this constants class. The litmus: *"Is this tag measuring one hypothesis across sessions, or identifying which subsystem emitted the log?"* Hypothesis → constant; subsystem → magic string.

Note for analyzer-side reasoning: calls using `$"{InstrumentationTags.X} ..."` look untagged to a naïve regex (first char after `"` is `{`, not `[`) but ARE compliant — the constant value embeds the bracket. The `check_logger_tag_prefix.py` hook recognizes this pattern.

## `[DIAG-<id>]` discipline (active diagnosis only)

When using `JmoLogger.Debug` for short-lived diagnostic instrumentation during a debugging session (Phase 4 of `Debugging` skill), compose the diagnostic tag **after** the subsystem tag:

```csharp
JmoLogger.Debug(this, $"[Inventory][DIAG-a4f2] state={state} item={item?.Name ?? "null"}");
```

**Composition rule:** `[Subsystem][DIAG-<id>]`, never `[DIAG-<id>]` alone. Without the subsystem prefix, `--target Inventory` skips the diagnostic log.

Pick four random hex chars for `<id>` per debugging session. Single grep `[DIAG-` removes all of a session's instrumentation at Phase 6 cleanup.

**Raise the threshold before instrumenting.** Set `JmoLogger.MinimumLevel` to `Debug` (project setting `debug/jmodot/minimum_log_level`, or `JmoLogger.DebugEnabled = true`); at the default threshold a diagnostic emits nothing and the path reads as never executed. Restore the setting at Phase 6 cleanup.

**Cleanup is owed.** See `archive_diagnostic_log_cleanup_discipline.md` (auto-memory) for the worklog-item rule: any `[DIAG-]` log without a same-session removal commit needs a worklog item tracking it, or the noise calcifies. `[DEBUG-]` is reserved — would collide with `JmoLogger.Debug` itself when grepping.

**Sibling tag — `[PROTO-<slug>]`.** Same family, same composition rule (`[Subsystem][PROTO-<slug>]`), same single-grep cleanup (`[PROTO-`). It marks the agent-readable channel of a `prototype` skill run; `<slug>` is the prototype's directory name under `prototypes/`, not a random id, so the tag survives across sessions on that prototype's branch. No cleanup is owed on `main` — prototype code never lands there (`prototype` skill §Containment); cleanup applies only if a `[PROTO-]` line reaches production code during promotion.

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
2. **State dumps** — `Info("current health is 30")` when health has not changed. Log the transition (`[Health] {target} health 35 → 30 from {source}`), not the current value.
3. **Narrative / developer talk** — `Info("checking branch A because foo")`. Either it is a decision point (→ Debug with `[Subsystem]`) or noise (delete).
4. **Unprefixed Info** — invisible to subsystem filters.
5. **Info catch-all for what should be Debug** — repeated operation detail belongs at Debug. Keep Info for state transitions and user-visible events.
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
/analyze_godot_logs --target Inventory --mode timeline --last 50
/analyze_godot_logs --node AgentA --mode entity
```

Don't flip Debug back off — you'll lose the diagnostic detail you just enabled. The analyzer's job is to slice; let it.

### "Debug is OFF and the log is STILL flooded"

The toggle only ever governed `Debug`. A flood with debug off is `Info` call sites, and no amount of
toggling reaches them — check the level assignment at the noisiest sites, not the flag.

```
/analyze_godot_logs --mode tags        # which subsystem owns the volume
/analyze_godot_logs --level info       # then fix the call sites, or drop MinimumLevel to Warning
```

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
