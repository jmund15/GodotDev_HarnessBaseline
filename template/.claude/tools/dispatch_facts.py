#!/usr/bin/env python3
"""The dispatch facts a launcher's `--check` prints before its verdict: the requested pin, the brief's size and the
files it names, the profile's tier, the ladder row that serves it and the latest shape rating. Facts only, never a
recommendation. `lib/sidecar_common.sh` `sc_check_dispatch_facts` runs this as

    dispatch_facts.py <harness root> <default model> [--check] [launcher flags...]

and a proof calls `fact_lines` (a generator of the lines) directly."""
import sys
from pathlib import Path

LADDER_COLUMNS = ('model', 'strengths', 'evidence', 'intel', 'deleg', 'speed', 'cost', 'billing', 'taste', 'effort', '±')


def fact_lines(root, default_model, args):
    root = Path(root)
    sys.path[:0] = [str(root / 'tools'), str(root / 'hooks')]
    import sidecar_argv, dispatch_size, model_registry, orchestration_metrics, tool_profiles, _dispatch_checks
    flags = sidecar_argv.sidecar_flags(args[1:] if args[:1] == ['--check'] else args)
    model = flags.get('m') or default_model
    effort = flags.get('e') or 'unspecified'
    profile = flags.get('G') or 'unspecified'
    kind_row = tool_profiles.load_table()['kinds'].get(profile) or {}
    yield f'requested pin: {model}·{effort}'
    brief = flags.get('f')
    if brief:
        try:
            content = Path(brief).read_bytes()
            tokens = dispatch_size.window_fit(None, len(content.decode('utf-8', errors='replace')))['tokens']
            yield f'brief: {len(content)} bytes; estimated tokens={tokens}'
            size = dispatch_size.dispatch_size(
                {'jobs': [{'promptPath': str(Path(brief).resolve()), 'model': model}]}, root=dispatch_size._project_root())
            yield dispatch_size.format_line(size)
        except OSError as exc:
            yield f'brief: unreadable ({exc})'
        except (KeyError, IndexError, ValueError) as exc:
            yield f'brief: unsized ({type(exc).__name__}: {exc})'
    else:
        yield 'brief: not supplied'
    try:
        tier = _dispatch_checks.job_tier(profile, flags.get('Q'))[0]
    except ValueError as exc:
        tier = None
        yield f'tier: {exc}'
    yield f'profile={profile}; tier={tier}' if tier else f'profile={profile}; no tier mapping exists'
    try:
        data = model_registry.load()
        entry = model_registry.resolve(model, data)
        if tier:
            rows = model_registry.parse_ladder_rows(
                (root / 'reference/model_ladder_evidence.md').read_text(encoding='utf-8').splitlines(), LADDER_COLUMNS)
            matched = [row for row in rows if tier in model_registry.ladder_cell_tiers(row['model'], data)
                       and entry['id'] in {m['id'] for m in model_registry.ladder_rows(row['model'], data)}]
            for row in matched:
                yield f"ladder row: {row['model']} ({row['strengths']}); rung: {row['effort']}"
            if not matched:
                yield 'ladder row: none for this tier and requested model'
        rating = orchestration_metrics.latest_shape_rating(
            tool_profiles.kind_guard(profile) if kind_row else profile, model, data)
        yield (f"shape rating: {rating['shape']}; note: {rating.get('note') or 'not recorded'}" if rating
               else 'shape rating: none exists')
    except (OSError, ValueError, model_registry.RegistryError) as exc:
        yield f'dispatch facts: unreadable ({exc})'


if __name__ == '__main__':
    for line in fact_lines(sys.argv[1], sys.argv[2], sys.argv[3:]):
        print(line)
