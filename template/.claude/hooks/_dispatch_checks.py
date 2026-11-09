#!/usr/bin/env python3
"""The checks a dispatch passes before launch, on either route.

Callers: `workflow_provider_guard.py` (native Workflow/Agent, RULE 3) and
`sidecar_dispatch_context.py` (every launcher and `sidecar_fanout.py` job). A sidecar child runs
on `bypassPermissions`, so this session's hooks never run inside it: the check runs here, before
launch, or not at all.

`tier_fit(model, tier, transport, data)` is the deny: a job whose tier its own call names (a written
`tier`, a registry lens, a shape row) must pin a row serving that tier on the job's OWN transport (native:
the session's; sidecar: the alias's registry transport). None when it fits or no tier is implied.

`seat_fit(seats, data)` is the deny for a review panel: it keeps `tier_fit`'s line for a pin holding the
seat's tier in neither registry `roles` nor `supplementary`, and refuses a pin holding it only in `supplementary`
unless a qualified seat in the same panel carries every mandate the seat does (ladder Role definitions). The
weakness: a review seat from such a row alone leaves defects a qualified seat finds, or reports invalid ones.
The retirement observation: a lone seat from the row matches a qualified seat's valid-finding recall on the
plan-review pin A/B; its entry then moves to `roles`. A row holding the tier in both `roles` and `supplementary`
is admitted alone here, because the ledger's clause 9 (`review_ledger.py`) floors its coverage.

`cheaper_claimants(model, tier, transport, reading_tokens, data, job=job)` is the fact list behind the preflight's claimant line
(`dispatch_size.fact_lines`) and the brief hook's table: each row on `transport` that claims `tier` in registry `roles`,
neither unavailable nor on an owner probation mark, and is cheaper than `model` for this reading. Cheaper means a lower
`price_reading` (`model_registry.list_price` for the reading, priced with the reading as the request's input so a row's
long-context multiplier applies past its threshold); when the row or the pin cannot be priced, it means a higher ladder
`cost`. Each claimant states its prices, its ladder `±` cell, whether the reading fits the point it would compact at for `job`
(`model_registry.compaction_point`), and whether
the reading is past its long-context point. These are facts for the reader: no check in this module denies on them.
`spare_plan_fact(tier, transport, data)` adds one fact when a row claiming `tier` on another plan projects
quota unused at its reset: `<alias> on <plan>: ~N% unused at reset in Dd`, read from the capacity cache only.

`kind_tiers()` reads each kind row's tier from `guards/tool_profiles.json`, for a job's `kind`.
`job_tier(kind, written)` is the sidecar route's one resolution: the job's written `tier`, else its kind row's.
Capacity is read through `provider_bands.read_capacity`: the fresh cache, else one live read while
the caller's deadline (`set_deadline`) leaves room for `provider_capacity.LIVE_BUDGET_SECONDS`.
"""
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import quota_bands  # noqa: E402
from _dispatch_pins import _tp, tier_rows  # noqa: E402
try:
    import model_registry  # noqa: E402
    import provider_bands  # noqa: E402
    import provider_capacity  # noqa: E402
except Exception:  # no registry reader: tier fit cannot be judged, currency says nothing
    model_registry = provider_bands = provider_capacity = None

# A started agent whose transcript has not changed for this long is not counted as live.
LIVE_SECONDS = 15 * 60
# Bytes read from the end of a standalone agent transcript to find its last record.
TAIL_BYTES = 256 * 1024
# Bytes read from the end of the session transcript to find subagent completion notifications, and how
# far before an agent's last write a notification may be stamped and still mean that write was its last.
NOTIFY_TAIL_BYTES = 8 * 1024 * 1024
NOTIFY_SLACK_SECONDS = 5.0

_TERMINAL = ("result", "failed", "error")
_AGENT_FILE = re.compile(r"^agent-(a[0-9a-f]+)\.jsonl$")
_NOTIFY_ID = re.compile(r"<task-id>(a[0-9a-f]+)</task-id>")
STAMP_RE = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')


