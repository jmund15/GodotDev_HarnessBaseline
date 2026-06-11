---
disable-model-invocation: true
---

Analyze Godot log files with mode-based presets and token-efficient JSON output.

## Quick Reference

| User asks | Invoke | Typical JSON size |
|---|---|---:|
| "Health check" / no specifics | `--json --mode summary` (default) | ~200 tok |
| "Just the errors" | `--json --mode errors` | <500 tok |
| "What just happened?" | `--json --mode tail --last 20` | ~3K tok |
| "[Tag] frequency" | `--json --mode tags` | ~150 tok |
| "Everything HoarderCritter did" | `--json --mode entity --node HoarderCritter` | varies |
| "Targeted timeline" | `--json --target HSM --target Transition` | varies |
| "Previous game session" | add `--log previous` to any | varies |

**Always use `--json` for agent consumption.**

---

## Natural-Language Inference (READ THIS FIRST)

**Users will rarely pass flags.** Most invocations look like:

- `/analyze-logs` (no args)
- "check the logs"
- "what just happened?"
- "the game crashed when I cast Rock Pillar"
- "any HSM issues?"
- "look at the warnings"

You must infer the correct invocation. Default to the SAFEST interpretation, surface what you inferred in your response, and offer to drill in. **Do not ask clarifying questions unless the input is genuinely ambiguous AND a wrong default would mislead.**

### Inference decision flow

Apply in order, stop at first match:

1. **Crash/freeze/quit mentioned?** → add `--log previous` (the active `godot.log` was truncated by re-launch; the timestamped historical log holds the crash state). Examples: "crashed", "froze", "had to quit", "Alt-F4'd", "the app stopped responding".

2. **Specific system / entity / domain mentioned?** → use the Domain Mapping table below for `--target`/`--node`. Examples: "HSM", "the hoarder", "spell spawning", "navigation".

3. **Temporal/recency cue?** → `--mode tail --last 20`. Examples: "what just happened", "last few seconds", "right before X", "after I pressed Y".

4. **Error/break framing?** → `--mode errors`. Examples: "what's broken", "errors only", "anything failing", "what crashed", "exceptions".

5. **Generic/vague "check the logs"?** → `--mode summary` (default). It's the cheapest (~200 tok) AND the most informative for "I don't know what to look for".

6. **No prior signal AND user just typed `/analyze-logs` bare?** → `--mode summary`. Same reason: cheapest first pass; if it surfaces issues, propose drilling in next turn.

### Combine signals greedily

Multiple signals stack. Examples:

| User says | Inferred invocation |
|---|---|
| "the hoarder crashed during pathfinding" | `--log previous --mode errors --node HoarderCritter --target Navigator` |
| "what HSM transitions just happened?" | `--mode tail --last 30 --target HSM Transition` |
| "any spell pool errors recently?" | `--mode errors --target Pool` |
| "check the logs after the rock pillar test" | `--log previous --mode summary` (or `--target Rock Pillar` if recent discussion was about it) |
| "what's going on?" | `--mode summary` (no other signal — cheapest first pass) |
| "warnings about navigation" | `--target Navigator --level warning` |

### Session-context cues (use what you already know)

If the conversation has been about a specific topic for the last few turns, **assume that topic is the implicit subject** even if not restated. Examples:

- Last 5 turns discussed HoarderCritter perception → "check the logs" → `--target Perception --node HoarderCritter`
- User just edited spell-spawning code → "look at the logs" → `--target Spawn`
- User just ran `--target HSM` and got results → "now check warnings" → `--target HSM --level warning` (preserve the prior filter)

**Surface inferred context in your response**: *"Inferring from earlier discussion you want HoarderCritter perception — running `--target Perception --node HoarderCritter`. If that's not right, say what to filter on instead."*

### When to ask vs when to default

- **Default silently** when one signal is clearly present (crash, named system, error framing).
- **Default with context callout** when you're inferring from session context — give the user one line saying what you assumed.
- **Ask ONE clarifying question** only if: (a) the input is "the logs" with zero other signal AND (b) recent session context could plausibly point to 2+ very different targets. Even then, propose a default and ask "or do you want X instead?"
- **Never** ask "which mode do you want?" — modes are an implementation detail. Ask in user-language: "is this about a crash, recent activity, or a specific system?"

### Token-budget escalation pattern

For open-ended queries, prefer the **cheap-first / drill-in** pattern over going deep immediately:

1. Start with `--mode summary` (~200 tok, gives counts + top groupings)
2. If summary reveals N>5 errors/warnings OR a clear pattern → propose drill-in: *"Top warning is X (12x). Want me to pull the timeline with `--target X --mode timeline`?"*
3. Only go to `--mode timeline` directly if the user already specified a target (in which case the targeted timeline IS the cheapest useful answer)

This prevents the "agent dumped 15K tokens of timeline data when summary would have done it in 200" failure mode.

---

## Script Location
`.claude/hooks/analyze_godot_logs.py`

