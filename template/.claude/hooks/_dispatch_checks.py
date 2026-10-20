#!/usr/bin/env python3
"""The checks a dispatch passes before launch, on either route.

Callers: `workflow_provider_guard.py` (native Workflow/Agent, RULE 3) and
`sidecar_dispatch_context.py` (every launcher and `sidecar_fanout.py` job). A sidecar child runs
on `bypassPermissions`, so this session's hooks never run inside it: the check runs here, before
launch, or not at all.

`tier_fit(model, tier, transport, data)` is the deny: a job whose tier its own call implies (a
registry lens, a shape row) must pin a row serving that tier on the job's OWN transport (native:
the session's; sidecar: the alias's registry transport). None when it fits or no tier is implied.

`seat_fit(seats, data)` is the deny for a review panel: it keeps `tier_fit`'s line for a pin holding the
seat's tier in neither registry `roles` nor `supplementary`, and refuses a pin holding it only in `supplementary`
unless a qualified seat in the same panel carries every mandate the seat does (ladder Role definitions). The
weakness: a review seat from such a row alone leaves defects a qualified seat finds, or reports invalid ones.
The retirement observation: a lone seat from the row matches a qualified seat's valid-finding recall on the
plan-review pin A/B; its entry then moves to `roles`. A row holding the tier in both `roles` and `supplementary`
is admitted alone here, because the ledger's clause 9 (`review_ledger.py`) floors its coverage.

`shape_tiers()` reads each shape row's tier from `guards/tool_profiles.json` for both routes.
Capacity is read through `provider_bands.read_capacity`: the fresh cache, else one live read while
the caller's deadline (`set_deadline`) leaves room for `provider_capacity.LIVE_BUDGET_SECONDS`.
"""
import os
import shlex
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from _dispatch_pins import _tp, tier_rows  # noqa: E402
try:
    import model_registry  # noqa: E402
    import provider_bands  # noqa: E402
    import provider_capacity  # noqa: E402
except Exception:  # no registry reader: tier fit cannot be judged, currency says nothing
    model_registry = provider_bands = provider_capacity = None

EFFORT_HOP = "add `-e` from the ladder's effort cell for each job's work shape"
_live_until = None


def set_deadline(monotonic_deadline):
    """Start no live capacity read that could still be running at `monotonic_deadline`."""
    global _live_until
    _live_until = monotonic_deadline


def read_capacity(transport, data=None):
    live = _live_until is None or time.monotonic() + provider_capacity.LIVE_BUDGET_SECONDS < _live_until
    return provider_bands.read_capacity(transport, data, live=live)


def shape_tiers():
    """{shape: tier} for every shape row of the tool-profile table that declares one, or {}."""
    try:
        rows = _tp().load_table()["shapes"]
    except Exception:
        return {}
    return {name: row["tier"] for name, row in rows.items() if isinstance(row.get("tier"), str)}


def tier_fit(model, tier, transport, data, pin_name=None):
    """None when `model` is one of `tier`'s rows on `transport` (or `tier` is None), else why not.

    `pin_name(row)` spells the fit rows in the refusal: the native pin on a Workflow call, the
    launcher alias (the default) on a sidecar.
    """
    if not tier:
        return None
    rows = tier_rows(tier, transport, data)
    if model in {v for r in rows for v in (r.get("id"), r.get("alias")) if v}:
        return None
    name = pin_name or (lambda r: r.get("alias") or r.get("id"))
    names = ", ".join(sorted({name(r) for r in rows})) or "none on this transport"
    return f"{model} is not among the `{tier}` rows on {transport} ({names})"


def _seat_entry(model, transport, data):
    """The registry row on `transport` that `model` (an id or alias) names, else None."""
    for row in data.get("models") or []:
        if row.get("transport") == transport and model in (row.get("id"), row.get("alias")):
            return row
    return None


def seat_verdicts(seats, data, pin_name=None):
    """[(seat, refusal line, is_rule)] for the seats of one review panel that do not fit.

    A seat is {label, model, tier, mandates, transport}. `is_rule` is False for a `tier_fit` line, returned
    unchanged and unlabelled, and True for the panel rule's line, which names its seat. A seat with no tier
    is never refused. The qualified seats are those whose pin holds the tier in `roles` and not in
    `supplementary` on the seat's own transport; their mandates, on any transport (a panel spans transports),
    are the ones a supplementary-only seat may lean on."""
    name = pin_name or (lambda r: r.get("alias") or r.get("id"))
    entries, standing, fits = [], [], []
    for seat in seats:
        tier = seat.get("tier")
        entry = _seat_entry(seat["model"], seat["transport"], data) if tier else None
        entries.append(entry)
        fits.append(tier_fit(seat["model"], tier, seat["transport"], data, pin_name))
        standing.append(model_registry.seat_standing(entry, tier) if entry else None)
    covered = {}
    for seat, how, fit in zip(seats, standing, fits):
        if how == "qualified" and fit is None:
            covered.setdefault(seat["tier"], set()).update(seat.get("mandates") or [])
    out = []
    for seat, entry, how, fit in zip(seats, entries, standing, fits):
        supplementary_only = (how == "supplementary" and seat["tier"] not in model_registry.entry_tiers(entry))
        if not supplementary_only:
            if fit:
                out.append((seat, fit, False))
            continue
        tier, transport = seat["tier"], seat["transport"]
        live = {r["id"] for r in model_registry.supplementary_rows(tier, data, seat=transport)
                if r.get("transport") == transport}
        if entry["id"] not in live:
            out.append((seat, fit, False))
            continue
        carried = list(seat.get("mandates") or [])
        missing = [m for m in carried if m not in covered.get(tier, set())] or (
            [] if carried else ["a mandate (this seat carries none)"])
        if not missing:
            continue
        qualified = sorted({name(r) if t == transport else (r.get("alias") or r.get("id"))
                            for t in (data.get("transports") or {}) for r in tier_rows(tier, t, data)
                            if tier not in model_registry.supplementary_tiers(r)})
        pins = f"one of {', '.join(qualified)}" if qualified else f"a row qualified for `{tier}`"
        out.append((seat, (
            f"`{seat['label']}`: {seat['model']} holds `{tier}` only as a supplementary review seat, and no "
            f"qualified `{tier}` seat in this call carries {', '.join(missing)}. Add a seat pinning {pins} "
            f"that carries them (`model_registry.py for-role {tier}`; ladder Role definitions)"), True))
    return out


def seat_fit(seats, data, pin_name=None):
    """The refusal lines for one review panel's seats, in seat order; [] when every seat fits."""
    return [line for _seat, line, _rule in seat_verdicts(seats, data, pin_name)]


def hop_line(row, data, label, launcher=None):
    """The launcher invocation that runs job `label` on `row`, or None when no launcher is registered.
    `launcher` names the paying plan's script; the default is the row's own transport's.
    `model_registry._sidecar_line` owns the command's shape; its effort pointer is stated once per deny."""
    try:
        line = model_registry._sidecar_line(row, data, launcher)
    except Exception:
        return None
    if not line.startswith("bash "):
        return None
    return line.split("  (", 1)[0].replace("<label>", shlex.quote(str(label)))