def _run_live(run_dir, cutoff):
    """[{run, agentId, label}] started in this run's journal, not ended there, and fresh: its transcript,
    or the journal while it has none yet, changed at or after `cutoff`."""
    seen = {}
    with os.scandir(run_dir) as entries:
        for e in entries:
            if e.name.startswith("agent-") and e.name.endswith(".jsonl"):
                seen[e.name[len("agent-"):-len(".jsonl")]] = e.stat().st_mtime
    journal = os.path.join(run_dir, "journal.jsonl")
    journal_mtime = os.path.getmtime(journal)
    if journal_mtime < cutoff and not any(t >= cutoff for t in seen.values()):
        return []
    started, ended = {}, set()
    with open(journal, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            aid = rec.get("agentId") if isinstance(rec, dict) else None
            if not aid:
                continue
            if rec.get("type") == "started":
                started[aid] = str(rec.get("label") or aid)
            elif rec.get("type") in _TERMINAL:
                ended.add(aid)
    run = os.path.basename(run_dir)
    return [{"run": run, "agentId": aid, "label": label} for aid, label in started.items()
            if aid not in ended and seen.get(aid, journal_mtime) >= cutoff]


def _ended(transcript):
    """Is the last message record of a standalone agent transcript an assistant turn that stopped?"""
    with open(transcript, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") in ("assistant", "user"):
            return rec["type"] == "assistant" and \
                (rec.get("message") or {}).get("stop_reason") in ("end_turn", "stop_sequence")
    return False


def _notifications(transcript):
    """{agent id: epoch of its latest completion notification} from the session transcript's tail, or {}.

    A background agent's completion reaches the session as a `<task-notification>` naming its
    `<task-id>`; a resumed agent keeps writing after its old notification, so the caller compares times.
    """
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - NOTIFY_TAIL_BYTES))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        if "<task-notification>" not in line:
            continue
        stamp = STAMP_RE.search(line)
        try:
            when = datetime.datetime.fromisoformat(stamp.group(1).replace("Z", "+00:00")).timestamp()
        except (AttributeError, ValueError):
            continue
        for aid in _NOTIFY_ID.findall(line):
            out[aid] = max(out.get(aid, 0.0), when)
    return out


def _meta_label(meta_path, fallback):
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        return str(meta.get("description") or meta.get("agentType") or fallback)
    except (OSError, ValueError, AttributeError):
        return fallback


def live_jobs(session, now=None, deadline=None):
    """([{run, agentId, label}], note): the live native jobs under the session directory `session`, and what
    could not be read.

    A Workflow agent is live while its run journal holds its `started` line and no terminal line. An
    agent under `subagents/` itself (an Agent-tool child, or a resumed agent) has no journal: it is live
    unless its last message record is a stopped assistant turn or the session transcript holds a
    completion notification for it stamped after its last write. Either counts only while its
    transcript changed in the last LIVE_SECONDS. No tree is none live, with the reason as `note`.
    `deadline` is a `time.monotonic()` mark the scan stops a second before.
    """
    sub = os.path.join(session, "subagents")
    if not os.path.isdir(sub):
        return [], None if os.path.exists(session + ".jsonl") or os.path.isdir(session) else "session dir absent"
    cutoff = (time.time() if now is None else now) - LIVE_SECONDS
    live, seen, note = [], set(), None
    workflows = os.path.join(sub, "workflows")
    if os.path.isdir(workflows):
        with os.scandir(workflows) as runs:
            for run in runs:
                if deadline is not None and time.monotonic() > deadline - 1.0:
                    note = "scan stopped at the hook's time budget; runs after it were not read"
                    break
                if run.is_dir() and os.path.exists(os.path.join(run.path, "journal.jsonl")):
                    for job in _run_live(run.path, cutoff):
                        live.append(job)
                        seen.add(job["agentId"])
    notified = None
    with os.scandir(sub) as entries:
        for e in entries:
            m = _AGENT_FILE.match(e.name)
            if not m or m.group(1) in seen:
                continue
            aid, mtime = m.group(1), e.stat().st_mtime
            if mtime < cutoff or _ended(e.path):
                continue
            if notified is None:
                notified = _notifications(session + ".jsonl")
            if notified.get(aid, 0.0) >= mtime - NOTIFY_SLACK_SECONDS:
                continue
            live.append({"run": "agent-" + aid, "agentId": aid,
                         "label": _meta_label(os.path.join(sub, "agent-%s.meta.json" % aid), "(no description)")})
    return live, note