## Modes

```bash
python .claude/hooks/analyze_godot_logs.py [LOG_PATH] --json --mode <MODE> [FILTERS] [EFFICIENCY]
```

| Mode | What it returns | Default fields | Use for |
|------|-----------------|----------------|---------|
| `summary` (default) | counts + top warnings/errors + recommendations + pattern stats | aggregate | "is the game healthy overall?" |
| `errors` | error+exception blocks only, max 20 by default | line, level, message, source, type, node | "just show me what broke" |
| `timeline` | chronological matched blocks, max 100 | all (raw_lines stripped) | targeted investigation, walk through events in order |
| `tail` | last N blocks (default 20, set with `--last`) | all (raw_lines stripped) | "what happened most recently?" |
| `tags` | `{tag: count}` histogram + tag-by-level breakdown | n/a | "which subsystems are noisy? [HSM] vs [DIAG] frequency" |
| `entity` | filtered to one entity (requires `--node`) | all (raw_lines stripped) | "everything HoarderCritter did across all systems" |

If you give filters (`--target`, `--level`, `--node`, `--type`) without `--mode`, mode defaults to `timeline`.

## Log File Selection

| `--log` value | Resolves to | When to use |
|---|---|---|
| (omitted) or `latest` | Active `godot.log` | Default — current/most-recent run |
| `previous` | Most-recent timestamped historical log | After a crash + quit (active log was truncated by next launch) |
| `<int>` | Nth-most-recent (0=latest, 1=previous, 2=...) | Walking back through past runs |
| `<path>` | Explicit file | Specific log you have a path for |

**Why this matters**: Godot truncates `godot.log` on every launch. If the user crashed, quit, and is asking you to analyze the crash, the active log is empty — you need `--log previous`.

## Filters (compose with any mode)

| Flag | Description |
|------|-------------|
| `--target TERM [...]` | AND-mode search (all terms must match in a block) |
| `--target-any TERM [...]` | OR-mode search (any term matches) |
| `--level LEVELS` | Comma-separated: `info,debug,warning,error,exception` (note: `errors` mode forces this to `error,exception` regardless) |
| `--node PATH` | Filter by node/owner path substring (case-insensitive) |
| `--type CLASS` | Filter by exact class name |
| `--limit N` | Max entries to show (default per mode: 100 timeline, 20 errors, all summary) |
| `--last N` | For `--mode tail`: number of trailing blocks (default 20) |

## Token-Efficiency Flags (apply to `--json` output)

| Flag | Default | Effect |
|------|---|---|
| `--include-raw` | OFF | Include `raw_lines` field. Default OFF saves **30-40%** of JSON size — `raw_lines` duplicates `message`+`source_file`+`backtrace`. |
| `--max-message N` | 300 | Truncate `message` field at N chars (appends `…(+N)` overflow indicator). `0` = no truncation. |
| `--no-backtrace` | (off) | Drop `backtrace` field entirely. Useful for non-error investigation. |
| `--fields F1,F2,...` | (per-mode) | Whitelist specific block fields. **Big win for narrow queries** — `--fields line_number,level,message` on tail-30 saves ~66%. |

### Choosing efficiency flags by goal

- "I just want a quick health check" → `--mode summary` (~200 tok, no per-block data)
- "I need every detail of a specific error" → `--mode timeline --target ... --include-raw`
- "I want to scan recent activity" → `--mode tail --last N --fields line_number,level,message`
- "I need exception backtraces" → `--mode errors` (already includes them; backtraces are typed)
- "I'm grepping for a pattern across many blocks" → `--mode tags` first to find the right tag, then `--target [Tag]`

## Domain Mapping Guide

Translate the user's natural language into script flags. **Always use `--json`.**

| User says | Script flags | What to examine in results |
|-----------|-------------|---------------------------|
| "perception" / "detection" / "sensing" | `--target Perception` | confidence values, category matches, `hasMemory`, `activeMemoryCount` |
| "hoarder" / "hoarder critter" | `--node HoarderCritter` | All events for the hoarder entity across all systems |
| "HSM" / "state transitions" / "state machine" | `--target HSM Transition` | from→to chains, urgent flags, propagation, missing transitions |
| "behavior tree" / "BT" | `--target BehaviorTree` | Task status changes, tree resets, enter/exit balance |
| "spell" / "spell spawning" | `--target Spawn` or `--target SpellPool` | Pool issues, archetype, SpawnEffect, spawner errors |
| "pool" / "pooling" | `--target Pool` | acquire vs return balance, PooledArchetype missing |
| "navigation" / "pathfinding" | `--target Navigator` or `--target AINavigator` | Target reached, path computation, ClearPath |
| "steering" / "avoidance" | `--target Steering` or `--target Consideration` | Score values, direction weights, override layers |
| "cornered" / "cornered state" | `--target Cornered` | Shuffle waypoints, threat position, entry/exit |
| "scurry" / "flee" | `--target Scurry` or `--target Flee` | Flee triggers, fade actions, threat detection |
| "forage" / "foraging" | `--target Forage` | Consume actions, ingredient collection, target selection |
| "wizard" / "player" | `--target Wizard` or `--node Player` | Player state machine, input handling |
| "ingredient" / "crafting" | `--target Ingredient` | Spawn counts, trait assignment, selector weights |
| "errors only" | `--mode errors` | All errors and exceptions, minimal fields |
| "warnings" | `--level warning` | All warnings |
| "what's been happening" / "recent" | `--mode tail --last 20` | Last 20 blocks chronologically |
| "what subsystems are talking" | `--mode tags` | Tag frequency histogram |

