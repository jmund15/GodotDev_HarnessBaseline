---
disable-model-invocation: true
---

Inspect and aggregate the continuous routing-audit log.

> Surfaces silent-misses against CLAUDE.md §9 routing rules. Complements `/routing_battery` (synthetic, periodic) with continuous, real-traffic data.

## Quick reference

| Subcommand | Purpose |
|---|---|
| `/routing_audit` | Default. Aggregate entries from the last 30 days, rotate older entries to archive, write stats JSON, print summary. Same as `aggregate`. |
| `/routing_audit aggregate` | Explicit alias of default. |
| `/routing_audit show <week>` | Print all silent-miss + cue-exempt entries from the named ISO week (e.g. `2026-W18`). |
| `/routing_audit silent-miss-only` | Print only `silent_miss` entries from the active window. |
| `/routing_audit top-rules` | Print the top-5 silent-miss rules with trends (subset of `aggregate` output). |

## What the audit log captures

The `routing_audit.py` PostToolUse hook (wired at `settings.json`) classifies every routing-relevant tool call and appends an event to `logs/routing_audit.jsonl` when the call falls into one of two tiers:

- **`nudge-warranted`** — call violated a clear §9 rule (PascalCase Grep on indexed file, synthesis-shaped Obsidian read, etc.). Each entry records `nudge_fired: bool` indicating whether the existing nudge channel (`tool_routing_post_grep.py` / stderr) actually reached the agent.
  - `nudge_fired=false` → **silent-miss** (the gap this audit log exists to find).
  - `nudge_fired=true` → **nudged routing miss** (channel works; agent ignored or accepted the nudge).
- **`cue-exempt`** — would warrant nudge BUT user prompt invokes K1 (literal-intent), L6 (verified-unique-name), or audit-shape carve-out. These count as compliant overrides.

Compliant calls and tools without §9 routing rules (Bash, Edit, Write, Read, Glob, etc.) are NOT logged — would drown the silent-miss signal in noise.

## How to read the output

The headline number is **`silent_miss`** count. Three things drive it up:
1. Doctrine drift in the agent — re-run `/routing_battery` to check if a doctrine-level fix is needed (negative-framing bullet in CLAUDE.md, etc.).
2. New code-discovery patterns the classifier doesn't recognize as compliant — extend `routing_classifier.py`.
3. The nudge channel breaking — verify `tool_routing_post_grep.py` still wires in `settings.json` and the per-session state file path is reachable.

The **`top_5_silent_miss_rules`** block shows trends vs. the prior 4-week average:
- `+N%` / `-N%` — current week vs. prior-4-week average; `flat` for <5% change.
- `new` — rule didn't fire in the prior window; appearance is the signal worth investigating.

## Files involved

- `.claude/hooks/routing_audit.py` — PostToolUse classifier + appender.
- `.claude/hooks/routing_classifier.py` — shared classification library.
- `.claude/tools/aggregate_routing_audit.py` — the aggregator (subcommands wrap this).
- `logs/routing_audit.jsonl` — active log (gitignored via `logs/`).
- `logs/routing_audit_stats.json` — single source of truth for `/eval_dashboard` Routing Stability section.
- `logs/routing-audit-archive/YYYY-MM.jsonl` — rotated entries (>30 days old).

## Procedure

### Default / `aggregate`

Run `python3 .claude/tools/aggregate_routing_audit.py`. Default behavior:
- Active window = 30 days.
- Rotate entries older than 30 days to `logs/routing-audit-archive/YYYY-MM.jsonl`.
- Write `logs/routing_audit_stats.json`.
- Print human-readable summary.

For non-default windows: pass `--weeks N` (e.g. `--weeks 1` for current sprint).
For no rotation: pass `--rotate-older-than-days 0` (kept entries == all entries).

### `show <week>`

```bash
python3 -c "
import json, sys
from pathlib import Path
target = sys.argv[1]
log = Path('logs/routing_audit.jsonl')
if not log.exists():
    print(f'(no log at {log})')
    sys.exit(0)
for line in log.read_text(encoding='utf-8').splitlines():
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        continue
    ts = e.get('ts','')
    # ISO week derivation
    from datetime import datetime
    try:
        if ts.endswith('Z'): ts = ts[:-1] + '+00:00'
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        continue
    iy, iw, _ = dt.isocalendar()
    if f'{iy}-W{iw:02d}' != target:
        continue
    print(f\"{e['ts']} | {e['classification']:<16} | {e['rule']:<30} | nudge_fired={e['nudge_fired']!s:<5} | {e['args_summary']}\")
" "$1"
```

### `silent-miss-only`

```bash
python3 -c "
import json
from pathlib import Path
log = Path('logs/routing_audit.jsonl')
if not log.exists():
    print('(no log)')
else:
    for line in log.read_text(encoding='utf-8').splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get('classification') == 'nudge-warranted' and not e.get('nudge_fired', False):
            print(f\"{e['ts']} | {e['rule']:<30} | {e['args_summary']}\")
"
```

### `top-rules`

Run aggregator and grep:
```bash
python3 .claude/tools/aggregate_routing_audit.py 2>/dev/null | sed -n '/Top silent-miss rules:/,/^$/p'
```

## Integration with /session_end

`/session_end` Phase 3.5 invokes `/routing_audit aggregate` (default mode). This keeps the stats JSON fresh for the next `/eval_dashboard` run without requiring manual invocation. Non-blocking — failures don't halt session-end flow.

## Integration with /eval_dashboard

The dashboard's "Routing Stability" section reads `logs/routing_audit_stats.json` produced by this command. Renders:
- 4-week trend line of `silent_miss` count.
- Top-5 silent-miss rules with `current_week / prior_4wk_avg / trend` columns.
- Cross-link to the most recent `/routing_battery` results for triangulation (synthetic vs. real-traffic).

## Troubleshooting

- **"no entries in logs/routing_audit.jsonl"** — either the audit hook isn't wired in `settings.json`, or no routing-relevant tool calls happened yet (a fresh session before any Grep/Obsidian/Memory call). Trigger a known-warrant call (e.g. `Grep("FireballBehavior", glob="*.cs")`) to confirm the hook fires.
- **Silent-miss count seems too high after a CLAUDE.md change** — re-run `/routing_battery` to triangulate. Continuous-audit silent-miss can spike for normal reasons (a sprint with lots of unfamiliar code-discovery tasks); the battery is the synthetic baseline.
- **Stats JSON has empty `top_5_silent_miss_rules`** — log is healthy but no `nudge-warranted` entries in the active window (good outcome). Verify by checking `silent_misses` count in the JSON.