def session_dir(transcript, session_id=None):
    """`<transcript dir>/<session id>`, or None when no transcript is named. A transcript path already inside
    the session's tree (a subagent's) is cut back to the session directory."""
    transcript = str(transcript or "")
    if not transcript:
        return None
    sid = str(session_id or os.path.splitext(os.path.basename(transcript))[0])
    norm = os.path.normpath(transcript)
    marker = os.sep + sid + os.sep
    cut = norm.find(marker)
    return norm[:cut + len(marker) - 1] if cut >= 0 else os.path.join(os.path.dirname(norm), sid)


def band_run(session_id=None, budget=10.0):
    """(CompletedProcess, None) of `budget_posture.py --band --pressure --five-hour --used`, or (None, why) when
    it timed out or could not start. `session_id` makes it read this session's capture, not all of TEMP."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget_posture.py")
    cmd = [sys.executable, script, "--band", "--pressure", "--five-hour", "--used"]
    if session_id:
        cmd += ["--session", str(session_id)]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=budget), None
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:200])


def five_hour_fields(fields, used_field=()):
    """{"pressure", "resets_at", "used"} from the band read's 5h fields, or {} when they are absent or empty.
    `used` is the capture's 5h used percent from 0 to 100, None when the read did not carry it."""
    try:
        window = {"pressure": float(fields[0]), "resets_at": float(fields[1])}
    except (IndexError, ValueError):
        return {}
    try:
        window["used"] = float(used_field[0])
    except (IndexError, ValueError):
        window["used"] = None
    return window


def five_hour_read(session_id=None, budget=10.0):
    """({"pressure", "resets_at", "used"} or {}, why or None) for this session's 5-hour window."""
    out, err = band_run(session_id, budget)
    if out is None:
        return {}, err
    parts = out.stdout.strip("\r\n").split("\t")
    window = five_hour_fields(parts[2:4], parts[5:6])
    return window, None if window else "the band read carried no 5h window"


def native_room(pressure, live_count):
    """The one five-hour width rule (orchestration §5b): None while the window is cool or unread, else how many
    native jobs this session may still add: one live at a time, so `1 - live_count`, never below zero.
    RULE 5 denies a call wider than this; the fan-out keeps at most this many native units."""
    if pressure is None or pressure <= quota_bands.FIVE_HOUR_NARROW_PRESSURE:
        return None
    return max(0, 1 - live_count)


def native_width(session_id, session, deadline=None, now=None, budget=10.0):
    """{"pressure", "resets_at", "used", "live", "room", "note"} for the session whose directory is `session`:
    the window read, this session's live native jobs (scanned only while the window is hot) and `native_room`.
    `room` None means no limit, `note` says what could not be read."""
    window, why = five_hour_read(session_id, budget)
    pressure = window.get("pressure")
    live, note = [], None
    if native_room(pressure, 0) is not None:
        live, note = live_jobs(session, now=now, deadline=deadline) if session else ([], "no session directory")
    return {"pressure": pressure, "resets_at": window.get("resets_at"), "used": window.get("used"),
            "live": live, "room": native_room(pressure, len(live)), "note": why or note}

EFFORT_HOP = "add `-e` from the ladder's effort cell for each job's work shape"
_live_until = None


def set_deadline(monotonic_deadline):
    """Start no live capacity read that could still be running at `monotonic_deadline`."""
    global _live_until
    _live_until = monotonic_deadline


def read_capacity(transport, data=None):
    live = _live_until is None or time.monotonic() + provider_capacity.LIVE_BUDGET_SECONDS < _live_until
    return provider_bands.read_capacity(transport, data, live=live)


def kind_tiers():
    """{kind: tier} for every kind row of the tool-profile table that declares one, or {}. A job that
    carries `kind` takes its tier from here; a `judge` is an executor-role job."""
    try:
        rows = _tp().load_table().get("kinds") or {}
    except Exception:
        return {}
    return {name: row["tier"] for name, row in rows.items() if isinstance(row.get("tier"), str)}


def job_tier(kind, written=None):
    """(tier, source) a job's pin must serve: its written `tier`, else its `kind` row's, else (None, None).

    A written tier is canonical (`validation` folds onto `fanout`); an unknown one raises ValueError naming the known tiers."""
    if written:
        try:
            return model_registry.canonical_tier(str(written)), "the job's `tier`"
        except model_registry.RegistryError as err:
            raise ValueError(str(err)) from None
    tier = kind_tiers().get(kind)
    return (tier, f"kind `{kind}`") if tier else (None, None)


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