**Compound queries** (filters + modes are orthogonal):
- "hoarder perception issues" → `--mode timeline --node HoarderCritter --target Perception`
- "HSM warnings in last run" → `--target HSM --level warning`
- "spell pool errors from previous session" → `--log previous --mode errors --target Pool`
- "what HoarderCritter just did" → `--mode tail --node HoarderCritter --last 20`

**When the user's topic is NOT in the table**: Use their literal terms with `--target`. The script searches across all block fields (class name, node path, owner, message text, source file, method, tags, backtraces).

## Presenting Results

### Summary Mode
1. Run `--json --mode summary`, parse the JSON
2. Report severity counts and total blocks parsed
3. Highlight top grouped warnings/errors
4. Present HIGH severity recommendations
5. Suggest next steps (e.g., "want me to dig into the top error? `--mode timeline --target X`")

### Errors Mode
1. Run `--json --mode errors`, parse the JSON
2. If `blocks` is empty: "No errors in this log."
3. Otherwise walk through the errors with line numbers and source locations
4. Group by source file or message pattern if there's a clear cluster

### Timeline / Entity Mode
1. Parse the JSON's `blocks` array
2. Report: "Found N matching events out of M total blocks"
3. **Walk through chronologically as a narrative**: explain what happened in order
4. **Identify patterns**: repeated warnings, rapid-fire events, missing expected events
5. **Diagnose**: based on the sequence, propose a hypothesis for the issue
6. **Suggest**: concrete next steps (code to inspect, additional instrumentation, config changes)

### Tail Mode
1. Parse the JSON's `blocks` array (last N entries)
2. Walk through them in order — most useful for "what's the last thing that happened"
3. Often combined with `--node X` to get the last events for a specific entity

### Tags Mode
1. Parse the JSON's `tags` dict (overall frequencies) and `tags_by_level` (breakdown)
2. Report which subsystems are most active
3. Useful as a *first pass* — pick a tag, then drill in with `--target [Tag]`

### Diagnostic Strategies by Domain

**Perception**: Look for `hasMemory=True` vs `False` mismatches, wrong category hashes, `activeMemoryCount=0` when objects should be detected. Check if the sensor's category filter matches the target's Identity categories.

**HSM**: Trace the full state chain. Red flags: transitions looping to the same state, missing exit→enter sequences, urgent transitions overriding normal flow, states entered but never exited.

**Behavior Tree**: Track task lifecycle. Red flags: trees restarting immediately after success (missing `OnTreeSuccessState`), tasks failing silently (no error log), enter/exit count mismatch.

**Pooling**: Count acquire vs return operations. Red flags: "no pool exists" messages, `PooledArchetype` null, returns without prior acquire.

**Navigation**: Check for "Target reached" confirmations after path requests. Red flags: path never completed, rapid re-pathing, ClearPath called unexpectedly.

## Example invocations

```bash
# Health check (default)
python .claude/hooks/analyze_godot_logs.py --json

# Just the errors from the last run
python .claude/hooks/analyze_godot_logs.py --json --mode errors

# Just the errors from the previous run (after a crash)
python .claude/hooks/analyze_godot_logs.py --json --log previous --mode errors

# What happened most recently for the player wizard
python .claude/hooks/analyze_godot_logs.py --json --mode tail --node Wizard --last 30

# All HSM transitions across the spell-cast subsystem
python .claude/hooks/analyze_godot_logs.py --json --target HSM Spell

# Tag frequency to discover what's noisy
python .claude/hooks/analyze_godot_logs.py --json --mode tags

# Maximum compression: just the line numbers + messages of error blocks
python .claude/hooks/analyze_godot_logs.py --json --mode errors --fields line_number,level,message --no-backtrace
```

## Notes
- The parser handles JmoLogger's multi-line format (context + severity + backtrace as one block)
- Backtraces are compressed to user-relevant frames ({{PROJECT_NAME}}/Jmodot only, max 5)
- The script fixes the WARNING double-counting bug from Godot's GD.PushWarning duplication
- Empty fields are stripped from JSON output by default (further token savings)
- Log location: see CLAUDE.md MCP section for platform-specific default paths
- Legacy flags `--summary` and `--timeline` still work (alias to `--mode summary`/`--mode timeline`) for backward compat with prior automation