def _name(row):
    return row.get("alias") or row["id"]


ASSUMED_OUTPUT_TOKENS = 2000  # the output every row is priced at for a reading: one fixed assumption, so the comparison is about input


def price_reading(row_id, reading_tokens, data):
    """(usd, reason): the request priced at `reading_tokens` input (which applies a row's long-context multiplier past
    its threshold) and ASSUMED_OUTPUT_TOKENS output; (None, why) when unmeasured or unpriceable."""
    if not isinstance(reading_tokens, int):
        return None, "reading not measured"
    return model_registry.list_price(row_id, fresh=reading_tokens, request_input=reading_tokens,
                                     output=ASSUMED_OUTPUT_TOKENS, data=data)


def output_rate(row_id, data):
    """The row's list output rate per 1M tokens, or None when unpriced. A plan's draw tracks this rate within one
    plan (`orchestration_metrics.quota_weight`'s rule, read from the registry's price data), so a ratio of two rates
    means nothing across plans."""
    row = model_registry.row_for_model(row_id, data)
    rate = ((row or {}).get("price") or {}).get("outputPer1M")
    return float(rate) if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0 else None


def draw_against(row_id, base_id, data):
    """A row's output-rate ratio to a base row on the same plan, or None when either is unpriced."""
    rate, base = output_rate(row_id, data), output_rate(base_id, data)
    return None if rate is None or base is None else rate / base


def draw_ratios(row_ids, data):
    """{row id: output-rate ratio to the cheapest priced row among `row_ids`, or None}: one plan's draw column."""
    rates = {rid: output_rate(rid, data) for rid in row_ids}
    priced = [rate for rate in rates.values() if rate is not None]
    base = min(priced) if priced else None
    return {rid: (None if rate is None or not base else rate / base) for rid, rate in rates.items()}


def cheaper_claimants(model, tier, transport, reading_tokens, data=None, ladder=None, job=None):
    """[{alias, id, cost, pinCost, sign, usable, fits, thresholdAt, pastLongContext, tierMultiplier, price, pinPrice,
    priced, plan, draw}] (`plan`: the transport spends a plan quota; `draw`: output-rate ratio to the pin, or None), ordered by ladder cost, cheapest first: each row on `transport` claiming `tier` in registry `roles`,
    available, not on an owner probation mark for `tier`, and cheaper than `model` for `reading_tokens`. Cheaper is
    `price < pinPrice` when both are priced (`priced` True); otherwise `priced` is False and cheaper is a higher ladder
    `cost`. `fits` and `pastLongContext` are None for a reading not measured, and `fits` is None for a row stating no
    window. Facts only.

    Contract: `[]` means only that nothing is cheaper. What cannot be compared raises, so a caller prints the reason
    instead of an absence: `model_registry.UnknownModel` (the pin resolves to no row, its context suffix included),
    `model_registry.RegistryError` (the pin has no ladder cost), `OSError` (the ladder cannot be read) and `ValueError`
    (no `tier`)."""
    if not tier:
        raise ValueError("no tier to compare the pin against")
    data = data if data is not None else model_registry.load()
    pinned = model_registry.resolve(model, data)
    costs = model_registry.ladder_costs(data, ladder)
    mine = costs.get(pinned["id"])
    if mine is None:
        raise model_registry.RegistryError(
            f"`{pinned['id']}` has no cost on the ladder (or the ladder cannot be read), so it cannot be compared")
    with open(ladder or model_registry.ladder_path(), encoding="utf-8") as fh:
        cells = model_registry.parse_ladder_rows(fh, ("model", "±"))
    signs = {}
    for cell in cells:
        for m in model_registry.ladder_rows(cell["model"], data):
            signs.setdefault(m["id"], cell["±"])
    measured = isinstance(reading_tokens, int)
    pin_price, _why = price_reading(pinned["id"], reading_tokens, data) if measured else (None, None)
    plan = model_registry.cost_model_for(transport, data) == "plan-quota"
    out = []
    for row in tier_rows(tier, transport, data):
        cost = costs.get(row["id"])
        if row["id"] == pinned["id"] or not cost or model_registry.probation(row["id"], tier, data):
            continue
        price, _why = price_reading(row["id"], reading_tokens, data) if measured else (None, None)
        priced = price is not None and pin_price is not None
        if not (price < pin_price if priced else cost > mine):
            continue
        usable = model_registry.compaction_point(row, job)[0]
        point = model_registry.long_context_tier(row, data)
        at = point["thresholdInputTokens"] if point else None
        out.append({"alias": _name(row), "id": row["id"], "cost": cost, "pinCost": mine,
                    "sign": signs.get(row["id"], "no ± cell"), "usable": usable,
                    "fits": (reading_tokens <= usable) if measured and usable is not None else None,
                    "thresholdAt": at,
                    "pastLongContext": (at is not None and reading_tokens > at) if measured else None,
                    "tierMultiplier": point["multipliers"]["input"] if point else None,
                    "price": price if priced else None, "pinPrice": pin_price if priced else None,
                    "priced": priced, "plan": plan, "draw": draw_against(row["id"], pinned["id"], data)})
    return sorted(out, key=lambda c: -c["cost"])


def spare_plan_fact(tier, transport, data=None, now=None, read=None):
    """One terse fact per row claiming `tier` on another plan whose quota projects unused at its reset,
    joined with `; `, or None: `<alias> on <plan>: ~N% unused at reset in Dd`.

    Facts only, never a pick: it names quota that moving the job to that plan's row could spend. Every
    qualifying plan is named, and percents are never compared across plans. `read` defaults to the
    capacity cache only, so a preflight starts no probe. Never raises."""
    if not tier:
        return None
    try:
        data = data if data is not None else model_registry.load()
        now = time.time() if now is None else now
        read = read or (lambda name, _data=None: provider_bands.read_capacity(name, data, live=False))
        facts = []
        for plan in provider_bands.plan_quota_transports(data):
            if plan == transport:
                continue
            rows = tier_rows(tier, plan, data)
            if not rows:
                continue
            row = provider_bands.plan_room(plan, data, read, now)
            pct = provider_bands.spare_pct(row) if row.get("room") is not None else None
            if pct is None:
                continue
            days = row["unusedAtReset"].get("daysLeft")
            facts.append("%s on %s: ~%d%% unused at reset in %dd" % (
                _name(rows[0]), plan, pct, round(days if isinstance(days, (int, float)) else 0)))
        return "; ".join(facts) or None
    except Exception:
        return None
    return None


def claimant_text(c, reading_tokens, upper_tokens=None):
    """One claimant's facts, as the refusal and the preflight line state them. `reading_tokens` is the job's lower
    number, which the row's fit is judged on; `upper_tokens`, when it alone is past the row's point, adds `ceiling over`."""
    measured = isinstance(reading_tokens, int)
    if c["plan"]:
        money = f"draw ≈{c['draw']:.2f}x the pin" if c["draw"] is not None else "draw unpriced"
    elif c["priced"]:
        money = (f"≈${c['price']:.3f} vs the pin's ≈${c['pinPrice']:.3f} for this reading, "
                 f"{c['price'] / c['pinPrice']:.2f}x")
    else:
        money = "not priced, cheaper by ladder cost"
    parts = [f"cost {c['cost']}", money, f"± {c['sign']}"]
    if c["usable"] is None:
        parts.append("states no window")
    elif measured:
        ceiling = c["fits"] and isinstance(upper_tokens, int) and upper_tokens > c["usable"]
        parts.append(f"reading {reading_tokens:,} tokens {'fits' if c['fits'] else 'is over'} its "
                     f"{c['usable']:,}-token compaction point" + (f", ceiling over ({upper_tokens:,} if every named "
                                                                   f"file is read whole)" if ceiling else ""))
    else:
        parts.append(f"compaction point {c['usable']:,} tokens")
    if c["thresholdAt"] is None:
        parts.append("no long-context point")
    elif measured and c["pastLongContext"]:
        parts.append(f"past its {c['thresholdAt']:,}-token long-context point, its x{c['tierMultiplier']:g} "
                     f"tier price included")
    elif measured:
        parts.append(f"under its {c['thresholdAt']:,}-token long-context point")
    else:
        parts.append(f"long-context point {c['thresholdAt']:,} input tokens")
    return f"{c['alias']} ({'; '.join(parts)})"


