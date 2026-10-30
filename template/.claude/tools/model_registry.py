#!/usr/bin/env python3
"""Reader + validator for .claude/reference/external_models.json.

The registry is the SSOT for external-transport models: role resolution, prices,
limits, and authorization gates. This module is the ONE parser — hooks and validate_commands.py
import it, Bash calls the CLI, PowerShell parses the JSON natively
(ConvertFrom-Json) to keep the shell profile free of a Python dependency.

Consumers differ in failure posture, deliberately (instruction_quality section 16):

  advisory hooks  fail OPEN  — fall back to a flash-only constant, but ALWAYS emit
                               the degradation in hookSpecificOutput.additionalContext
                               (stdout, exit 0). Never stderr: dead channel on PreToolUse.
  spending gates  fail CLOSED — deepseek_sidecar.sh exits 2 on an unreadable registry
                               rather than dispatching at a tier nobody chose.
  shared tooling  fail SILENT — validate_commands.py skips when the file is absent;
                               it is baseline-tracked and ships to projects with no
                               registry.

`gate.minBand` is validated and rank-compared against quota_bands.BANDS by IMPORT. A local
copy of the band list would drift from the thresholds it gates on, which is the whole failure
this registry removes.

`gate.minBand` is a FLOOR on THIS session's own pressure: a paid transport declares `On pace`
so it refuses to spend dollars at Surplus, where prepaid quota is expiring unused. There is no
per-row CEILING on the provider's own pressure. A plan-quota window refuses exactly where the
native route does — exhausted, or a band it cannot read — because a per-row ceiling (`Ahead` on
every codex row) refused at pressure the provider was nowhere near while the native Anthropic
route kept taking work. Which plan-quota window takes a job is the `orchestration` §5b
comparison between them, never a registry constant.

CLI:
    model_registry.py --check                       validate; exit 0 ok, 2 invalid
    model_registry.py resolve pro --field id        print one field; exit 0 / 2
    model_registry.py resolve pro                   print the whole entry as JSON
    model_registry.py for-role executor             rows serving a tier, in-transport and across
    model_registry.py for-role fanout --from codex  ...as seen from another seat
    model_registry.py context-window <model> [--max]  print the effective default or max window
    model_registry.py max-output <model> [--max]    print the child's output cap: the row's maxOutputTokens,
                                                    at most a quarter of its effective window
    model_registry.py rail-tier <model>             print detailed|condensed|minimal; detailed when unknown
    model_registry.py price <id> <fresh> <cache_read> <output> [cache_write] [cache_write_1h]   (list price at the current window)
    model_registry.py price-window <model>          billing window now (SC_PRICE_AT overrides), rates
    model_registry.py effort-values <model>         effort rungs the row may request; [] = undeclared
    model_registry.py effort-avoid <model> <rung>   the row's evidence when it avoids <rung>, else nothing
    model_registry.py band-satisfies <current> <required>   exit 0 yes, 1 no, 2 bad name
    model_registry.py sidecar-fields <model> [--plan <plan>]   launcher fields; 8-10 are the plan's
    model_registry.py plan-for-launcher <path>      plan, configDir, default flag, credential source; exit 1 if none
    model_registry.py review-state <alias> <tier>   probation basis or none
    model_registry.py ladder-slice <tier> [<tier>...]   the ladder cut to the rows claiming those tiers;
                                                    records the tiers for the dispatch gate (needs CLAUDE_CODE_SESSION_ID)
"""

import sys as _cli_usage_sys
from pathlib import Path as _cli_usage_path
_cli_usage_sys.path.insert(0, str(_cli_usage_path(__file__).resolve().parent))
from cli_usage import usage


import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from _win_path import drive_path

REGISTRY_RELPATH = Path(".claude") / "reference" / "external_models.json"
ENV_OVERRIDE = "HARNESS_MODEL_REGISTRY"

_REQUIRED_PRICE_FIELDS = ("cacheHitPer1M", "cacheMissPer1M", "outputPer1M", "currency", "asOf", "source")
# Cache-write rates, where the vendor bills writes apart from input (Anthropic's 5-minute and
# 1-hour writes). tools/plan_usage_calc.py weighs transcript usage with them.
_OPTIONAL_PRICE_RATES = ("cacheWrite5mPer1M", "cacheWrite1hPer1M", "cacheWritePer1M")

# A run record's `costBasis` when a plan-quota run carries a LIST-price cost: tokens times the vendor's
# rate, comparable across models, but not money spent. A consumer that sums spend calls
# `is_spend`, never tests `costUSD` for being numeric.
LIST_PRICE_BASIS = "list-price (plan-quota; not spend)"


def is_spend(cost_model, cost_basis):
    """False for a run record whose costUSD is a plan-quota list-price figure, not money spent.

    A record with no `costModel` reads its `costBasis`: the list-price marker is the only basis
    that carries a number for a plan-quota run.
    """
    return cost_model != "plan-quota" and not str(cost_basis or "").startswith("list-price")


_STALE_PRICE_DAYS = 90

# Availability gates the ROSTER, not the routing. An unavailable model is removed from the set a
# dispatcher chooses from; it is never paired with a substitute, because choosing the replacement is
# the dispatcher's job and depends on the task's shape and the budget — facts a config file does not
# have. `available` is the default, so an entry with no status resolves exactly as before.
#
# Declarable on a transport (gating every model it carries) or on a single model.
_LEGAL_TRANSPORT_STATES = {"available", "unavailable"}

# WHAT an exclusion is about. `all` (the default) excludes the row everywhere. `sidecar` excludes
# only the HOP -- the row stays dispatchable from its own transport, which is the case where the
# objection is to the cross-transport call rather than to the model. Validated, because an
# unvalidated closed set is unenforceable here: `sidcar` is not `all`, so a typo would silently
# narrow an exclusion to hops and leave the row dispatchable exactly where it was meant to be gone.
_LEGAL_STATUS_SCOPES = {"all", "sidecar"}

# WHICH CURRENCY a transport spends, which is the axis suspension decisions turn on. `marginal-usd`
# bills real money per call; `plan-quota` draws on prepaid allowance that expires unused. The two
# are not comparable and the cheaper one flips with circumstance, so the cost model is recorded
# rather than inferred from price fields -- a plan-quota transport can carry per-token prices for
# accounting and still cost nothing marginal to run.
_LEGAL_COST_MODELS = {"marginal-usd", "plan-quota"}

# The call shape a model's backend answers on. Absent keeps the Chat Completions default;
# `messages` takes the native Anthropic path, and `chat`/`responses` use a proxy.
_LEGAL_API_MODES = {"messages", "chat", "responses"}

# Rail tiers are the guards/*.md section names. A row without `railTier` reads `detailed`: a tier
# above it is earned by evidence that the model follows the shorter rails, never by capability.
_LEGAL_RAIL_TIERS = ("detailed", "condensed", "minimal")
# A tier above `detailed` names its evidence (instruction_quality §3): `unmeasured`, or a
# /rail_battery Phase B report. Shape is checked on every load; the report file only by --check, so
# a moved report never breaks a registry read.
_SHORTER_RAIL_TIERS = ("condensed", "minimal")
_RAIL_EVIDENCE_KINDS = ("measured", "unmeasured")

# `contextSuffix` is the request modifier every native dispatch of the row must carry to be served
# with the larger context window. Its only legal value is the host client's 1M-window modifier.
_LEGAL_CONTEXT_SUFFIXES = ("[1m]",)

# Time-of-day pricing. A provider that bills a window at a discount declares ONE schedule on its
# transport (the windows apply to every model it bills); row prices stay the list rates. Any
# future time-priced provider is a registry edit, never a launcher change.
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_HHMM = __import__("re").compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# What a launcher does when a dispatch starts inside a peak window. Absent = allow.
_LEGAL_PEAK_POLICIES = {"allow", "refuse"}

# Version succession. `current` is the newest version of its family and holds the bare alias;
# `superseded` is an older version still served under a versioned alias; `retired` is kept only so
# stored records and the alias history resolve. One family has at most one current row.
_LEGAL_LIFECYCLES = ("current", "superseded", "retired")

# Intake input (bench intake), never a measurement.
_LEGAL_PRIORS = ("frontier", "mid", "small")

# The one-model-table fields every row carries; consumers derive their order and rank tables from
# these instead of hand-copying a constant per view.
_ROW_TABLE_FIELDS = ("label", "family", "version", "lifecycle", "order", "costRank", "prior")

# The four fields every `driverNotes` entry carries: the instruction the model reads, the evidence
# that earned it, the failure it compensates, and the observation that retires it (instruction_quality
# §3). A note naming no weakness cannot be scoped, and one naming no removal condition outlives its
# cause.
_DRIVER_NOTE_FIELDS = ("text", "evidenceRef", "weakness", "removeWhen")


class RegistryError(Exception):
    """Registry missing, unreadable, or violating a field contract."""


class UnknownModel(RegistryError):
    """Name matched no alias and no id."""


# --------------------------------------------------------------------------- bands

def _quota_bands():
    """The band SSOT module. Never re-implement a band comparison in this file."""
    tools = Path(__file__).resolve().parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import quota_bands  # noqa: WPS433 - deliberate late import
    except Exception as exc:  # pragma: no cover - environment breakage
        raise RegistryError(f"cannot import quota_bands (band SSOT): {exc}")
    return quota_bands


def _bands():
    """Import BANDS from the module that owns them. Never copy the list."""
    return [name for _bound, name, _desc in _quota_bands().BANDS]


def band_rank(name):
    """Index into the ascending-pressure band order. Raises on an unknown name.

    `Exhausted` deliberately has NO rank (quota_bands.EXHAUSTED_BAND): it is a quantity, not a
    rung, so asking for its rank is an error here and a refusal in both comparators below.
    """
    try:
        return _quota_bands().band_rank(name)
    except ValueError as exc:
        raise RegistryError(str(exc))


def band_satisfies(current, required):
    """FLOOR: true when `current` is at or above `required` burn rate. See module docstring.

    Delegates to the SSOT rather than comparing ranks locally. A locally-computed rank would
    raise on `Exhausted`, and the launcher's gate reads that failure as SKIPPED, i.e. a PASS --
    so a spent allowance would sail through the very gate meant to stop it (2026-09-18).
    """
    try:
        return _quota_bands().band_satisfies(current, required)
    except ValueError as exc:
        raise RegistryError(str(exc))


# ------------------------------------------------------------------------ discovery

def find_registry(start=None):
    """Explicit env override, else walk up from `start` (default: this file).

    Walking up rather than assuming a fixed depth lets a user-global consumer
    (statusline.py) locate a per-project registry from the payload's cwd.
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override)
    here = Path(start).resolve() if start else Path(__file__).resolve()
    if here.is_file():
        here = here.parent
    for parent in [here, *here.parents]:
        candidate = parent / REGISTRY_RELPATH
        if candidate.is_file():
            return candidate
    # Fall back to this file's own repo location so an in-repo import always works.
    return Path(__file__).resolve().parents[1] / "reference" / "external_models.json"


# ----------------------------------------------------------------------------- load

_cache = {}


def _merge_overlay(data, target):
    """Merge `<registry stem>.local.json` beside `target` into `data`: a project's account fields
    (`credentialFile`, `credit`), keyed `{"transports": {<name>: {<field>: <value>}}}`. Absent is a
    no-op; anything unreadable or naming a transport the registry lacks raises."""
    overlay_path = target.with_name(target.stem + ".local.json")
    if not overlay_path.is_file():
        return
    try:
        with open(overlay_path, encoding="utf-8") as fh:
            overlay = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"overlay unreadable at {overlay_path}: {exc}")
    if not isinstance(overlay, dict) or set(overlay) - {"transports"}:
        raise RegistryError(f"{overlay_path}: top level must be an object holding only 'transports'")
    rows = overlay.get("transports", {})
    if not isinstance(rows, dict):
        raise RegistryError(f"{overlay_path}: 'transports' must be an object")
    transports = data.get("transports") if isinstance(data, dict) else None
    for name, fields in rows.items():
        if not isinstance(fields, dict):
            raise RegistryError(f"{overlay_path}: transport '{name}' must be an object")
        if not isinstance(transports, dict) or not isinstance(transports.get(name), dict):
            raise RegistryError(f"{overlay_path}: transport '{name}' is not in the registry")
        transports[name].update(fields)


def load(path=None, start=None):
    """Parse and validate. Raises RegistryError on anything unusable."""
    target = Path(path) if path else find_registry(start)
    key = str(target)
    if key in _cache:
        return _cache[key]
    if not target.is_file():
        raise RegistryError(f"registry not found: {target}")
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"registry unreadable at {target}: {exc}")
    _merge_overlay(data, target)
    _validate(data, target)
    _cache[key] = data
    return data


def _owner_evidence_valid(value):
    if not isinstance(value, str):
        return False
    dates = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", value)
    if not any(_iso_date_valid(day) for day in dates):
        return False
    return bool(re.search(r'["“][^"”]+["”]|\b(?:transcript|decision-ledger)[:/#][\w./-]+',
                          value, re.I))


def _iso_date_valid(day):
    try:
        date.fromisoformat(day)
        return True
    except ValueError:
        return False


def _pub_score_valid(score):
    value = score.get("value") if isinstance(score, dict) else None
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and isinstance(score.get("source"), str) and score["source"].strip() != ""
            and isinstance(score.get("date"), str) and _iso_date_valid(score["date"]))


def _validate(data, target):
    if not isinstance(data, dict):
        raise RegistryError(f"{target}: top level must be an object")
    groups = data.get("reviewGroups", {})
    if not isinstance(groups, dict) or any(not isinstance(k, str) or not k.strip()
            or not isinstance(v, str) or not v.strip() for k, v in groups.items()):
        raise RegistryError(f"{target}: reviewGroups must map family names to producer groups")
    for row in data.get("models", []) if isinstance(data.get("models"), list) else []:
        if isinstance(row, dict) and "reviewGroup" in row and row["reviewGroup"] != groups.get(row.get("family")):
            raise RegistryError(f"{target}: reviewGroup must agree with its family's route-independent reviewGroups mapping")
    transports = data.get("transports")
    models = data.get("models")
    if not isinstance(transports, dict) or not transports:
        raise RegistryError(f"{target}: 'transports' must be a non-empty object")
    if not isinstance(models, list) or not models:
        raise RegistryError(f"{target}: 'models' must be a non-empty array")

    # A transport can be SUSPENDED without being removed. The distinction matters because the
    # reasons a transport goes unused are temporary and external -- an unreplenished balance, a
    # price rise, a currency change -- while its ids, prices, roles and gates stay correct and
    # should survive to be reactivated. Deleting the entry to stop routing would throw away the
    # measured configuration and force it to be rebuilt from memory later.
    for tname, tcfg in transports.items():
        if not isinstance(tcfg, dict):
            raise RegistryError(f"{target}: transport {tname!r} must be an object")
        _validate_driver_notes(tcfg.get("driverNotes"), f"{target}: transport {tname}")
        if "longContextTier" in tcfg:
            _validate_long_context_tier(tcfg["longContextTier"], f"{target}: transport {tname}")
        cost = tcfg.get("costModel")
        if cost not in _LEGAL_COST_MODELS:
            raise RegistryError(
                f"{target}: transport {tname} costModel {cost!r} must be one of "
                f"{', '.join(sorted(_LEGAL_COST_MODELS))}")
        capacity_probe = tcfg.get("capacityProbe")
        capacity_unsupported = tcfg.get("capacityUnsupported")
        if (capacity_probe is None) == (capacity_unsupported is None):
            raise RegistryError(
                f"{target}: transport {tname} needs exactly one capacityProbe or "
                "capacityUnsupported declaration")
        if capacity_probe is not None:
            if not isinstance(capacity_probe, dict):
                raise RegistryError(f"{target}: transport {tname} capacityProbe must be an object")
            kind = capacity_probe.get("kind")
            timeout = capacity_probe.get("timeoutSeconds")
            if (not isinstance(capacity_probe.get("command"), str)
                    or not isinstance(capacity_probe.get("args"), list)
                    or kind not in ("quota", "balance")
                    or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                    or not 1 <= timeout <= 35):
                raise RegistryError(
                    f"{target}: transport {tname} capacityProbe needs command, args, "
                    "kind quota|balance, and timeoutSeconds 1..35")
            if cost == "plan-quota" and kind != "quota":
                raise RegistryError(
                    f"{target}: plan-quota transport {tname} needs a quota capacityProbe")
        elif (not isinstance(capacity_unsupported, dict)
              or not isinstance(capacity_unsupported.get("reason"), str)
              or not capacity_unsupported.get("reason").strip()):
            raise RegistryError(
                f"{target}: transport {tname} capacityUnsupported needs a non-empty reason")
        _validate_schedule(tname, tcfg, cost, target)
        status = tcfg.get("status")
        if status is None:
            continue
        if not isinstance(status, dict):
            raise RegistryError(f"{target}: transport {tname} 'status' must be an object")
        state = status.get("state")
        if state not in _LEGAL_TRANSPORT_STATES:
            raise RegistryError(
                f"{target}: transport {tname} status.state {state!r} must be one of "
                f"{', '.join(sorted(_LEGAL_TRANSPORT_STATES))}")
        if state == "unavailable" and not status.get("reason"):
            # A reason, and nothing more. It explains the exclusion to a human reading the file or to
            # an agent that tried anyway; it does not tell anyone what to use instead.
            raise RegistryError(
                f"{target}: transport {tname} is unavailable and needs a non-empty status.reason")

    _validate_plans(transports, target)

    seen_names = {}
    seen_upstreams = {}
    for entry in models:
        if not isinstance(entry, dict):
            raise RegistryError(f"{target}: every model must be an object")
        mid = entry.get("id")
        alias = entry.get("alias")
        transport = entry.get("transport")
        if not mid or not alias:
            raise RegistryError(f"{target}: model missing 'id' or 'alias': {entry!r}")
        if transport not in transports:
            raise RegistryError(f"{target}: model {mid} names unknown transport {transport!r}")
        if transports[transport].get("planOf") is not None:
            raise RegistryError(
                f"{target}: model {mid} names plan transport {transport!r}; a plan owns no rows, so "
                f"the row belongs on its route {transports[transport]['planOf']!r}")

        served_ids = entry.get("servedIds", [])
        if not isinstance(served_ids, list) or not all(isinstance(s, str) and s for s in served_ids):
            raise RegistryError(f"{target}: model {mid} servedIds must be an array of id strings")
        for name in (mid, alias, *served_ids):
            if name in seen_names:
                raise RegistryError(f"{target}: duplicate id/alias {name!r}")
            seen_names[name] = mid
        upstream = entry.get("upstreamId", mid)
        if not isinstance(upstream, str) or not upstream.strip():
            raise RegistryError(f"{target}: model {mid} upstreamId must be a non-empty string")
        upstream_key = (transport, upstream.lower())
        if upstream_key in seen_upstreams:
            raise RegistryError(f"{target}: duplicate upstreamId {upstream!r} on transport {transport}")
        seen_upstreams[upstream_key] = mid
        _validate_table_fields(entry, target)

        # Same shape as transport-level status (line ~195), and consumed the same way by
        # model_available() -- but until now nothing validated it, so a malformed model-level
        # status block would silently pass `--check` while breaking availability at dispatch
        # time instead.
        m_status = entry.get("status")
        if m_status is not None:
            if not isinstance(m_status, dict):
                raise RegistryError(f"{target}: model {mid} 'status' must be an object")
            m_state = m_status.get("state")
            if m_state not in _LEGAL_TRANSPORT_STATES:
                raise RegistryError(
                    f"{target}: model {mid} status.state {m_state!r} must be one of "
                    f"{', '.join(sorted(_LEGAL_TRANSPORT_STATES))}")
            m_scope = m_status.get("scope")
            if m_scope is not None and m_scope not in _LEGAL_STATUS_SCOPES:
                raise RegistryError(
                    f"{target}: model {mid} status.scope {m_scope!r} must be one of "
                    f"{sorted(_LEGAL_STATUS_SCOPES)} (default 'all')")
            if m_state == "unavailable" and not m_status.get("reason"):
                raise RegistryError(
                    f"{target}: model {mid} is unavailable and needs a non-empty status.reason")

        roles = entry.get("roles")
        if not isinstance(roles, list) or any(r not in TIER_ORDER for r in roles):
            raise RegistryError(
                f"{target}: model {mid} roles must be tier tokens only ({', '.join(TIER_ORDER)}); "
                "an empty array means alias-only dispatch")
        if len(roles) != len(set(roles)):
            raise RegistryError(f"{target}: model {mid} roles must not repeat a tier")
        effort = entry.get("effort") or {}
        if effort.get("evidence") == "unmeasured" and roles:
            raise RegistryError(
                f"{target}: model {mid} has effort.evidence 'unmeasured' but claims roles {roles!r}")
        supplementary = entry.get("supplementary", [])
        if not isinstance(supplementary, list) or any(r not in TIER_ORDER for r in supplementary):
            raise RegistryError(
                f"{target}: model {mid} supplementary must be tier tokens only ({', '.join(TIER_ORDER)})")
        if len(supplementary) != len(set(supplementary)):
            raise RegistryError(f"{target}: model {mid} supplementary must not repeat a tier")
        if effort.get("evidence") == "unmeasured" and supplementary:
            raise RegistryError(
                f"{target}: model {mid} has effort.evidence 'unmeasured' but lists supplementary "
                f"{supplementary!r}")

        # `pool` names the ladder pool a row belongs to (a `<pool> pool` cell covers each row carrying it).
        # `pubScore` is the published score a new member is ingested with; both are hand-set, never inferred.
        pool = entry.get("pool")
        if pool is not None and (not isinstance(pool, str) or not pool.strip()):
            raise RegistryError(f"{target}: model {mid} pool must be a non-empty string")
        if entry.get("pubScore") is not None and not _pub_score_valid(entry["pubScore"]):
            raise RegistryError(f"{target}: model {mid} pubScore must be an object with a numeric value, "
                                f"a non-empty source and an ISO date")

        # A `limits` block must name where its numbers came from. First-party metadata
        # (~/.codex/models_cache.json, models.dev) is a fact; an unsourced number is a guess that
        # reads identically once it is in the file, and a wrong context window truncates silently.
        # Provenance may be recorded per row or once per transport (a whole roster refreshed
        # from one catalog documents itself at the transport level; repeating it per row would
        # be the duplication this file exists to avoid).
        limits = entry.get("limits")
        if limits is not None and not isinstance(limits, dict):
            raise RegistryError(f"{target}: model {mid} 'limits' must be an object")
        if isinstance(limits, dict):
            for field in ("contextTokens", "maxContextTokens"):
                value = limits.get(field)
                if value is not None and (type(value) is not int or value <= 0):
                    raise RegistryError(
                        f"{target}: model {mid} limits.{field} must be a positive integer or null")
            percent = limits.get("effectiveContextPercent")
            if percent is not None and (type(percent) is not int or not 0 < percent <= 100):
                raise RegistryError(
                    f"{target}: model {mid} limits.effectiveContextPercent must be an integer "
                    "from 1 through 100")
            default_window = limits.get("contextTokens")
            max_window = limits.get("maxContextTokens")
            if (type(default_window) is int and type(max_window) is int
                    and max_window < default_window):
                raise RegistryError(
                    f"{target}: model {mid} limits.maxContextTokens must be at least "
                    "limits.contextTokens")
        compact_at = (limits or {}).get("compactAtTokens") if isinstance(limits, dict) else None
        if compact_at is not None and (isinstance(compact_at, bool) or not isinstance(compact_at, int)
                                       or not 0 < compact_at <= int(limits.get("contextTokens") or 0)):
            raise RegistryError(f"{target}: model {mid} limits.compactAtTokens must be a positive integer "
                                f"no larger than limits.contextTokens")
        if isinstance(limits, dict) and any(
            limits.get(k) is not None for k in ("contextTokens", "maxContextTokens", "maxOutputTokens")
        ) and not (entry.get("_limitsSource") or entry.get("_limitsComment")
                   or transports[transport].get("_limitsSource")):
            raise RegistryError(
                f"{target}: model {mid} authors 'limits' values with no '_limitsSource' naming "
                f"where they came from - an unsourced window is a guess. Record it on the row, "
                f"or once on transport {transport} when one catalog sourced the whole roster")

        # Every callable row carries the vendor's LIST price, whatever its costModel: the price is the
        # one axis two models' runs compare on, and `costModel` separately says what a run spends.
        # A row the vendor publishes no rate for carries a `priceGap` saying why and where that was
        # checked, so an absent rate is a recorded finding, never an oversight. A retired row that is
        # also excluded from dispatch can never be called, so it owes neither.
        if entry.get("longContextTier") is not None:
            _validate_long_context_tier(entry["longContextTier"], f"{target}: model {mid}")
        price = entry.get("price")
        gap = entry.get("priceGap")
        uncallable = (entry.get("lifecycle") == "retired"
                      and (entry.get("status") or {}).get("state") == "unavailable")
        if price is not None and gap is not None:
            raise RegistryError(f"{target}: model {mid} carries both 'price' and 'priceGap'; a gap "
                                f"says no first-party rate exists")
        if price is None and gap is None and uncallable:
            pass
        elif price is None and gap is not None:
            if not isinstance(gap, dict) or not all(
                    isinstance(gap.get(k), str) and gap[k].strip() for k in ("reason", "checked", "asOf")):
                raise RegistryError(f"{target}: model {mid} priceGap must be an object with non-empty "
                                    f"string 'reason', 'checked' (the source URL) and 'asOf'")
        elif not isinstance(price, dict):
            raise RegistryError(f"{target}: model {mid} missing 'price' object (or a 'priceGap' "
                                f"naming why no first-party rate exists)")
        else:
            for field in _REQUIRED_PRICE_FIELDS:
                if field not in price:
                    raise RegistryError(f"{target}: model {mid} price missing {field!r}")
            for field in (*_REQUIRED_PRICE_FIELDS[:3], *_OPTIONAL_PRICE_RATES):
                value = price.get(field)
                if field in price and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    raise RegistryError(f"{target}: model {mid} price {field!r} must be a number")

        # A callable row declares its context window, or a `windowGap` saying where the window was
        # looked for and not found: a dispatch is sized against `context-window`, and a silent absence
        # reads as "unbounded". A row that can never be called owes neither.
        window_gap = entry.get("windowGap")
        has_window = isinstance(limits, dict) and limits.get("contextTokens") is not None
        if has_window and window_gap is not None:
            raise RegistryError(f"{target}: model {mid} carries both limits.contextTokens and "
                                f"'windowGap'; a gap says no first-party window exists")
        if window_gap is not None:
            if not isinstance(window_gap, dict) or not all(
                    isinstance(window_gap.get(k), str) and window_gap[k].strip()
                    for k in ("reason", "checked", "asOf")):
                raise RegistryError(f"{target}: model {mid} windowGap must be an object with non-empty "
                                    f"string 'reason', 'checked' (the source) and 'asOf'")
        elif not has_window and not uncallable:
            raise RegistryError(f"{target}: model {mid} declares no limits.contextTokens (or a "
                                f"'windowGap' naming where the window was checked)")

        # An empty roles array keeps the row dispatchable by explicit alias only.
        auth = entry.get("authTier")
        if auth not in ("open", "gated"):
            raise RegistryError(f"{target}: model {mid} authTier must be 'open' or 'gated'")
        gate = entry.get("gate")
        if not isinstance(gate, dict):
            raise RegistryError(f"{target}: model {mid} missing 'gate' object")
        min_band = gate.get("minBand")
        if not min_band:
            raise RegistryError(f"{target}: model {mid} gate missing 'minBand'")
        band_rank(min_band)  # raises naming the legal set
        if auth == "gated" and not isinstance(gate.get("minBalanceUSD"), (int, float)):
            raise RegistryError(f"{target}: gated model {mid} needs a numeric gate.minBalanceUSD")
        if (transports[transport].get("capacityUnsupported") is not None
                and isinstance(gate.get("minBalanceUSD"), (int, float))
                and gate.get("minBalanceUSD") > 0):
            raise RegistryError(
                f"{target}: model {mid} requires a positive balance floor but transport "
                f"{transport} declares capacityUnsupported")

        # A per-row CEILING on the provider's own pressure is retired: it refused a codex dispatch
        # at band `Ahead` while the native Anthropic route kept taking work, so the same rule now
        # holds for every plan-quota window -- refuse on exhaustion or an unreadable band, and
        # compare windows through the `orchestration` §5b ordering rather than a registry constant.
        if gate.get("maxProviderBand") is not None:
            raise RegistryError(
                f"{target}: model {mid} declares gate.maxProviderBand {gate['maxProviderBand']!r}; "
                f"the per-row provider-band ceiling is retired - a plan-quota window refuses only "
                f"on exhaustion or an unreadable band, whoever its vendor is")

        policy = gate.get("peakPolicy")
        if policy is not None:
            if policy not in _LEGAL_PEAK_POLICIES:
                raise RegistryError(
                    f"{target}: model {mid} gate.peakPolicy {policy!r} must be one of "
                    f"{', '.join(sorted(_LEGAL_PEAK_POLICIES))}")
            if policy == "refuse" and not transports[transport].get("pricingSchedule"):
                raise RegistryError(
                    f"{target}: model {mid} gate.peakPolicy 'refuse' needs transport {transport} "
                    f"to declare a pricingSchedule - with no windows there is no peak to refuse")

        effort = entry.get("effort")
        if not isinstance(effort, dict) or effort.get("evidence") not in (
                "measured", "unmeasured", "owner", "see-ladder"):
            raise RegistryError(
                f"{target}: model {mid} effort.evidence must be measured|unmeasured|owner|see-ladder")
        if effort["evidence"] == "owner" and not _owner_evidence_valid(effort.get("evidenceRef")):
            raise RegistryError(
                f"{target}: model {mid} effort.evidenceRef needs an ISO date and quoted owner words "
                "or a transcript/decision-ledger citation")
        # No row holds a rung: the ladder cell owns the rung of every row it covers, and a row it
        # does not cover is `unmeasured`. `see-ladder` is legal on every transport.
        rival = [k for k in ("converged", "open") if effort.get(k)]
        if rival:
            raise RegistryError(
                f"{target}: model {mid} carries {', '.join('effort.' + k for k in rival)} - the "
                f"ladder cell owns every rung, so a rung here is a second home that drifts from it")

        if "default" in effort and effort["default"] not in effort_values(entry, data):
            raise RegistryError(
                f"{target}: model {mid} effort.default {effort['default']!r} is not a legal effort value")

        _validate_non_thinking(entry, data, target)
        _validate_avoid(entry, target)

        # Evidence names the version it measured. A new version takes over its family's claims until
        # a benchmark or an owner ruling shows it differs (owner ruling 2026-09-22), so measured
        # evidence may name this row's own version or a registered SAME-FAMILY row's version
        # (inherited). Any other version is a claim with no measured owner.
        version = entry.get("version")
        if version and effort.get("evidence") == "measured":
            measured = effort.get("measuredVersion")
            family = entry.get("family")
            inherited = measured is not None and family is not None and any(
                other is not entry and other.get("family") == family and other.get("version") == measured
                for other in data.get("models", []))
            if measured != version and not inherited:
                raise RegistryError(
                    f"{target}: model {mid} effort.measuredVersion {measured!r} is neither its version "
                    f"{version!r} nor a registered {family!r}-family version. Re-benchmark, or set "
                    f"effort.evidence 'unmeasured' and roles []")

        api_mode = entry.get("apiMode")
        if api_mode is not None and api_mode not in _LEGAL_API_MODES:
            raise RegistryError(
                f"{target}: model {mid} apiMode {api_mode!r} must be one of "
                f"{', '.join(sorted(_LEGAL_API_MODES))}")

        rail = entry.get("railTier")
        if rail is not None and rail not in _LEGAL_RAIL_TIERS:
            raise RegistryError(
                f"{target}: model {mid} railTier {rail!r} must be one of {', '.join(_LEGAL_RAIL_TIERS)}")
        if "contextSuffix" in entry and entry["contextSuffix"] not in _LEGAL_CONTEXT_SUFFIXES:
            raise RegistryError(
                f"{target}: model {mid} contextSuffix {entry['contextSuffix']!r} must be one of "
                f"{', '.join(_LEGAL_CONTEXT_SUFFIXES)}; delete the field to declare none")
        if rail in _SHORTER_RAIL_TIERS:
            ev = entry.get("railTierEvidence")
            if not isinstance(ev, dict) or ev.get("evidence") not in _RAIL_EVIDENCE_KINDS:
                raise RegistryError(
                    f"{target}: model {mid} railTier {rail!r} needs railTierEvidence "
                    f"{{evidence: {'|'.join(_RAIL_EVIDENCE_KINDS)}}} (/rail_battery)")
            if ev["evidence"] == "measured":
                missing = [k for k in ("evidenceRef", "route", "effort") if not str(ev.get(k) or "").strip()]
                if missing:
                    raise RegistryError(
                        f"{target}: model {mid} measured railTierEvidence is missing {', '.join(missing)}")
        _validate_driver_notes(entry.get("driverNotes"), f"{target}: model {mid}")
        _validate_probation(entry.get("probation"), f"{target}: model {mid}")

    current_by_family = {}
    order_owner = {}
    for entry in models:
        if entry["lifecycle"] == "current":
            family_key = (entry["family"], entry["transport"])
            other = current_by_family.setdefault(family_key, entry["id"])
            if other != entry["id"]:
                raise RegistryError(
                    f"{target}: family {entry['family']!r} on transport {entry['transport']!r} "
                    f"has two current rows ({other}, {entry['id']}) - mark the older one "
                    "superseded and give it a versioned alias")
        other = order_owner.setdefault(entry["order"], entry["id"])
        if other != entry["id"]:
            raise RegistryError(
                f"{target}: display order {entry['order']} is used by both {other} and {entry['id']}")
    _validate_alias_history(data.get("aliasHistory"), models, target)
    _validate_refresh_pins(transports, target)


# The capacity probe's budget split (scripts/anthropic_capacity_probe.py main): interpreter start
# before main() runs, and the least time the usage GET keeps after any lock wait or refresh.
PROBE_STARTUP_SECONDS = 3
PROBE_GET_FLOOR_SECONDS = 5


def _validate_refresh_pins(transports, target):
    """`capacityProbe.refresh` pins the probe's token-refresh turn: a model id, an effort the route
    declares, and a timeout that leaves the usage GET its floor inside the 35 s probe cap. `--check` also resolves the model to a row on
    the route (refresh_pin_problems); load() does not, so a fixture with a synthetic roster loads."""
    for tname, tcfg in transports.items():
        refresh = (tcfg.get("capacityProbe") or {}).get("refresh")
        if refresh is None:
            continue
        route = tcfg.get("planOf") or tname
        timeout = refresh.get("timeoutSeconds") if isinstance(refresh, dict) else None
        if not isinstance(refresh, dict) or not isinstance(refresh.get("model"), str) or not refresh["model"]:
            raise RegistryError(f"{target}: transport {tname} capacityProbe.refresh needs a model id")
        efforts = tcfg.get("effortValues", transports[route].get("effortValues"))
        if refresh.get("effort") is not None and efforts is not None and refresh.get("effort") not in efforts:
            raise RegistryError(
                f"{target}: transport {tname} capacityProbe.refresh.effort {refresh.get('effort')!r} is not "
                f"in {route}'s effortValues")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 35:
            raise RegistryError(
                f"{target}: transport {tname} capacityProbe.refresh.timeoutSeconds must be 1..35")
        probe = tcfg["capacityProbe"].get("timeoutSeconds")
        budget = min(35, timeout + probe) if isinstance(probe, (int, float)) and not isinstance(probe, bool) else 35
        if budget - timeout < PROBE_STARTUP_SECONDS + PROBE_GET_FLOOR_SECONDS:
            raise RegistryError(
                f"{target}: transport {tname} capacityProbe.refresh.timeoutSeconds {timeout} leaves the "
                f"usage GET {budget - timeout - PROBE_STARTUP_SECONDS} s of the probe's {budget} s; it "
                f"needs {PROBE_GET_FLOOR_SECONDS}")


def refresh_pin_problems(data):
    """One line per `capacityProbe.refresh.model` that names no row on its entry's route."""
    problems = []
    for tname, tcfg in (data.get("transports") or {}).items():
        model = ((tcfg.get("capacityProbe") or {}).get("refresh") or {}).get("model")
        route = tcfg.get("planOf") or tname
        if model and not any(m.get("id") == model and m.get("transport") == route
                             for m in data.get("models") or []):
            problems.append(f"transport {tname} capacityProbe.refresh.model {model!r} is not a row id on "
                            f"route {route}")
    return problems


def _validate_plans(transports, target):
    """`planOf` names a route (a registered transport with no `planOf` of its own); every plan of a
    route that has plans, the route included, names its login directory in `configDir`."""
    plans = {}
    for tname, tcfg in transports.items():
        route = tcfg.get("planOf")
        if route is None:
            continue
        if not isinstance(route, str) or route == tname or route not in transports:
            raise RegistryError(
                f"{target}: transport {tname} planOf {route!r} must name another registered transport")
        if transports[route].get("planOf") is not None:
            raise RegistryError(
                f"{target}: transport {tname} planOf {route!r} names a plan; planOf must name a route "
                f"(a transport with no planOf)")
        plans.setdefault(route, []).append(tname)
    for route, names in plans.items():
        owners = {}
        for name in (route, *names):
            config_dir = transports[name].get("configDir")
            if not isinstance(config_dir, str) or not config_dir.strip():
                raise RegistryError(
                    f"{target}: transport {name} needs a configDir: every plan of route {route}, the "
                    f"route included, names the login directory that pays for it")
            other = owners.setdefault(login_dir(config_dir), name)
            if other != name:
                raise RegistryError(
                    f"{target}: transports {other} and {name} of route {route} share configDir "
                    f"{config_dir!r}; each plan names its own login directory")


def _validate_table_fields(entry, target):
    mid = entry["id"]
    for field in ("label", "family", "version"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            raise RegistryError(f"{target}: model {mid} needs a non-empty '{field}' string")
    if entry.get("lifecycle") not in _LEGAL_LIFECYCLES:
        raise RegistryError(
            f"{target}: model {mid} lifecycle {entry.get('lifecycle')!r} must be one of "
            f"{', '.join(_LEGAL_LIFECYCLES)}")
    for field in ("order", "costRank"):
        value = entry.get(field)
        if type(value) is not int or value < 0:
            raise RegistryError(f"{target}: model {mid} '{field}' must be a non-negative integer")
    if entry.get("prior") not in _LEGAL_PRIORS:
        raise RegistryError(
            f"{target}: model {mid} prior {entry.get('prior')!r} must be one of {', '.join(_LEGAL_PRIORS)}")
    values = entry.get("effortValues")
    if values is not None and (not isinstance(values, list)
                               or not all(isinstance(v, str) and v for v in values)):
        raise RegistryError(f"{target}: model {mid} effortValues must be an array of rung names")


def _validate_non_thinking(entry, data, target):
    """`effort.nonThinking`: the rungs at which this row does not think, each one its transport
    forwards, with `effort.nonThinkingRef` citing the evidence. A review engine refuses those rungs."""
    effort = entry.get("effort") or {}
    if "nonThinking" not in effort:
        return
    mid = entry.get("id")
    rungs = effort.get("nonThinking")
    if not isinstance(rungs, list) or not rungs or not all(isinstance(r, str) and r for r in rungs):
        raise RegistryError(f"{target}: model {mid} effort.nonThinking must be a non-empty array of rung names")
    served = effort_values(entry, data)
    unknown = [r for r in rungs if served and r not in served]
    if unknown:
        raise RegistryError(
            f"{target}: model {mid} effort.nonThinking names {', '.join(unknown)}, which its transport does "
            f"not forward ({', '.join(served)})")
    if not str(effort.get("nonThinkingRef") or "").strip():
        raise RegistryError(f"{target}: model {mid} effort.nonThinking needs an effort.nonThinkingRef citing its evidence")


def non_thinking(entry):
    """The rungs a registry row declares it does not think at; [] when it declares none."""
    return list(((entry or {}).get("effort") or {}).get("nonThinking") or [])


def _validate_avoid(entry, target):
    """`effort.avoid`: the rungs this row's evidence shows are wasteful, with `effort.avoidRef` citing that
    evidence once the list is non-empty. The route's vocabulary says which rungs exist; this says which of
    them a pin refuses outside a `sidecar_fanout.py --compare` run or a benchmark campaign. A rung the row cannot serve is refused by the
    vocabulary first, so a row narrowed from another row's may carry its avoid list unchanged."""
    effort = entry.get("effort") or {}
    rungs = effort.get("avoid")
    if rungs is None or rungs == []:
        return
    mid = entry.get("id")
    if not isinstance(rungs, list) or not all(isinstance(r, str) and r for r in rungs):
        raise RegistryError(f"{target}: model {mid} effort.avoid must be an array of rung names")
    if not str(effort.get("avoidRef") or "").strip():
        raise RegistryError(f"{target}: model {mid} effort.avoid needs an effort.avoidRef citing its evidence")


def avoided_rungs(entry):
    """The rungs a registry row's evidence marks wasteful; [] when it declares none."""
    return list(((entry or {}).get("effort") or {}).get("avoid") or [])


def avoid_evidence(entry):
    """The row's `effort.avoidRef`, stripped; "" when it declares none."""
    return str(((entry or {}).get("effort") or {}).get("avoidRef") or "").strip()


def _validate_alias_history(history, models, target):
    """`aliasHistory`: which id each alias served, and until when. The last entry is the alias's
    current row; every boundary names its evidence, because a guessed date relabels records."""
    if history is None:
        return
    if not isinstance(history, dict):
        raise RegistryError(f"{target}: aliasHistory must be an object keyed by alias")
    ids = {m["id"] for m in models}
    holder = {m["alias"]: m["id"] for m in models}
    for alias, entries in history.items():
        where = f"{target}: aliasHistory[{alias!r}]"
        if not isinstance(entries, list) or not entries:
            raise RegistryError(f"{where} must be a non-empty array")
        previous = None
        for i, item in enumerate(entries):
            last = i == len(entries) - 1
            if not isinstance(item, dict) or item.get("id") not in ids:
                raise RegistryError(f"{where} entry {i} names no registered id: {item!r}")
            if ("until" in item) == last:
                raise RegistryError(
                    f"{where} entry {i}: every entry but the last needs 'until'; the last has none")
            if "since" in item and i != 0:
                raise RegistryError(f"{where} entry {i}: only the first entry may carry 'since'")
            if ("until" in item or "since" in item) and not str(item.get("evidence") or "").strip():
                raise RegistryError(f"{where} entry {i}: a boundary needs non-empty 'evidence'")
            for key in ("since", "until"):
                if key not in item:
                    continue
                at = _history_time(item[key])
                if at is None:
                    raise RegistryError(f"{where} entry {i}: {key} {item[key]!r} is not an ISO date")
                if previous is not None and at <= previous:
                    raise RegistryError(f"{where} entry {i}: boundaries must strictly increase")
                previous = at
        if entries[-1]["id"] != holder.get(alias):
            raise RegistryError(
                f"{where}: the last entry must be the row that holds alias {alias!r} now "
                f"({holder.get(alias)}), got {entries[-1]['id']}")


def _history_time(value):
    """ISO date or datetime -> aware UTC datetime; None when unparseable. A bare date is midnight."""
    from datetime import datetime, timezone
    text = str(value or "").strip()
    if not text:
        return None
    try:
        at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def _validate_schedule(tname, tcfg, cost, target):
    sched = tcfg.get("pricingSchedule")
    if sched is None:
        return
    if cost != "marginal-usd":
        raise RegistryError(
            f"{target}: transport {tname} declares a pricingSchedule but is {cost} - a time-of-day "
            f"price applies only to a marginal-usd transport")
    if not isinstance(sched, dict):
        raise RegistryError(f"{target}: transport {tname} pricingSchedule must be an object")
    mult = sched.get("offPeakMultiplier")
    if isinstance(mult, bool) or not isinstance(mult, (int, float)) or not 0 < mult <= 1:
        raise RegistryError(
            f"{target}: transport {tname} pricingSchedule.offPeakMultiplier must be a number in (0, 1]")
    for field in ("source", "asOf"):
        if not sched.get(field):
            raise RegistryError(f"{target}: transport {tname} pricingSchedule missing {field!r}")
    windows = sched.get("peakWindowsUTC")
    if not isinstance(windows, list) or not windows:
        raise RegistryError(
            f"{target}: transport {tname} pricingSchedule.peakWindowsUTC must be a non-empty array")
    for i, win in enumerate(windows):
        where = f"{target}: transport {tname} pricingSchedule.peakWindowsUTC[{i}]"
        if not isinstance(win, dict):
            raise RegistryError(f"{where} must be an object")
        days = win.get("days")
        if not isinstance(days, list) or not days or any(d not in _DAY_NAMES for d in days):
            raise RegistryError(f"{where}.days must be a non-empty subset of {', '.join(_DAY_NAMES)}")
        start, end = win.get("start"), win.get("end")
        if not all(isinstance(v, str) and _HHMM.match(v) for v in (start, end)):
            raise RegistryError(f"{where} start/end must be HH:MM (24h, UTC)")
        if start == end:
            raise RegistryError(f"{where} window start must differ from end (an end before the start crosses midnight)")


def _parse_at(at):
    """A timezone-aware UTC datetime from a datetime, an ISO string, or SC_PRICE_AT / now."""
    from datetime import datetime, timezone

    if at is None:
        at = os.environ.get("SC_PRICE_AT") or datetime.now(timezone.utc)
    if isinstance(at, str):
        at = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise RegistryError(f"price time {at!r} has no timezone; pass UTC")
    return at.astimezone(timezone.utc)


def _in_peak(sched, t):
    """A window with end < start crosses midnight and belongs to the day it STARTS: it covers
    start-24:00 on a listed day and 00:00-end on the day after."""
    day = _DAY_NAMES[t.weekday()]
    prev = _DAY_NAMES[(t.weekday() - 1) % 7]
    hhmm = t.strftime("%H:%M")
    for w in sched["peakWindowsUTC"]:
        if w["start"] < w["end"]:
            if day in w["days"] and w["start"] <= hhmm < w["end"]:
                return True
        elif (day in w["days"] and hhmm >= w["start"]) or (prev in w["days"] and hhmm < w["end"]):
            return True
    return False


def price_window(transport, at=None, data=None):
    """Which billing window `at` falls in on a transport, its multiplier, and when it next changes.

    `flat` = the transport declares no schedule. `at` defaults to SC_PRICE_AT (ISO UTC; the test
    and dry-run seam), else now. `changesAt` is the next instant the window flips, found by
    walking the window boundaries of the next eight days -- enough to cross any weekly schedule.
    """
    from datetime import datetime, time, timedelta

    data = data or load()
    cfg = data["transports"].get(transport)
    if cfg is None:
        raise RegistryError(f"unknown transport {transport!r}")
    sched = cfg.get("pricingSchedule")
    if not sched:
        return {"window": "flat", "multiplier": 1.0, "changesAt": None}
    t = _parse_at(at)
    peak = _in_peak(sched, t)
    boundaries = set()
    for offset in range(-1, 8):   # -1: yesterday's cross-midnight window can end today
        day = (t + timedelta(days=offset)).date()
        for w in sched["peakWindowsUTC"]:
            if _DAY_NAMES[day.weekday()] not in w["days"]:
                continue
            end_day = day if w["start"] < w["end"] else day + timedelta(days=1)
            for hhmm, on in ((w["start"], day), (w["end"], end_day)):
                h, m = (int(x) for x in hhmm.split(":"))
                boundaries.add(datetime.combine(on, time(h, m), tzinfo=t.tzinfo))
    changes = next((b for b in sorted(boundaries) if b > t and _in_peak(sched, b) != peak), None)
    return {
        "window": "peak" if peak else "off-peak",
        "multiplier": 1.0 if peak else float(sched["offPeakMultiplier"]),
        "changesAt": changes.strftime("%Y-%m-%dT%H:%M:%SZ") if changes else None,
    }


def stale_prices(data=None, max_age_days=_STALE_PRICE_DAYS):
    """Non-fatal: model ids whose price.asOf is older than the window.

    DeepSeek has announced a rise, so a cited-but-stale rate would silently
    degrade a stated fact into an assumption while the cost echo presents it
    as measured.
    """
    from datetime import date

    data = data or load()
    out = []
    today = date.today()
    for entry in data["models"]:
        price = entry.get("price")
        if not isinstance(price, dict):
            continue  # plan-quota model, no rate to go stale
        as_of = price.get("asOf")
        try:
            y, m, d = (int(part) for part in str(as_of).split("-"))
            age = (today - date(y, m, d)).days
        except Exception:
            out.append((entry["id"], "unparsable asOf"))
            continue
        if age > max_age_days:
            out.append((entry["id"], f"{age} days old"))
    return out


# ------------------------------------------------------------------------ accessors

def _validate_driver_notes(notes, where):
    """`driverNotes` is optional; when present every note names the evidence that earned it, the
    weakness it compensates and the observation that retires it."""
    if notes is None:
        return
    if not isinstance(notes, list):
        raise RegistryError(f"{where} driverNotes must be an array")
    for note in notes:
        if not isinstance(note, dict):
            raise RegistryError(f"{where} driverNotes entries must be objects")
        missing = [field for field in _DRIVER_NOTE_FIELDS if not str(note.get(field) or "").strip()]
        if missing:
            raise RegistryError(
                f"{where} driverNotes entries need non-empty {', '.join(missing)} - "
                f"a per-model correction without its evidence, weakness or removal condition "
                f"cannot be scoped or retired")


def row_for_model(model, data=None):
    """The registry row a session payload's model string names, or None.

    Payload strings are not registry ids: they carry a context suffix (`[1m]`) and may name a
    longer, unregistered version of a registered id. Match an alias, id or `servedIds` spelling
    exactly, else the longest id the string starts with. Unknown stays None; callers treat that as
    strict. Record identity uses `row_by_id`, which never takes the prefix step.
    """
    name = str(model or "").strip().lower()
    if "[" in name:
        name = name.split("[", 1)[0]
    if not name:
        return None
    try:
        rows = [m for m in (data or load())["models"] if isinstance(m, dict)]
    except Exception:
        return None
    for m in rows:
        names = [m.get("id", ""), m.get("alias", ""), *(m.get("servedIds") or [])]
        if name in (str(n).lower() for n in names):
            return m
    prefixed = [m for m in rows if m.get("id") and name.startswith(str(m["id"]).lower())]
    return max(prefixed, key=lambda m: len(m["id"])) if prefixed else None


def rail_tier(model, data=None):
    """`detailed|condensed|minimal` for a model string; `detailed` for anything unknown or unreadable."""
    try:
        row = row_for_model(model, data)
    except Exception:
        return "detailed"
    tier = (row or {}).get("railTier")
    return tier if tier in _LEGAL_RAIL_TIERS else "detailed"


def context_suffix(model, data=None):
    """The `contextSuffix` a model string's row declares, or "" when it declares none or is unknown."""
    try:
        row = row_for_model(model, data)
    except Exception:
        return ""
    suffix = (row or {}).get("contextSuffix")
    return suffix if suffix in _LEGAL_CONTEXT_SUFFIXES else ""


def context_suffix_names(data, transport=None):
    """{id or alias: suffix} for every row that declares `contextSuffix`, on `transport` when given.

    The names are exact registry spellings: a guard rewriting a pin matches a whole name, never a prefix.
    """
    out = {}
    for m in (data or {}).get("models") or []:
        if not isinstance(m, dict) or m.get("contextSuffix") not in _LEGAL_CONTEXT_SUFFIXES:
            continue
        if transport is not None and m.get("transport") != transport:
            continue
        for name in (m.get("id"), m.get("alias")):
            if name:
                out[name] = m["contextSuffix"]
    return out


def driver_notes(model, data=None):
    """[(text, evidenceRef)] for the model driving a session: its row's notes, then its transport's."""
    try:
        data = data or load()
        row = row_for_model(model, data)
        if row is None:
            return []
        transport = (data.get("transports") or {}).get(row.get("transport")) or {}
        notes = list(row.get("driverNotes") or []) + list(transport.get("driverNotes") or [])
        return [(n["text"].strip(), n["evidenceRef"].strip()) for n in notes
                if isinstance(n, dict) and n.get("text") and n.get("evidenceRef")]
    except Exception:
        return []


def resolve(name, data=None):
    """Accept an alias or a full id, with or without a legal context suffix; return the model entry."""
    data = data or load()
    bare = name
    for suffix in _LEGAL_CONTEXT_SUFFIXES:
        if isinstance(name, str) and name.endswith(suffix):
            bare = name[:-len(suffix)]
    for entry in data["models"]:
        if bare in (entry["id"], entry["alias"]):
            return entry
    legal = sorted({v for e in data["models"] for v in (e["id"], e["alias"])})
    raise UnknownModel(f"unknown model {name!r}; legal values: {', '.join(legal)}")


def issuer(name, data=None):
    """The lab that issues `name`'s model (`familyIssuers` by the row's family), whatever transport serves it;
    None when the row is unknown or the table omits its family."""
    data = data or load()
    try:
        entry = resolve(name, data)
    except UnknownModel:
        return None
    return (data.get("familyIssuers") or {}).get(entry.get("family"))


# ------------------------------------------------------------------ model identity (S1)
#
# A cell token is the dispatch alias, never identity: `opus` names a different model on each side
# of a version bump. Identity is the model a record attests, resolved to its registry row.

def _bare_id(model_id):
    name = str(model_id or "").strip().lower()
    return name.split("[", 1)[0] if "[" in name else name


def row_by_id(model_id, data=None, *, transport=None):
    """Find an exact recorded id or upstream spelling, preferring the record's transport.

    Without a transport, preserve legacy id/servedIds resolution before considering upstreamIds.
    """
    name = _bare_id(model_id)
    if not name:
        return None
    models = (data or load())["models"]
    if transport:
        for m in models:
            if m["transport"] == transport and (name == m["id"].lower()
                    or name == m.get("upstreamId", "").lower()
                    or name in (s.lower() for s in m.get("servedIds") or [])):
                return m
    for m in models:
        if name == m["id"].lower() or name in (s.lower() for s in m.get("servedIds") or []):
            return m
    return next((m for m in models if name == m.get("upstreamId", "").lower()), None)


def family_named(name, data=None):
    """The family `name` stands for when it is a bare family name, else None. A family name is not a version:
    it names whichever row held it when a record was written."""
    wanted = _bare_id(name)
    models = (data if data is not None else load())["models"]
    return next((m["family"] for m in models if wanted and wanted == str(m.get("family", "")).lower()), None)


_VERSION_SEGMENT = re.compile(r"^[a-z]?\d+(?:\.\d+)*$")


def product_line(model_id):
    """The model id without its version tokens: `acme-2.1-mini` -> `acme-mini`. A family groups one line."""
    parts = []
    for segment in model_id.lower().split("-"):
        if _VERSION_SEGMENT.match(segment):
            continue
        segment = re.sub(r"\d+(?:\.\d+)*$", "", segment)
        if segment:
            parts.append(segment)
    return "-".join(parts) or model_id.lower()


def row_for_alias(name, data=None):
    """The row whose alias is exactly `name`, else None."""
    wanted = _bare_id(name)
    models = (data if data is not None else load())["models"]
    return next((m for m in models if wanted and wanted == str(m.get("alias", "")).lower()), None)


def takes_effort(name, data=None):
    """False only when the row `name` names (alias, id, or a served id) declares `effortParam: false`;
    an unknown or empty name takes an effort."""
    if not name:
        return True
    try:
        data = data or load()
        try:
            row = resolve(name, data)
        except UnknownModel:
            row = row_by_id(name, data)
    except Exception:
        return True
    return not (row and row.get("effortParam") is False)

def label(model_id, data=None):
    """Display label for a model id; the id itself when unregistered."""
    row = row_by_id(model_id, data)
    return row["label"] if row else str(model_id)


def family(model_id, data=None):
    row = row_by_id(model_id, data)
    return row["family"] if row else None


def lifecycle(model_id, data=None):
    """`current|superseded|retired`, or None for an unregistered id."""
    row = row_by_id(model_id, data)
    return row["lifecycle"] if row else None


def rows(lifecycle=None, data=None):
    """Every row in display order, optionally only one lifecycle."""
    out = [m for m in (data or load())["models"] if lifecycle is None or m["lifecycle"] == lifecycle]
    return sorted(out, key=lambda m: m["order"])


def anthropic_ids(data=None):
    """Exact ids of the dispatchable Anthropic-transport rows -- the only judge ids a scorer accepts."""
    data = data or load()
    return [m["id"] for m in rows(data=data)
            if m["transport"] == HOST_ROLE_SOURCE and model_available(m["id"], data)]


def transport_is_direct(transport, data=None):
    """True when a child on this transport reports its served model truthfully.

    A transport declaring `modelAttestation` is a proxy whose child reports its own client pin, so
    its `servedModel` is not identity. An unregistered transport is never direct.
    """
    cfg = (data or load())["transports"].get(transport) if isinstance(transport, str) else None
    return isinstance(cfg, dict) and "modelAttestation" not in cfg


def resolve_alias_at(alias, when, data=None):
    """The id `alias` served at ISO date/datetime `when`, or None when no evidence covers it.

    An alias absent from `aliasHistory` has only ever served its current row. A date before the
    history's first evidenced `since` is unresolved rather than assigned to the earliest known id.
    """
    data = data or load()
    at = _history_time(when)
    history = (data.get("aliasHistory") or {}).get(alias)
    if not history:
        for m in data["models"]:
            if alias in (m["alias"], m["id"]):
                return m["id"]
        return None
    if at is None:
        return None
    first = history[0]
    if "since" in first and at < _history_time(first["since"]):
        return None
    for item in history:
        if "until" not in item or at < _history_time(item["until"]):
            return item["id"]
    return None


def record_model_id(record, token=None, when=None, data=None):
    """The model id an arm record ran on, by authority; None when nothing identifies it.

    attestedModel (the proxy's own capture) > modelVersion > servedModel on a direct transport only
    > the alias history for the dispatch token at the record's date.
    """
    data = data or load()
    rec = record or {}
    attested = rec.get("attestedModel")
    if isinstance(attested, str) and attested.strip():
        return attested
    version = rec.get("modelVersion")
    if isinstance(version, str) and version.strip():
        # A vendor version string names the row recording it on the record's own transport before any
        # id or served-id spelling, which may belong to another transport's row.
        for m in data["models"]:
            if m.get("version") == version and m.get("transport") == rec.get("transport"):
                return m["id"]
        matched = row_by_id(version, data, transport=rec.get("transport"))
        if matched:
            return matched["id"]
        # A vendor version string (a versionless id's `version`), mapped to the row recording it.
        for m in data["models"]:
            if m.get("version") == version and m.get("transport") == rec.get("transport"):
                return m["id"]
        for m in data["models"]:
            if m.get("version") == version:
                return m["id"]
        return version  # unregistered: row_by_id -> None flags it; never relabel via the alias
    served = rec.get("servedModel")
    if isinstance(served, str) and served.strip() and transport_is_direct(rec.get("transport"), data):
        matched = row_by_id(served, data, transport=rec.get("transport"))
        return matched["id"] if matched else served
    if token:
        return resolve_alias_at(token, when or rec.get("timestamp"), data)
    return None


def record_identity(record, token=None, when=None, data=None):
    """Annotate record_model_id's authority path; alias history is inferred, never observed."""
    data = data or load()
    rec = record if isinstance(record, dict) else {}
    model = record_model_id(rec, token=token, when=when, data=data)
    basis = None
    if isinstance(rec.get("attestedModel"), str) and rec["attestedModel"].strip():
        basis = "attestedModel"
    elif isinstance(rec.get("modelVersion"), str) and rec["modelVersion"].strip():
        basis = "modelVersion"
    elif isinstance(rec.get("servedModel"), str) and rec["servedModel"].strip() and transport_is_direct(rec.get("transport"), data):
        basis = "servedModel"
    elif model is not None:
        basis = "aliasHistory"
    return {"model": model, "basis": basis, "modelObserved": basis in ("attestedModel", "modelVersion", "servedModel")}


def review_group(model, data=None):
    """Producer group by registry family, independent of route or paying plan; unknown stays None."""
    data = data or load()
    row = row_by_id(model, data) if model else None
    return (data.get("reviewGroups") or {}).get(row.get("family")) if row else None


def models_for(transport, data=None):
    data = data or load()
    return [e for e in data["models"] if e["transport"] == transport]


def transports(data=None):
    """Every registered transport name, host included."""
    data = data or load()
    return sorted(data["transports"])


_PLAN_INHERITED = ("effortValues", "roleSource")


def transport_meta(name, data=None):
    """One transport's config row, or None. The resolver in hooks/_session_transport.py reads
    `baseUrl` from this to match a session endpoint, so a transport without one (codex's
    per-session loopback proxy, anthropic's direct connection) is identified by
    CLAUDE_CODE_TRANSPORT alone -- by construction, not by omission. A plan (`planOf`) inherits
    only `effortValues` and `roleSource` from its route; every other field is its own."""
    data = data or load()
    cfg = data["transports"].get(name)
    if not isinstance(cfg, dict):
        return None
    meta = dict(cfg)
    route = data["transports"].get(cfg.get("planOf")) if cfg.get("planOf") else None
    if isinstance(route, dict):
        for key in _PLAN_INHERITED:
            if key not in meta and key in route:
                meta[key] = route[key]
    return meta


# A transport is a route (which model ids and efforts are legal); a plan is who pays. Every transport
# is its own plan; an entry with `planOf` is a further plan paying for its route's rows.

DEFAULT_LOGIN_DIR = "~/.claude"


def expand_login_dir(path):
    """`path` as a usable login directory: `~`, `$VAR`, `${VAR}` and `%VAR%` expanded, Git Bash
    `/c/...` read as `C:/...`, forward slashes, no trailing separator."""
    text = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), str(path).strip())
    text = os.path.expandvars(os.path.expanduser(text)).replace("\\", "/")
    text = drive_path(text)
    return text.rstrip("/") or "/"


def login_dir(path):
    """The comparable form of a login (config) directory: expand_login_dir, case-folded on Windows.
    Every comparison of two login dirs goes through this."""
    text = expand_login_dir(path)
    return text.lower() if os.name == "nt" else text


def is_default_login(path):
    """True when `path` (None: unset) names the default login `~/.claude`."""
    return login_dir(path or DEFAULT_LOGIN_DIR) == login_dir(DEFAULT_LOGIN_DIR)


def projects_dir(path):
    """`<login dir>/projects`, where a login writes its session transcripts; `path` None is the default login."""
    return os.path.join(expand_login_dir(path or DEFAULT_LOGIN_DIR), "projects")


def login_dirs(data=None):
    """Every login (config) directory, expanded, each once: the default `~/.claude`, each transport's
    `configDir`, and `CLAUDE_CONFIG_DIR` when set. The registry declares each login a transport uses,
    so a directory no transport names is not a login."""
    data = data if data is not None else load()
    candidates = [DEFAULT_LOGIN_DIR]
    candidates += [cfg["configDir"] for _, cfg in sorted((data.get("transports") or {}).items())
                   if isinstance(cfg, dict) and cfg.get("configDir")]
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        candidates.append(os.environ["CLAUDE_CONFIG_DIR"])
    out, seen = [], set()
    for path in candidates:
        if login_dir(path) not in seen:
            seen.add(login_dir(path))
            out.append(expand_login_dir(path))
    return out


def session_projects_dir():
    """This session's transcript root: its login's `projects` (`CLAUDE_CONFIG_DIR`, default `~/.claude`)."""
    return projects_dir(os.environ.get("CLAUDE_CONFIG_DIR"))


def route_of(name, data=None):
    """The transport whose model rows `name` pays for: its `planOf`, else `name` itself."""
    data = data if data is not None else load()
    cfg = (data.get("transports") or {}).get(name)
    return (cfg.get("planOf") if isinstance(cfg, dict) else None) or name


def _plan_live(name, data):
    cfg = (data.get("transports") or {}).get(name)
    return isinstance(cfg, dict) and (cfg.get("status") or {}).get("state") != "unavailable"


def plans_of(transport, data=None):
    """`transport` first, then each plan whose `planOf` names it and whose own status is not unavailable."""
    data = data if data is not None else load()
    siblings = sorted(n for n, c in (data.get("transports") or {}).items()
                      if isinstance(c, dict) and c.get("planOf") == transport and _plan_live(n, data))
    return [transport] + siblings


def plan_for_launcher(path, data=None):
    """The one transport whose `launcher` is `path` (repo-relative, absolute, Windows or Git Bash
    form), else None -- also when two transports share the script, since it then names no plan."""
    found = transports_for_launcher(path, data)
    return found[0] if len(found) == 1 else None


def transports_for_launcher(path, data=None):
    """Every transport whose `launcher` is `path`, in registry order."""
    data = data if data is not None else load()
    norm = str(path or "").replace(chr(92), "/")
    return [name for name, cfg in (data.get("transports") or {}).items()
            if isinstance(cfg, dict) and cfg.get("launcher")
            and (norm == cfg["launcher"] or norm.endswith("/" + cfg["launcher"]))]


def plan_launchers(row, seat_plan, data=None):
    """[(plan, launcher)] for every live plan of `row`'s transport except `seat_plan`, which runs natively."""
    data = data if data is not None else load()
    transports_cfg = data.get("transports") or {}
    out = []
    for plan in plans_of(row["transport"], data):
        launcher = (transports_cfg.get(plan) or {}).get("launcher")
        if plan != seat_plan and launcher and _plan_live(plan, data):
            out.append((plan, launcher))
    return out


LADDER_RELPATH = Path(".claude") / "reference" / "model_ladder_evidence.md"

# The transport whose effort rungs the LADDER owns rather than this file.
HOST_ROLE_SOURCE = "anthropic"

# Ordered strongest-first. `validation` is deliberately absent: it is the `fanout` row at a lower
# effort pin, not a row of its own, and minting another tier would imply otherwise.
TIER_ORDER = ("orchestrator", "architect", "executor", "fanout", "scout")
_TIER_ALIASES = {"validation": "fanout"}

_LADDER_HEADING = "## Role guidance"
_LADDER_DEFINITIONS_HEADING = "## Role definitions"
LADDER_EVIDENCE_COLUMN = "evidence"
_LADDER_COLUMNS = ("model", "strengths", LADDER_EVIDENCE_COLUMN)
EVIDENCE_TAGS = ("pub", "bench", "use")
# A `model` cell opening with a pool name (`scout pool`, or `<pool> pool` for a registry `pool`) names no
# family itself; it covers each family its parentheses list, and each row carrying that pool.
SCOUT_POOL = "scout pool"


def ladder_path(start=None):
    return find_registry(start).parent.parent.parent / LADDER_RELPATH


def parse_ladder_rows(raw_lines, columns, heading=None):
    """One dict per data row of the ladder's Role guidance table, keyed by column NAME; [] on drift.

    The single reader for that table. Two parsers used to scan it -- this module's and
    `workflow_provider_guard.parse_ladder_role_lines` -- both anchored to the heading, both keyed by
    name, both hardened for the same positional-read incident, and a third column set would have
    been a third copy. Callers supply the columns they need and project the rows themselves.
    """
    heading = heading or _LADDER_HEADING
    columns = [c.lower() for c in columns]
    rows = []
    in_section = False
    idx = None
    for raw in raw_lines:
        if raw.startswith("## "):
            if in_section:
                break                               # the next section ends the table
            in_section = raw.strip() == heading
            continue
        if not in_section or not raw.startswith("| "):
            continue
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if idx is None:                             # the first table row is the header
            lowered = [c.lower() for c in cells]
            if not all(c in lowered for c in columns):
                return []                           # schema drifted; say nothing rather than guess
            idx = {c: lowered.index(c) for c in columns}
            continue
        if all(set(c) <= {"-", " ", ":"} for c in cells) or len(cells) <= max(idx.values()):
            continue
        rows.append({c: cells[idx[c]] for c in columns})
    return rows


def strengths_cell_problem(cell):
    """Why a strengths cell is malformed, else None. A cell describes the row's work in words; one
    that opens with a tier token restates a claim registry `roles` owns, and would drift from it."""
    rest = cell.strip()
    if rest.startswith("`"):
        token = rest[1:].partition("`")[0]
        if token in TIER_ORDER or _TIER_ALIASES.get(token) in TIER_ORDER:
            return (f"strengths cell {cell!r} opens with tier token `{token}`; tier claims live in "
                    f"registry `roles`, and the cell describes the row's work in words")
    return None


def evidence_problem(cell):
    """Why an `evidence` cell is malformed, else None. A cell is a nonempty `+`-joined subset of
    EVIDENCE_TAGS, in any order."""
    tags = [t.strip() for t in str(cell or "").split("+")]
    if not any(tags):
        return f"evidence cell is empty; name its sources from {'+'.join(EVIDENCE_TAGS)}"
    unknown = [t for t in tags if t not in EVIDENCE_TAGS]
    if unknown:
        return f"evidence tag(s) {', '.join(repr(t) for t in unknown)} not in {', '.join(EVIDENCE_TAGS)}"
    if len(tags) != len(set(tags)):
        return f"evidence cell {cell!r} repeats a tag"
    return None


def check_ladder(path=None, start=None, data=None):
    """Raise on a malformed strengths or evidence cell, a tier no ladder row serves, or a tier-claiming
    registry row no ladder row covers. The last holds on every transport; an owner claim
    (`effort.evidence: owner`) is exempt, since an owner claim is recorded in the registry."""
    path = path or ladder_path(start)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as err:
        raise RegistryError(f"cannot read the role ladder at {path}: {err}")
    rows = parse_ladder_rows(lines, _LADDER_COLUMNS)
    problems = [f"ladder row {row['model']!r}: {why}"
                for row in rows
                for why in (strengths_cell_problem(row["strengths"]), evidence_problem(row[LADDER_EVIDENCE_COLUMN]))
                if why]
    data = data if data is not None else load()
    role_tiers(path, data=data)
    covered = {m["id"] for row in rows for m in ladder_rows(row["model"], data)}
    for entry in data["models"]:
        held = sorted(set(entry_tiers(entry)) | set(supplementary_tiers(entry)), key=TIER_ORDER.index)
        if not held or (entry.get("effort") or {}).get("evidence") == "owner":
            continue
        if entry["id"] not in covered:
            problems.append(f"registry row {entry.get('alias') or entry['id']!r} claims "
                            f"{held} but no ladder row covers its family "
                            f"{entry.get('family')!r}")
    if problems:
        raise RegistryError(f"{path}: " + "; ".join(problems))


def pool_names(data=None):
    """The ladder `model` cell names that open with a pool: `scout pool`, plus `<pool> pool` for each
    registry row's `pool` (`free` gives `free pool`)."""
    data = data if data is not None else load()
    return {SCOUT_POOL} | {f"{m['pool']} pool" for m in data["models"] if m.get("pool")}


def _ladder_name(cell, data=None):
    """The name a ladder `model` cell shows: its pool name (`scout pool`, `free pool`), else its first
    word, backticks and notes dropped."""
    text = cell.replace("`", " ").strip()
    for name in sorted(pool_names(data), key=len, reverse=True):
        if text.lower().startswith(name):
            return name
    return text.split()[0] if text else ""


def ladder_members(cell, data=None):
    """The names a `model` cell covers: each family a pool cell lists in parentheses, else its name."""
    data = data if data is not None else load()
    name = _ladder_name(cell, data)
    if name not in pool_names(data):
        return [name] if name else []
    listed = cell.partition("(")[2].rpartition(")")[0]
    return [n.strip(" `") for n in listed.split(",") if n.strip(" `")]


def ladder_rows(cell, data=None):
    """Every registry row a ladder `model` cell covers: the rows of each family it names, on any
    transport, plus a row whose alias or id it names, plus each row carrying the cell's pool."""
    data = data if data is not None else load()
    names = set(ladder_members(cell, data))
    name = _ladder_name(cell, data)
    pool = name[:-len(" pool")] if name in pool_names(data) and name != SCOUT_POOL else None
    return [m for m in data["models"]
            if m.get("family") in names or m.get("alias") in names or m["id"] in names
            or (pool is not None and m.get("pool") == pool)]


def ladder_row_versions(path=None, start=None, data=None):
    """{ladder row name: sorted versions of the current registry rows it covers}: the keys the
    ladder-audit ledger records, so a family's version change can be seen against its last audit."""
    path = path or ladder_path(start)
    data = data if data is not None else load()
    with open(path, encoding="utf-8") as fh:
        rows = parse_ladder_rows(fh.readlines(), _LADDER_COLUMNS)
    return {_ladder_name(row["model"], data): sorted({str(m.get("version")) for m in ladder_rows(row["model"], data)
                                                      if m.get("lifecycle") == "current" and m.get("version")})
            for row in rows}


def ladder_cell_tiers(cell, data=None, supplementary=False):
    """The tiers the rows a ladder `model` cell covers claim in registry `roles`, in TIER_ORDER.
    `supplementary=True` adds the tiers those rows hold only as an added review seat."""
    claimed = {t for m in ladder_rows(cell, data)
               for t in entry_tiers(m) + (supplementary_tiers(m) if supplementary else [])}
    return [t for t in TIER_ORDER if t in claimed]


def ladder_cell_supplementary(cell, data=None):
    """The tiers the rows a ladder `model` cell covers list in registry `supplementary`, in TIER_ORDER."""
    held = {t for m in ladder_rows(cell, data) for t in supplementary_tiers(m)}
    return [t for t in TIER_ORDER if t in held]


def parse_role_tiers(raw_lines, data=None):
    """{tier: [ladder model name]}: each ladder row under every tier its covered registry rows claim.

    Registry `roles` is the only source of a claim; the ladder contributes which rows it covers.
    A ladder name that covers no registry row claims nothing.
    """
    data = data if data is not None else load()
    tiers = {}
    for row in parse_ladder_rows(raw_lines, _LADDER_COLUMNS):
        name = _ladder_name(row["model"], data)
        for tier in ladder_cell_tiers(row["model"], data):
            tiers.setdefault(tier, []).append(name)
    return tiers


def role_tiers(path=None, start=None, data=None):
    """parse_role_tiers over the live ladder. Raises rather than returning a partial map: a silent
    empty here would make every `for-role` answer look like 'no row serves this tier'."""
    path = path or ladder_path(start)
    try:
        with open(path, encoding="utf-8") as fh:
            tiers = parse_role_tiers(fh, data)
    except OSError as err:
        raise RegistryError(f"cannot read the role ladder at {path}: {err}")
    missing = [t for t in TIER_ORDER if t not in tiers]
    if missing:
        raise RegistryError(
            f"{path} has no ladder row claiming: {', '.join(missing)}. "
            f"A tier's rows are the ladder rows whose registry `roles` claim it (see the ladder's Role definitions)."
        )
    return tiers


def ladder_slice(tiers, path=None, data=None):
    """The ladder a dispatcher needs to pin `tiers`: everything from `## Role definitions` on, with the Role
    guidance rows cut to those whose registry `roles` claim a named tier. Raises when no row claims one or the
    table schema drifted, so an empty slice never reads as a ladder with nothing to say."""
    tiers = {canonical_tier(t) for t in tiers}
    if not tiers:
        raise RegistryError("ladder-slice needs at least one tier")
    path = path or ladder_path()
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as err:
        raise RegistryError(f"cannot read the role ladder at {path}: {err}")
    data = data if data is not None else load()
    cells = [row["model"] for row in parse_ladder_rows(lines, ("model",))]
    keep = {c for c in cells if tiers & set(ladder_cell_tiers(c, data, supplementary=True))}
    if not cells or not keep:
        raise RegistryError(f"{path} has no Role guidance row claiming: {', '.join(sorted(tiers))}")
    out, section = [], None
    for raw in lines:
        if raw.startswith("## "):
            section = raw.strip()
        if section is None or (not out and section != _LADDER_DEFINITIONS_HEADING):
            continue
        if section == _LADDER_HEADING and raw.startswith("| "):
            first = raw.strip().strip("|").split("|")[0].strip()
            if first in cells and first not in keep:
                continue
        out.append(raw)
    if not out:
        raise RegistryError(f"{path} has no `{_LADDER_DEFINITIONS_HEADING}` section")
    return "\n".join(out) + "\n"


def canonical_tier(name):
    """`validation` folds onto `fanout`; anything else must be a tier name."""
    tier = _TIER_ALIASES.get(name, name)
    if tier not in TIER_ORDER:
        raise RegistryError(
            f"unknown role {name!r}; legal: {', '.join(TIER_ORDER)} "
            f"(plus `validation`, which resolves to the fanout rows at a lower effort pin)"
        )
    return tier


def entry_tiers(entry):
    """Tier tokens this registry row claims, in TIER_ORDER."""
    roles = set(entry.get("roles") or [])
    return [tier for tier in TIER_ORDER if tier in roles]


def supplementary_tiers(entry):
    """Tier tokens this row fills only as an added review seat beside a qualified seat, in TIER_ORDER.
    A seat constraint, never a claim: `roles` stays the claim (ladder §Role definitions)."""
    held = set(entry.get("supplementary") or [])
    return [tier for tier in TIER_ORDER if tier in held]


def seat_standing(entry, tier):
    """A row's standing for a review seat at `tier`: "qualified" (in `roles`, not in `supplementary`),
    "supplementary" (in `supplementary`: admitted beside a qualified seat only), or None."""
    if tier in supplementary_tiers(entry):
        return "supplementary"
    return "qualified" if tier in entry_tiers(entry) else None


# ------------------------------------------------------------ probation
#
# Definition: `orchestration` §5. The owner mark (`probation`) is a hand edit by the owner, or by an
# agent carrying the owner's quoted words; the computed half of probation is never stored.


def _validate_probation(marks, where):
    """`probation` is optional; each mark names a tier from TIER_ORDER, why, and its evidence."""
    if marks is None:
        return
    if not isinstance(marks, list):
        raise RegistryError(f"{where} probation must be an array")
    for mark in marks:
        if (not isinstance(mark, dict) or mark.get("tier") not in TIER_ORDER
                or not str(mark.get("text") or "").strip() or not str(mark.get("evidenceRef") or "").strip()):
            raise RegistryError(
                f"{where} probation entries need 'tier' ({', '.join(TIER_ORDER)}) and non-empty 'text' "
                f"and 'evidenceRef' - an owner mark without its evidence cannot be reviewed or lifted")


def metrics_archive():
    """This project's rated-dispatch archive (`/orchestration_metrics`)."""
    return Path(__file__).resolve().parents[1] / "orchestration_metrics.jsonl"


def _known_row(model, data):
    row = row_for_model(model, data)
    if row is None:
        raise UnknownModel(f"unknown model {model!r}")
    return row


def _clean_at(row, tier, data, archive_path):
    """True when an archive line rates a dispatch of `row` at `tier` `clean`. Unreadable = none."""
    same = {}
    try:
        with open(archive_path, encoding="utf-8") as fh:
            for line in fh:
                if '"clean"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("outcome") != "clean" or _TIER_ALIASES.get(rec.get("tier"), rec.get("tier")) != tier:
                    continue
                name = str(rec.get("model") or "")
                if name not in same:
                    same[name] = (row_for_model(name, data) or {}).get("id") == row["id"]
                if same[name]:
                    return True
    except OSError:
        return False
    return False


def probation(model, tier, data=None, archive_path=None):
    """The basis on which `model` is on probation for `tier`, or None when it is off.

    In order: an owner mark for the tier; a `roles` claim (off); a clean-rated archive dispatch of
    the same registry row at the tier (off); otherwise the unclaimed basis. The archive is read only
    in the third step. Raises UnknownModel for a model the registry does not know and RegistryError
    for an unknown tier.
    """
    data = data or load()
    tier = canonical_tier(tier)
    row = _known_row(model, data)
    for mark in row.get("probation") or []:
        if isinstance(mark, dict) and mark.get("tier") == tier:
            return f"owner mark: {mark.get('text')}"
    if tier in entry_tiers(row):
        return None
    if _clean_at(row, tier, data, archive_path or metrics_archive()):
        return None
    return f"no {tier} claim in registry roles and no clean-rated {tier} dispatch"


def row_tier_claims(model, data=None):
    """The tiers a model's registry row claims; [] for a name the registry does not know."""
    try:
        return entry_tiers(resolve(model, data))
    except UnknownModel:
        return []


def ladder_costs(data=None, path=None):
    """{registry id: ladder `cost` (int, higher = cheaper) or None}: each ladder row's cost cell, applied to every
    registry row that ladder row covers. {} when the ladder is unreadable or its cost column is gone."""
    data = data if data is not None else load()
    try:
        with open(path or ladder_path(), encoding="utf-8") as fh:
            rows = parse_ladder_rows(fh, ("model", "cost"))
    except OSError:
        return {}
    out = {}
    for row in rows:
        value = row["cost"].strip()
        for m in ladder_rows(row["model"], data):
            out.setdefault(m["id"], int(value) if value.isdigit() else None)
    return out


def ladder_cost(entry, data=None, costs=None):
    """One registry row's ladder cost (higher = cheaper), else None."""
    return (costs if costs is not None else ladder_costs(data)).get(entry["id"])


def cheapest_first(rows, data=None, costs=None):
    """`rows` ordered by ladder cost, cheapest first; an unknown cost goes last; ties keep their order.

    For after-the-fact review (`orchestration_metrics.pricier_pins`) and for a pin no judgment separates
    (`workflow_provider_guard` fills an omitted `args.checker` with the first row). Never a listing shown to an
    agent choosing a pin: a listing sorted by one axis reads as the order to pick by."""
    costs = costs if costs is not None else ladder_costs(data)
    return sorted(rows, key=lambda m: (costs.get(m["id"]) is None, -(costs.get(m["id"]) or 0)))


def rows_for_tier(tier, data=None, seat=None):
    """Every dispatchable row claiming `tier` through registry `roles`, on any transport.

    Availability is seat-aware when a seat is supplied; otherwise it is seat-blind. A row may
    claim more than one tier, and two rows on the same transport may claim the same tier.
    """
    data = data or load()
    pool = available_models(data) if seat is None else [
        m for m in data["models"] if dispatchable(m, seat, data)]
    return [m for m in pool if tier in entry_tiers(m)]


def supplementary_rows(tier, data=None, seat=None):
    """Every dispatchable row holding `tier` in registry `supplementary`, on any transport, with the
    availability reading `rows_for_tier` takes. A row also in `roles` appears here and in `rows_for_tier`."""
    data = data or load()
    pool = available_models(data) if seat is None else [
        m for m in data["models"] if dispatchable(m, seat, data)]
    return [m for m in pool if tier in supplementary_tiers(m)]


def _sidecar_line(m, data, launcher=None):
    """Hop recipe; placeholders leave model judgment and effort with the ladder and caller. `launcher`
    names the paying plan's script; the default is the row's own transport's."""
    launcher = launcher or (data["transports"].get(m["transport"]) or {}).get("launcher")
    if not launcher:
        return f"(no launcher registered for transport {m['transport']} -- cannot hop)"
    return (f"bash {launcher} -m {m['alias']} -e <effort-from-ladder> "
            "-D <disclosure> -G <shape> -f <prompt-file> -R <record.json> "
            "-P <progress.jsonl> -l <label> -d <repo-root>")


def for_role(role, seat_plan, data=None):
    """{tier, seat, inTransport, crossTransport, otherPlans, nearest, note, supplementary} -- a REPORT, never a pin.

    `seat_plan` is the plan paying for this session; its route's rows are in-transport. `otherPlans`
    holds (row, plan, launcher) for each other live plan that can pay a serving row, except a
    cross-transport row's own transport, whose launcher that row's hop line already names.

    Deliberately never collapses to one row. A single sorted answer reads as 'the' model and lets a
    seat quietly downgrade itself to stay in-transport, which is the exact failure this exists to
    remove: capability picks the row, and the hop is printed rather than weighed for the reader.
    Vendor ids remain the only legal pin on a provider session -- nothing here resolves one.
    """
    data = data or load()
    tier = canonical_tier(role)
    transport = route_of(seat_plan, data)
    serving = rows_for_tier(tier, data, seat=transport)
    inside = [m for m in serving if m["transport"] == transport]
    outside = [m for m in serving if m["transport"] != transport]
    other_plans = [(m, plan, launcher) for m in serving
                   for plan, launcher in plan_launchers(m, seat_plan, data)
                   if m["transport"] == transport or plan != m["transport"]]

    nearest = []
    if not inside:
        here = TIER_ORDER.index(tier)
        # Distance first, then the STRONGER side on a tie. `abs()` alone let a tier one rung weaker
        # win over one equally close and stronger, purely on TIER_ORDER position -- a silent
        # downgrade, which is the failure this whole function exists to make visible.
        for other in sorted(TIER_ORDER,
                            key=lambda t: (abs(TIER_ORDER.index(t) - here),
                                           TIER_ORDER.index(t) - here)):
            if other == tier:
                continue
            found = [m for m in rows_for_tier(other, data, seat=transport)
                     if m["transport"] == transport]
            if found:
                delta = TIER_ORDER.index(other) - here      # >0 == a weaker tier
                nearest = [(m, other, delta) for m in found]
                break

    note = None
    if role in _TIER_ALIASES:
        note = (f"`{role}` is not a row -- it is the `{tier}` row at a LOWER effort pin "
                f"(orchestration section 5). Pin the same model, drop the rung.")
    return {"tier": tier, "seat": seat_plan, "inTransport": inside, "crossTransport": outside,
            "otherPlans": other_plans, "nearest": nearest, "note": note,
            "supplementary": supplementary_rows(tier, data, seat=transport)}


def open_tier_model(transport, data=None):
    """The cheap default a degraded consumer falls back to."""
    for entry in models_for(transport, data):
        if entry["authTier"] == "open":
            return entry["id"]
    return None


def transport_for(model_id, data=None):
    return resolve(model_id, data)["transport"]


def transport_status(transport, data=None):
    """The transport's availability, defaulted so an entry with no status stays usable.

    Returns the status dict with `state` always present. Callers gate on `state == "suspended"`
    and report `reason` + `routeInstead`; a suspension the dispatcher cannot explain gets
    rationalised away as a glitch and retried.
    """
    data = data if data is not None else load()
    cfg = (data.get("transports") or {}).get(transport)
    if cfg is None:
        raise RegistryError(f"unknown transport {transport!r}")
    status = dict(cfg.get("status") or {})
    status.setdefault("state", "active")
    status.setdefault("costModel", cfg.get("costModel", "unknown"))
    return status


def transport_suspended(transport, data=None):
    return transport_status(transport, data)["state"] == "suspended"


def model_available(model_id, data=None):
    """Is this model selectable? False when its own status or its transport's says unavailable."""
    entry = resolve(model_id, data)
    if (entry.get("status") or {}).get("state") == "unavailable":
        return False
    return transport_status(entry["transport"], data)["state"] != "unavailable"


def status_scope(entry):
    """`all` (default) or `sidecar` -- what an unavailable row's exclusion covers."""
    return (entry.get("status") or {}).get("scope") or "all"


def dispatchable(entry, seat, data=None):
    """Can `seat` dispatch to this row? Availability gates DISPATCH, never launching a session model.

    Launching a model as the session's own driver goes through a launcher or profile function, not
    the roster, so an excluded row stays launchable and only stops being a delegation target. A
    `sidecar`-scoped exclusion is about the HOP: it does not apply when the row's transport is the
    seat's own.
    """
    if model_available(entry["id"], data):
        return True
    return status_scope(entry) == "sidecar" and entry["transport"] == seat


def available_models(data=None):
    """The models a dispatcher may choose from, unavailable ones removed.

    This is the whole mechanism. An excluded model is absent from the set rather than present with a
    warning and a redirect, because a redirect makes a routing decision on the dispatcher's behalf
    without knowing the task's breadth, its complexity, or the budget — and an entry that must be
    read and then discarded costs attention on every dispatch.
    """
    data = data if data is not None else load()
    return [m for m in data["models"] if model_available(m["id"], data)]


def unavailable_reasons(data=None):
    """{model id: reason} for the excluded ones. For explaining a refusal, never for routing."""
    data = data if data is not None else load()
    out = {}
    for m in data["models"]:
        if model_available(m["id"], data):
            continue
        own = (m.get("status") or {}).get("reason")
        out[m["id"]] = own or transport_status(m["transport"], data).get("reason", "unavailable")
    return out


def set_transport_state(transport, state, path=None, data=None, reason=None):
    """Flip a transport between available and unavailable, in place.

    A one-command operation rather than hand-editing JSON: an orchestrator should never have to
    reason about the file's shape to act on a decision that has already been made, and a hand edit is
    where the schema gets violated in a way only the next dispatch discovers.

    `reason` is required to go unavailable and is what `_validate` enforces on the whole document.
    Without the parameter this function could only ever set `available`: every attempt at the state
    it exists to set died in the validator, on a field the caller had no way to pass.
    """
    if state not in _LEGAL_TRANSPORT_STATES:
        raise RegistryError(f"state must be one of {', '.join(sorted(_LEGAL_TRANSPORT_STATES))}")
    p = Path(path) if path else Path(find_registry())
    doc = json.loads(p.read_text(encoding="utf-8"))
    cfg = (doc.get("transports") or {}).get(transport)
    if cfg is None:
        raise RegistryError(f"unknown transport {transport!r}")
    status = cfg.setdefault("status", {})
    # Checked against the ARGUMENT, not the stored document. Reading `status.get("reason")` let a
    # caller omit the reason whenever a stale one happened to be lying in the row, so whether the
    # contract bound depended on the registry's history rather than on the call.
    if state == "unavailable" and not (reason or "").strip():
        raise RegistryError(f"suspending {transport} needs a reason -- an exclusion an agent "
                            f"cannot read the cause of gets rationalised away and retried")
    status["state"] = state
    if reason:
        status["reason"] = reason
    _validate(doc, str(p))          # never write a document the loader would reject
    # newline="\n" explicitly: Windows text mode translates every \n to CRLF, which rewrites the
    # WHOLE registry on a one-field status flip and buries the flip in a 200-line diff.
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    # Drop the memoized copy. `load()` caches by path, so without this a process that flips the state
    # and then reads it back gets the PRE-flip value -- and every in-process consumer downstream
    # (the posture hook, the rails) would report a status the file no longer holds.
    _cache.pop(str(p), None)
    return cfg["status"]


def cost_model_for(transport, data=None):
    """Which currency the transport spends: 'marginal-usd' or 'plan-quota'."""
    data = data if data is not None else load()
    cfg = (data.get("transports") or {}).get(transport)
    if cfg is None:
        raise RegistryError(f"unknown transport {transport!r}")
    return cfg["costModel"]


def attestation_for(transport, data=None):
    """The transport's server-side model-attestation block, or None.

    Presence is the discriminator a void-check keys on, not the transport's name: a transport
    that declares this block is one whose own client-side identity field cannot be trusted,
    because something between the child and the model can rewrite the request. Absence means
    the ordinary rule applies -- the served-model field is authority.
    """
    data = data if data is not None else load()
    cfg = (data.get("transports") or {}).get(transport)
    if cfg is None:
        raise RegistryError(f"unknown transport {transport!r}")
    return cfg.get("modelAttestation")


def cache_write_split(total, cache_creation):
    """(5-minute, 1-hour) cache-write tokens for a run whose writes total `total`.

    `cache_creation` is the result's `usage.cache_creation` object. Its split applies only when it
    accounts for exactly `total`: that object covers the main loop, so a run with nested loops
    leaves a remainder it cannot split, and the whole total then bills at the 5-minute rate.
    """
    total = total or 0
    if isinstance(cache_creation, dict):
        five = cache_creation.get("ephemeral_5m_input_tokens") or 0
        hour = cache_creation.get("ephemeral_1h_input_tokens") or 0
        if hour and five + hour == total:
            return five, hour
    return total, 0


def long_context_tier(entry, data=None):
    """The long-context tier a request to `entry` bills under: the row's own `longContextTier` when the
    row declares the key (null opts the row out of its transport's tier), else its transport's; None
    when neither has one. Every reader (pricing, listings, dispatch sizing) resolves through here."""
    if "longContextTier" in entry:
        return entry["longContextTier"]
    data = data or load()
    return (data["transports"].get(entry["transport"]) or {}).get("longContextTier")


def tier_phrase(tier, entry=None):
    """`tier` as the one clause a dispatcher reads: 'x5 past 100k input tokens per request', plus where a child of
    `entry` compacts when its row sets `limits.compactAtTokens`; '' for None."""
    if not tier:
        return ""
    at = ((entry or {}).get("limits") or {}).get("compactAtTokens")
    m = tier["multipliers"]
    fmt = lambda x: f"x{x:g}"
    rates = (fmt(m["input"]) if m["input"] == m["output"] == m.get("cachedInput", m["input"])
             else f"input {fmt(m['input'])}, output {fmt(m['output'])}")
    n = tier["thresholdInputTokens"]
    size = f"{n // 1000}k" if n % 1000 == 0 else f"{n:,}"
    return f"{rates} past {size} input tokens per request" + (f"; compacts at {at // 1000}k" if at else "")


def _validate_long_context_tier(tier, where):
    """A tier is a positive threshold, numeric input/output multipliers (cachedInput optional) and its
    source and date; a row may also set null to opt out of its transport's tier."""
    def bad(why):
        raise RegistryError(f"{where}: longContextTier {why}")
    if not isinstance(tier, dict):
        bad("must be an object (a row may set null to opt out of its transport's tier)")
    n = tier.get("thresholdInputTokens")
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        bad("thresholdInputTokens must be a positive integer")
    m = tier.get("multipliers")
    if not isinstance(m, dict) or any(
            k in m and (isinstance(m[k], bool) or not isinstance(m[k], (int, float)) or m[k] < 1)
            for k in ("input", "output", "cachedInput")) or not all(k in m for k in ("input", "output")):
        bad("multipliers must hold numeric 'input' and 'output' (and optional 'cachedInput'), each at least 1")
    if not (isinstance(tier.get("asOf"), str) and (tier.get("source") or tier.get("sources"))):
        bad("must name its 'source' (or 'sources') and 'asOf'")


def price_run(model_id, fresh, cache_read, output, data=None, at=None, cache_write=0,
              cache_write_1h=0, request_input=None, multiplier=None):
    """The ONE cost formula: tokens times the vendor's list price. Every consumer calls this; nobody
    re-authors a rate. It is the same axis whether the transport bills per token or spends a plan
    quota; which currency a run spends is `cost_model_for`'s answer, not this function's.

    `cache_write` tokens bill at the row's 5-minute write rate (`cacheWrite5mPer1M`), else its single
    vendor rate (`cacheWritePer1M`), else at its miss rate: a row that lists no write rate charges a
    write as fresh input. `cache_write_1h` tokens bill at `cacheWrite1hPer1M` by the same fallback.

    `request_input` is the input-token count of the one request being priced. Past the
    `longContextTier.thresholdInputTokens` (long_context_tier), input, cache-read and cache-write bill at the tier's
    `input` multiplier (cache reads at `cachedInput`) and output at its `output` multiplier.
    The tier is per request: a caller holding per-request usage prices
    each request separately; one holding only run totals omits `request_input` and gets base rates.

    Raises on a row with no `price` (a `priceGap` row names its reason) rather than returning 0.0: a
    zero would be indistinguishable from a real free call.

    `at` is the moment the run started; its billing window (price_window) scales every rate.
    A run that crosses a window boundary is priced at its start window. `multiplier` replaces that
    lookup with a window multiplier already known (a run record's `priceMultiplier`; 1.0 prices at
    the row's list rates).
    """
    data = data or load()
    entry = resolve(model_id, data)
    price = entry.get("price")
    if not isinstance(price, dict):
        gap = entry.get("priceGap")
        why = f"; its priceGap says: {gap.get('reason')}" if isinstance(gap, dict) else ""
        raise RegistryError(f"{model_id} carries no list price{why}")
    transport = entry["transport"]
    mult = multiplier if multiplier is not None else price_window(transport, at=at, data=data)["multiplier"]
    miss = price["cacheMissPer1M"]
    write_5m = price.get("cacheWrite5mPer1M", price.get("cacheWritePer1M", miss))
    write_1h = price.get("cacheWrite1hPer1M", price.get("cacheWritePer1M", miss))
    in_mult = hit_mult = out_mult = 1.0
    tier = long_context_tier(entry, data)
    if tier and request_input is not None and request_input > tier["thresholdInputTokens"]:
        in_mult = tier["multipliers"]["input"]
        hit_mult = tier["multipliers"].get("cachedInput", in_mult)
        out_mult = tier["multipliers"]["output"]
    return mult * (
        in_mult * (float(fresh) * miss
                   + float(cache_write) * write_5m
                   + float(cache_write_1h) * write_1h)
        + hit_mult * float(cache_read) * price["cacheHitPer1M"]
        + out_mult * float(output) * price["outputPer1M"]
    ) / 1e6


def usage_events(text):
    """Objects from a JSON document, verbose event array or interleaved JSONL capture."""
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    events = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            events.append(row)
    return events


def request_usage(events):
    """Last usage per API message in Claude stream/progress or Workflow records.

    Result events hold run totals, not requests. They are never treated as a request.
    """
    import transcript_usage
    reduced = transcript_usage.reduce_records(
        event for event in events if isinstance(event.get('message'), dict)
        and isinstance(event['message'].get('usage'), dict))
    return [normalize_request(transcript_usage.call_usage(mid, rec))
            for mid, rec in reduced['calls']
            if (rec.get('message') or {}).get('model') != transcript_usage.SYNTHETIC_MODEL
            and isinstance((rec.get('message') or {}).get('usage'), dict)]


def normalize_request(usage):
    """Canonical token columns; transcript `cw` includes its 1-hour share."""
    if 'fresh' in usage:
        return dict(usage)
    if 'input_tokens' in usage or 'output_tokens' in usage:
        five, hour = cache_write_split(usage.get('cache_creation_input_tokens'), usage.get('cache_creation'))
        return dict(id=usage.get('id'), model=usage.get('model'), fresh=usage.get('input_tokens'),
                    cache_read=usage.get('cache_read_input_tokens') or 0, cache_write=five,
                    cache_write_1h=hour, output=usage.get('output_tokens'))
    total = usage.get('cw') or 0
    hour = min(usage.get('cw1h') or 0, total)
    return dict(id=usage.get('id'), model=usage.get('model'), fresh=usage.get('inp'),
                cache_read=usage.get('cr') or 0, cache_write=total - hour,
                cache_write_1h=hour, output=usage.get('out'))


def price_requests(model_id, requests, data=None, at=None, multiplier=None, part=None):
    """Price deduped request usage; `part` splits USD without changing the full prompt size.

    Accept canonical columns or transcript_usage.call_usage dicts. Last dict wins for a repeated
    id; id-less dicts are distinct requests. Input and output must be known, never inferred as zero.
    """
    data = data if data is not None else load()
    if multiplier is None:
        multiplier = price_window(resolve(model_id, data)['transport'], at=at, data=data)['multiplier']
    deduped = {}
    for i, usage in enumerate(requests):
        req = normalize_request(usage)
        deduped[('id', req['id']) if req.get('id') else ('row', i)] = req
    total = 0.0
    for req in deduped.values():
        counts = {k: req.get(k, 0) for k in ('fresh', 'cache_read', 'cache_write', 'cache_write_1h', 'output')}
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in counts.values()):
            raise RegistryError('request usage has missing or invalid token counts')
        prompt = req.get('request_input', sum(counts[k] for k in ('fresh', 'cache_read', 'cache_write', 'cache_write_1h')))
        if part is not None:
            counts = {k: v if k == part or (part == 'cache_write' and k == 'cache_write_1h') else 0
                      for k, v in counts.items()}
        total += price_run(model_id, counts.pop('fresh'), counts.pop('cache_read'), counts.pop('output'),
                           data=data, at=at, multiplier=multiplier, request_input=prompt, **counts)
    return total


def _sibling_usage_paths(path):
    path = Path(path)
    name = path.name
    for suffix in ('.record.json.out', '.record.json', '.out.json', '.progress.jsonl', '.stream.jsonl', '.raw.json', '.stream'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    candidates = [path.with_name(name + suffix) for suffix in
                  ('.progress.jsonl', '.stream.jsonl', '.record.json.out', '.stream', '.raw.json')]
    candidates.append(Path(str(path) + '.out'))
    return list(dict.fromkeys(candidates))


def sibling_requests(path):
    """Partial stream requests for input with no client session identity."""
    for candidate in _sibling_usage_paths(path):
        if candidate.is_file():
            requests = request_usage(usage_events(candidate.read_text(encoding='utf-8', errors='replace')))
            if requests:
                return requests, str(candidate)
    return None, None


def transport_projects_root(transport, data=None):
    """The paying transport's transcript root, not the current session's login."""
    config = transport_meta(transport, data=data) if transport else None
    return projects_dir((config or {}).get('configDir'))


_TRANSCRIPT_INDEX = {}


def _transcript_matches(root, name):
    """Sorted `<root>/*/<name>` files. One directory scan per root serves every lookup in the process (a
    repricing pass looks up hundreds of sessions); a miss rescans once, so a transcript written since is found."""
    for rescan in (False, True):
        index = _TRANSCRIPT_INDEX.get(root)
        if index is None or rescan:
            index = _TRANSCRIPT_INDEX[root] = {}
            try:
                with os.scandir(root) as projects:
                    for project in projects:
                        if project.is_dir():
                            with os.scandir(project.path) as files:
                                for entry in files:
                                    if entry.name.endswith('.jsonl'):
                                        index.setdefault(entry.name, []).append(Path(entry.path))
            except OSError:
                pass
        found = sorted(path for path in index.get(name, ()) if path.is_file())
        if found:
            return found
    return []


def session_transcript(session_id, transport, data=None):
    """Find one client transcript by session id on its transport's projects root."""
    if not session_id or Path(str(session_id)).name != session_id:
        return None
    root = Path(transport_projects_root(transport, data=data))
    matches = _transcript_matches(root, session_id + '.jsonl')
    if len(matches) > 1:
        raise RegistryError(f'ambiguous transcript for session {session_id}: {matches}')
    return matches[0] if matches else None


_REQUEST_COUNTS = ('fresh', 'cache_read', 'cache_write', 'cache_write_1h', 'output')
_RECORD_COUNTS = dict(fresh='inputTokens', cache_read='cacheReadTokens',
                      cache_write='cacheWriteTokens', output='outputTokens')


def _stream_events(path, events=None):
    """Request and boundary evidence from captured output plus reachable siblings."""
    rows = list(events or [])
    source = None
    for candidate in _sibling_usage_paths(path):
        if candidate.is_file():
            captured = usage_events(candidate.read_text(encoding='utf-8', errors='replace'))
            if captured:
                rows.extend(captured)
                if request_usage(captured) or any(e.get('subtype') == 'compact_boundary' for e in captured):
                    source = str(candidate)
                    break
    return rows, source


def _subagent_requests(transcript):
    """Requests of the session's Agent and Workflow subagents, each on its own served model.

    The result event's modelUsage, which the record's totals come from, counts them with the
    driver's; their transcripts sit under `<session>/subagents/` (Workflow agents one level
    deeper, in `workflows/<runId>/`). Left out, their tokens fall into the driver's remainder
    and are priced at the driver's rate.
    """
    out = []
    for agent in sorted(Path(transcript).with_suffix('').glob('subagents/**/agent-*.jsonl')):
        out += request_usage(usage_events(agent.read_text(encoding='utf-8', errors='replace')))
    return out


def _missed_final_requests(driver_requests, remainder):
    """How many trailing driver requests the record's totals omit, when the negative remainder is exactly
    their sum (a stopped run's result can drop its last served request); 0 for any other gap."""
    gap = {kind: -count for kind, count in remainder.items()}
    if any(count < 0 for count in gap.values()):
        return 0
    tail = dict.fromkeys(gap, 0)
    for n, req in enumerate(reversed(driver_requests), 1):
        for kind in tail:
            tail[kind] += req[kind] + (req['cache_write_1h'] if kind == 'cache_write' else 0)
        if tail == gap:
            return n
        if any(tail[kind] > gap[kind] for kind in tail):
            return 0
    return 0


def record_requests(record, path, data=None, events=None):
    """Final client requests plus the aggregate remainder, with explicit estimate labels.

    The record's four token kinds are authoritative totals. Its optional 1h cache split can
    be stale; final request splits win, and an inconsistent remainder split is flagged.
    Mixed/unknown compaction tiers use a labelled base-rate estimate, never a guessed tier.
    """
    data = data if data is not None else load()
    streams, stream_source = _stream_events(path, events)
    transcript = session_transcript(record.get('sessionId'), record.get('transport'), data=data)
    requests = request_usage(usage_events(transcript.read_text(encoding='utf-8'))) if transcript else []
    driver_count = len(requests)
    if transcript:
        requests += _subagent_requests(transcript)
    usage_basis = 'transcript-final' if requests else 'stream-partial-output'
    source = str(transcript) if requests else stream_source or str(path)
    if not requests:
        requests = request_usage(streams)
    view = dict(requests=requests or None, costUsageSource=source,
                costUsageBasis=usage_basis, costTierBasis='untiered', costCompactionBasis='none')
    if not requests:
        return view
    pinned = record_model_id(record, token=record.get('requestedModel'), data=data) if record.get('attestedModel') else None
    route = route_of(record.get('transport'), data)
    for req in requests:
        if any(isinstance(req.get(k), bool) or not isinstance(req.get(k), (int, float))
               or req[k] < 0 for k in _REQUEST_COUNTS):
            raise RegistryError('request usage has missing or invalid token counts')
        row = row_by_id(req.get('model'), data, transport=route)
        req['model'] = pinned or (row or {}).get('id') or req.get('model') or record.get('requestedModel')
    sums = {k: sum(req[k] for req in requests) for k in _REQUEST_COUNTS}
    sums['cache_write'] += sums['cache_write_1h']
    remainder = {}
    for kind, field in _RECORD_COUNTS.items():
        count = record.get(field, 0 if kind in ('cache_read', 'cache_write') else None)
        if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0:
            raise RegistryError(f'invalid aggregate {field}: {count!r}')
        remainder[kind] = count - sums[kind]
    negative = {k: v for k, v in remainder.items() if v < 0}
    missed = _missed_final_requests(requests[:driver_count], remainder) if negative else 0
    if negative and not missed:
        raise RegistryError(f'negative remainder (aggregate minus final requests): {negative}')
    if missed:
        remainder = dict.fromkeys(remainder, 0)
    basis = 'per-request' + (' (stream-partial-output)' if usage_basis != 'transcript-final' else '')
    if missed:
        basis += f' (aggregate missed the final {missed} request(s))'
    if any(remainder.values()):
        models = {req['model'] for req in requests[:driver_count]} or {req['model'] for req in requests}
        if len(models) != 1:
            raise RegistryError('compaction remainder cannot be assigned across several served models')
        model = next(iter(models))
        row = row_for_model(model, data)
        if row is None:
            raise RegistryError(f'model {model!r} is not in the registry')
        tier = long_context_tier(row, data)
        boundaries = {e.get('uuid') or json.dumps(e, sort_keys=True): e for e in streams
                      if e.get('subtype') == 'compact_boundary'}
        prompts = [(e.get('compact_metadata') or {}).get('pre_tokens') for e in boundaries.values()]
        known = bool(prompts) and all(type(n) in (int, float) and n >= 0 for n in prompts)
        compaction = 'mixed/unknown-base-estimate'
        request_input = 0
        if not tier:
            compaction = 'base'
        elif known:
            past = [n > tier['thresholdInputTokens'] for n in prompts]
            if all(past):
                compaction, request_input = 'tier', max(prompts)
            elif not any(past):
                compaction = 'base'
        hour = record.get('cacheWrite1hTokens')
        remaining_hour = hour - sums['cache_write_1h'] if type(hour) in (int, float) else None
        if remaining_hour is None or not 0 <= remaining_hour <= remainder['cache_write']:
            remaining_hour = 0
            view['costCacheWriteBasis'] = 'remainder-5m-estimate (record 1h split absent/inconsistent)'
        else:
            view['costCacheWriteBasis'] = 'final requests + aggregate remainder split'
        requests.append(dict(remainder, id=None, model=model, request_input=request_input,
                             cache_write=remainder['cache_write'] - remaining_hour,
                             cache_write_1h=remaining_hour))
        view['costCompactionBasis'] = compaction
        basis += '+compaction-' + compaction
    view['costTierBasis'] = basis
    return view


def tier_basis_known(basis):
    """Whether every priced token's tier is known, including compaction remainders."""
    return str(basis).startswith('per-request') and not any(
        marker in str(basis) for marker in ('partial', 'unknown', 'estimate'))


_reprice_cache = {}


def _reprice_stamp(record, path, data):
    """(size, mtime) of every file `record_requests` reads for this record, or None when one cannot be stat'd."""
    transcript = session_transcript(record.get('sessionId'), record.get('transport'), data=data)
    files = [Path(path)] + [p for p in _sibling_usage_paths(path) if p.is_file()]
    if transcript:
        files += [Path(transcript)] + sorted(Path(transcript).with_suffix('').glob('subagents/**/agent-*.jsonl'))
    try:
        return tuple((str(p), st.st_size, st.st_mtime_ns) for p in files for st in [p.stat()] if p.is_file())
    except OSError:
        return None


def reprice_record(record, path, data=None, events=None):
    """In-memory final-request pricing; old ledgers keep their spend/list semantics.

    A repeat call on an unchanged record, registry and source set reuses the parse: a rating
    collection reprices every record, and each parse re-reads the transcript and its subagents.
    Caller-held `events` have no file stamp, so they always reprice.
    """
    data = data if data is not None else load()
    if events is not None:
        return _reprice_record(record, path, data, events)
    try:
        stamp = _reprice_stamp(record, path, data)
    except (RegistryError, OSError, ValueError):
        return _reprice_record(record, path, data, events)
    if stamp is None:
        return _reprice_record(record, path, data, events)
    key = (str(path), json.dumps(record, sort_keys=True, default=str), id(data))
    hit = _reprice_cache.get(key)
    if hit and hit[0] is data and hit[1] == stamp:
        return dict(hit[2])
    rec = _reprice_record(record, path, data, events)
    if len(_reprice_cache) >= 256:
        _reprice_cache.clear()
    _reprice_cache[key] = (data, stamp, rec)
    return dict(rec)


def _reprice_record(record, path, data, events):
    rec = dict(record)
    rec.setdefault('costTierBasis', 'untiered')
    try:
        view = record_requests(record, path, data=data, events=events)
        requests = view.pop('requests')
        if not requests:
            return rec
        rec.update(view)
        groups = {}
        for req in requests:
            groups.setdefault(req['model'], []).append(req)
        priced = [list_price(model, requests=calls, data=data,
                             multiplier=rec.get('priceMultiplier') if rec.get('priceMultiplier') is not None else 1.0)
                  for model, calls in groups.items()]
        rec['costUSD'] = sum(usd for usd, _ in priced) if all(usd is not None for usd, _ in priced) else None
        rec.pop('costPricingProblem', None)
        if rec['costUSD'] is None:
            rec['costPricingProblem'] = '; '.join(problem for _, problem in priced if problem)
    except (RegistryError, OSError, ValueError) as exc:
        rec.update(costUSD=None, costTierBasis='invalid', costPricingProblem=str(exc))
    return rec


def list_price(model, fresh=0, cache_read=0, output=0, cache_write=0, cache_write_1h=0,
               multiplier=None, data=None, requests=None, request_input=None, part=None):
    """(usd, None) for a payload model string `row_for_model` resolves, priced by `price_run`; else
    (None, the reason it cannot be priced)."""
    data = data if data is not None else load()
    row = row_for_model(model, data)
    if row is None:
        return None, f"model {model!r} is not in the registry"
    try:
        if requests is not None:
            return price_requests(row['id'], requests, data=data, multiplier=multiplier, part=part), None
        return price_run(row["id"], fresh, cache_read, output, data=data, cache_write=cache_write,
                         cache_write_1h=cache_write_1h, multiplier=multiplier, request_input=request_input), None
    except RegistryError as exc:
        return None, f"UNPRICED: {exc}"


# ----------------------------------------------------------------------------- CLI

def _rail_treatment_hashes(repo):
    """sha256 of each file a rail tier's treatment is made of, keyed relative to `.claude/`."""
    base = Path(repo) / ".claude"
    files = sorted((base / "guards").glob("*.md")) + [base / "workflows" / "dispatch.js"]
    out = {}
    for f in files:
        if f.is_file():
            out[f.relative_to(base).as_posix()] = hashlib.sha256(
                f.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return out


def _matrix_evidence_problems(matrix, mid, rail, effort):
    """Why an S4 matrix (`matrix.json`) does not carry `rail` for `mid` at `effort`; [] when it does.

    The model must have a necessity row at this effort; every one NEEDED or WEAK needs a `carriage:<rail>` row for
    the same probe, and every carriage row for a probe (a rerun repeats the probe) must be CARRIED with runs.
    """
    rows = matrix.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return ["rows is not a list of records"]
    bad = [] if str(matrix.get("scorerSha") or "").strip() else ["scorerSha is missing"]
    mine = [r for r in rows if r.get("model") == mid and r.get("effort") == effort]
    carriage = {}
    for r in mine:
        if r.get("stage") == f"carriage:{rail}":
            carriage.setdefault(r.get("probe"), []).append(r)
    needed = sorted({r.get("probe") for r in mine
                     if r.get("stage") == "necessity" and r.get("verdict") in ("NEEDED", "WEAK")})
    if not any(r.get("stage") == "necessity" for r in mine):
        return bad + [f"no necessity row for {mid} at effort {effort!r}"]
    if not carriage:
        return bad + [f"no carriage:{rail} row for {mid} at effort {effort!r}"]
    for probe in needed:
        if probe not in carriage:
            bad.append(f"{probe} is NEEDED/WEAK but has no carriage:{rail} row")
    for probe, probe_rows in sorted(carriage.items()):
        for row in probe_rows:
            if row.get("verdict") != "CARRIED":
                bad.append(f"{probe} carriage verdict {row.get('verdict')!r} != 'CARRIED'")
            if not row.get("runs"):
                bad.append(f"{probe} carriage row has no runs")
    return bad


def rail_evidence_problems(data, repo):
    """(errors, stale) for every measured railTierEvidence: errors fail --check, stale only warns.

    evidenceRef is a Phase B PASS report or an S4 `matrix.json`; a matrix carries no treatment hashes, so it is never stale.
    """
    errors, stale = [], []
    current = _rail_treatment_hashes(repo)
    for entry in data.get("models") or []:
        rail = entry.get("railTier")
        ev = entry.get("railTierEvidence") or {}
        if rail not in _SHORTER_RAIL_TIERS or ev.get("evidence") != "measured":
            continue
        mid = entry.get("id")
        ref = Path(ev["evidenceRef"])
        ref = ref if ref.is_absolute() else Path(repo) / ref
        try:
            report = json.loads(ref.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{mid}: railTierEvidence report unreadable at {ref}: {exc}")
            continue
        if "rows" in report:
            bad = _matrix_evidence_problems(report, mid, rail, ev.get("effort"))
            if bad:
                errors.append(f"{mid}: railTierEvidence matrix {ref} does not support this row: {'; '.join(bad)}")
            continue
        want = {"phase": "B", "modelId": mid, "tier": rail, "route": ev.get("route"),
                "effort": ev.get("effort"), "verdict": "PASS"}
        bad = [f"{k} {report.get(k)!r} != {v!r}" for k, v in want.items() if report.get(k) != v]
        if bad:
            errors.append(f"{mid}: railTierEvidence report {ref} does not support this row: {'; '.join(bad)}")
            continue
        recorded = report.get("treatmentHashes") or {}
        moved = sorted(k for k in current if recorded.get(k) != current[k])
        if moved:
            stale.append(f"{mid}: {', '.join(moved)} changed since the measured run - re-run /rail_battery")
    return errors, stale


def _cmd_check(argv):
    path = argv[0] if argv else None
    data = load(path)
    rail_errors, rail_stale = rail_evidence_problems(data, Path(__file__).resolve().parents[2])
    if rail_errors:
        raise RegistryError("; ".join(rail_errors))
    refresh_errors = refresh_pin_problems(data)
    if refresh_errors:
        raise RegistryError("; ".join(refresh_errors))
    check_ladder(data=data)
    for line in rail_stale:
        print(f"STALE railTierEvidence {line}", file=sys.stderr)
    warnings = stale_prices(data)
    print(f"registry OK: {len(data['models'])} models, {len(data['transports'])} transports")
    for mid, why in warnings:
        print(
            f"WARNING: {mid} price.asOf is {why} - re-probe "
            f"{resolve(mid, data)['price'].get('source', 'the vendor pricing page')}",
            file=sys.stderr,
        )
    return 0


def _cmd_resolve(argv):
    if not argv:
        raise RegistryError("resolve needs a model name")
    entry = resolve(argv[0])
    if len(argv) >= 3 and argv[1] == "--field":
        field = argv[2]
        if field not in entry:
            raise RegistryError(f"no field {field!r} on {entry['id']}; have: {', '.join(entry)}")
        value = entry[field]
        print(value if isinstance(value, str) else json.dumps(value))
    else:
        print(json.dumps(entry, indent=2))
    return 0


def _cmd_for_role(argv):
    """Rows serving a role tier, from this seat and across the hop. Reports; never resolves a pin."""
    # Strip `--from <name>` BEFORE the arity check: `for-role --from codex` (no role) used to pass
    # a check run on the unstripped list and then raise IndexError at args[0]. `main()` catches only
    # RegistryError, so the caller got a traceback instead of the usage line.
    seat = None
    argv = list(argv)
    seat_given = "--from" in argv
    if seat_given:
        i = argv.index("--from")
        if i + 1 >= len(argv):
            print("--from needs a transport name", file=sys.stderr)
            return 2
        seat = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(f"usage: for-role <{'|'.join(TIER_ORDER)}|validation> [--from <transport>]",
              file=sys.stderr)
        return 2
    data = load()
    if seat is None:
        sys.path.insert(0, str(find_registry().parent.parent / "hooks"))
        import _session_transport                                       # noqa: E402
        seat = _session_transport.session_plan()
    if seat not in data["transports"]:
        print(f"unknown transport {seat!r}; registered: {', '.join(transports(data))}",
              file=sys.stderr)
        return 2

    res = for_role(args[0], seat, data)
    try:
        tiers = role_tiers()
    except RegistryError:
        tiers = None
    heading = f"role `{res['tier']}`"
    if tiers:
        heading += f" -- ladder row(s): {', '.join(tiers[res['tier']])}"
    print(heading)
    print(f"seat: {seat}" + ("" if seat_given else "  (resolved from this session)"))
    if res["note"]:
        print(f"note: {res['note']}")
    print("Every row claims the tier; the order is the registry's, not a pick. Choose by the ladder's selection "
          "line: `±` fit and quality set the floor; then the plan, one whose quota goes unused at reset first "
          "([budget-posture]); then the cheapest fit row on that plan.")

    costs = ladder_costs(data)

    def describe(m, marked=None):
        # The phrase follows `effort.evidence`, the field that says WHERE the rung lives -- never
        # the transport. A rung printed from the row would be the ladder cell's second home, and
        # keying this on the host transport is what made a codex row read differently from an
        # Anthropic row serving the same tier.
        eff = m.get("effort") or {}
        evidence = eff.get("evidence")
        measured = evidence == "measured"
        rungs = "/".join(f"{k}={eff[k]}" for k in ("converged", "open") if measured and eff.get(k))
        if not rungs:
            rungs = ("per the ladder row" if evidence == "see-ladder"
                     else "owner-claimed" if evidence == "owner"
                     else "measured, rung unstated" if measured else "unmeasured")
        cost = (data["transports"].get(m["transport"]) or {}).get("costModel", "unstated")
        ladder = ladder_cost(m, costs=costs)
        cost = f"cost {ladder if ladder is not None else '?'} (ladder; higher = cheaper), {cost}"
        held_supp = set(supplementary_tiers(m))
        claims = ",".join(f"{r}(supplementary)" if r in held_supp else r
                          for r in m.get("roles") or []) or "-"
        if m["transport"] != "anthropic":
            pin = f"provider pin: {m['id']}"
        elif m.get("family") and m["alias"] != m["family"]:
            pin = f"host pin: {m['id']} (a versioned alias does not resolve natively)"
        else:
            pin = f"host pin: {m['alias']} or {m['id']}"
        tier = tier_phrase(long_context_tier(m, data), m)
        mark = next((k["text"] for k in m.get("probation") or []
                     if isinstance(k, dict) and k.get("tier") == marked), None)
        return (f"  {m['alias']:8s} {m['id']:22s} {m['transport']:10s} effort {rungs}; "
                f"{cost}; claims {claims}; {pin}" + (f"; long context: {tier}" if tier else "")
                + (f'; probation "{mark}"' if mark else ""))

    print("\nIN-TRANSPORT (no hop; use each row's native Workflow/Agent pin):")
    if res["inTransport"]:
        for m in res["inTransport"]:
            print(describe(m, res["tier"]))
    else:
        print(f"  (none -- no available {seat} row serves `{res['tier']}`)")

    only = [m for m in res["supplementary"] if res["tier"] not in (m.get("roles") or [])]
    if only:
        print("\nSUPPLEMENTARY (added review seats only: never lone, never author; seat beside a qualified "
              "seat that carries the same mandates):")
        for m in only:
            print(describe(m))

    print("\nCROSS-TRANSPORT (sidecar hop; spends the TARGET's currency, so read its band):")
    if res["crossTransport"] or res["otherPlans"]:
        for m in res["crossTransport"]:
            print(describe(m, res["tier"]))
            print(f"           {_sidecar_line(m, data)}")
        for m, plan, launcher in res["otherPlans"]:
            print(describe(m, res["tier"]) + f"  [paid by plan {plan}]")
            print(f"           {_sidecar_line(m, data, launcher)}")
    else:
        print("  (none -- every serving row is already on this seat)")

    if res["nearest"]:
        print("\nNEAREST IN-TRANSPORT (a DIFFERENT tier -- state the trade before taking it):")
        for m, other, delta in res["nearest"]:
            direction = "below" if delta > 0 else "above"
            plural = "" if abs(delta) == 1 else "s"
            print(describe(m) + f"  [`{other}`, {abs(delta)} tier{plural} {direction}]")
    return 0


def _cmd_price(argv):
    if len(argv) not in (4, 5, 6):
        raise RegistryError("price needs: <model> <fresh> <cache_read> <output> [cache_write] [cache_write_1h]")
    print(f"{price_run(argv[0], argv[1], argv[2], argv[3], cache_write=argv[4] if len(argv) > 4 else 0, cache_write_1h=argv[5] if len(argv) > 5 else 0):.6f}")
    print('untiered: base rates; no per-request usage', file=sys.stderr)
    return 0


def _cmd_band_satisfies(argv):
    if len(argv) != 2:
        raise RegistryError("band-satisfies needs: <current-band> <required-band>")
    return 0 if band_satisfies(argv[0], argv[1]) else 1


def _cmd_plan_for_launcher(argv):
    """`<plan>\t<expanded configDir>\t<1 when it is ~/.claude, else 0>\t<credentialVar>\t<expanded
    credentialFile>` for the launcher at argv[0]; exit 1 with no output when no single plan owns
    that script.

    The last two fields are the plan's credential SOURCE, empty for a login plan (whose credential
    is `<configDir>/.credentials.json`, which the launcher already knows how to find). The launcher
    reads the variable out of the file the way deepseek_sidecar.sh reads a provider key; printing
    the name and the path here keeps the registry the one place either is declared.
    """
    if len(argv) != 1:
        raise RegistryError("plan-for-launcher needs a launcher path")
    data = load()
    plan = plan_for_launcher(argv[0], data)
    if plan is None:
        return 1
    cfg = transport_meta(plan, data) or {}
    config_dir = cfg.get("configDir") or DEFAULT_LOGIN_DIR
    var = cfg.get("credentialVar")
    cred_file = cfg.get("credentialFile")
    print("\t".join((plan, os.path.normpath(expand_login_dir(config_dir)),
                     "1" if is_default_login(config_dir) else "0",
                     var if isinstance(var, str) else "",
                     os.path.normpath(expand_login_dir(cred_file)) if isinstance(cred_file, str) else "")))
    return 0


def _cmd_sidecar_fields(argv):
    """Everything deepseek_sidecar.sh needs, pipe-delimited, in ONE call.

    Python startup dominates here; five `resolve --field` calls would cost the
    common dispatch path most of a second for data that is one dict lookup.
    """
    if not argv or len(argv) not in (1, 3) or (len(argv) == 3 and argv[1] != "--plan"):
        raise RegistryError("sidecar-fields needs: <model> [--plan <plan>]")
    data = load()
    entry = resolve(argv[0], data)
    gate = entry["gate"]
    plan = argv[2] if len(argv) == 3 else entry["transport"]
    if plan not in data["transports"] or route_of(plan, data) != entry["transport"]:
        raise RegistryError(f"plan {plan!r} does not pay for {entry['id']} on route {entry['transport']}")
    transport = data["transports"][plan]
    plan_state = (transport.get("status") or {}).get("state")
    usable = model_available(entry["id"], data) and plan_state != "unavailable"
    reason = ("" if usable else (unavailable_reasons(data).get(entry["id"], "")
                                 or (transport.get("status") or {}).get("reason", "")))
    print("|".join(str(v) for v in (
        entry["id"],
        entry["alias"],
        entry["authTier"],
        gate["minBand"],
        gate.get("minBalanceUSD", ""),
        (entry.get("price") or {}).get("cacheMissPer1M", ""),
        "",  # retired balanceUrl slot; kept empty until every external consumer moves past v1
        # Appended rather than inserted so an older consumer reading only the first seven fields
        # keeps working. The suspension travels on THIS call because the sidecar already pays one
        # Python startup here and a second process spawn to ask "may I run at all" would double
        # the cost of the check on the common path.
        # With --plan, fields 8-10 are the paying plan's: its own suspension refuses the launch.
        "available" if usable else "unavailable",
        reason,
        # Fields 10-11: which currency this dispatch spends, and a slot that used to carry the
        # row's provider-band ceiling. That ceiling is retired (a plan-quota window refuses only on
        # exhaustion or an unreadable band), and the slot stays EMPTY rather than being dropped:
        # `opencode_sidecar.sh --check` reads apiMode as field 12 positionally, so removing a
        # column here would silently renumber it.
        transport["costModel"],
        "",
        # Field 12: which OpenAI-compatible call shape the backend actually answers on. Absent
        # (empty string) means the ordinary Chat Completions surface a launcher already assumes.
        entry.get("apiMode", ""),
    )))
    return 0


def _cmd_set_status(argv):
    if not 2 <= len(argv) <= 3:
        raise RegistryError("set-status needs: <transport> <available|unavailable> [reason] "
                            "-- a reason is required to go unavailable")
    status = set_transport_state(argv[0], argv[1], reason=argv[2] if len(argv) == 3 else None)
    print(json.dumps(status, indent=2))
    return 0


def _cmd_available(argv):
    """The selectable roster, and the excluded set with reasons. One call answers both."""
    data = load()
    avail = available_models(data)
    print("Every transport, host included — tier claims are registry `roles`; judgment and effort are "
          "reference/model_ladder_evidence.md.")
    print(f"available ({len(avail)}):" if avail else "available: none")
    # roles + effort + price together, because those are the three things a routing decision
    # needs and splitting them across three lookups is what makes doctrine restate them from
    # memory. `roles` is the agnostic capability answer: a model claiming a role IS the
    # off-quota route for that role, whatever the roster happens to hold today.
    for m in avail:
        eff = m.get("effort") or {}
        evid = eff.get("evidence") or "unstated"
        # Same phrase rule as `for-role`: the rung is named by the home `effort.evidence` points
        # at, so a `see-ladder` row says so here rather than printing "unstated".
        rungs = "/".join(
            f"{k}={eff[k]}" for k in ("converged", "open") if evid == "measured" and eff.get(k)
        ) or ("per the ladder row" if evid == "see-ladder"
              else "owner-claimed" if evid == "owner" else "unstated")
        avoid = ",".join(eff.get("avoid") or [])
        rate = (m.get("price") or {}).get("cacheMissPer1M")
        # The currency prints beside the rate because the two are not comparable and the
        # cheaper one flips with circumstance: a dollar rate is only meaningful against a
        # balance, a plan-quota row against an allowance that expires unused either way.
        # A plan-quota row's list rate is an internal weight (plan_usage_calc.py), never a price
        # this dispatch pays, so it prints no dollars.
        cost = data["transports"][m["transport"]]["costModel"]
        money = f"; ${rate}/1M in" if rate is not None and cost != "plan-quota" else ""
        monthly_cap = (m.get("planCapUSD") or {}).get("monthly")
        cap = (f"; monthly plan cap ${monthly_cap:g}"
               if cost == "plan-quota" and isinstance(monthly_cap, (int, float)) else "")
        supp = ",".join(supplementary_tiers(m))
        print(f"  {m['alias']:8s} {m['id']:20s} roles={','.join(m.get('roles') or [])}"
              + (f" supplementary={supp}" if supp else ""))
        hops = "; ".join(f"{plan} {launcher}" for plan, launcher in plan_launchers(m, None, data))
        tier = tier_phrase(long_context_tier(m, data), m)
        print(f"  {'':8s} effort {rungs} ({evid})" + (f"; avoid {avoid}" if avoid else "")
              + money + (f"; long context: {tier}" if tier else "") + f"; {cost}" + cap
              + (f"; launchers: {hops}" if hops else ""))
    gone = unavailable_reasons(data)
    if gone:
        print("excluded from selection (do not substitute by rule — re-select under the ladder):")
        for mid, why in gone.items():
            print(f"  {mid:20s} {why}")
    return 0


def _cmd_transport_meta(argv):
    if len(argv) != 1:
        raise RegistryError("transport-meta needs one transport name")
    meta = transport_meta(argv[0])
    if meta is None:
        raise RegistryError(f"unknown transport {argv[0]!r}")
    print(json.dumps(meta))
    return 0


def _cmd_transport_status(argv):
    """Availability of a transport, for consumers that have no model in hand.

    budget_posture.py needs this to describe routing without naming a model, and it must not
    re-parse the registry itself: a local copy of the suspension would drift from the gate that
    enforces it, which is the whole failure this registry exists to remove.
    """
    if not argv:
        raise RegistryError("transport-status needs a transport name")
    print(json.dumps(transport_status(argv[0]), indent=2))
    return 0


# A row with a long-context price step (long_context_tier) holds its input under this share of the step,
# the margin the codex rows already carry (effectiveContextPercent 95 of their 272K step).
TIER_ROOM_PERCENT = 95


def _effective_window(limits, use_max, tier=None):
    """The effective default window (`maxContextTokens` when `use_max`), or None when the row states none.

    The EFFECTIVE window is what the provider actually enforces, and it is derived here rather
    than authored as a third number: the selected window and effectiveContextPercent are the
    authored pair. Confirmed 2026-08-20 against the Codex CLI's own statusline, which reports a
    "258K window" for a model whose catalog contextTokens is 272000 -- 272000 x 0.95 exactly.
    Declaring the raw window would tell the client it has ~13.6K more room than the provider
    will accept.
    """
    window = limits.get("maxContextTokens" if use_max else "contextTokens")
    if use_max and not window and tier:
        window = limits.get("contextTokens")  # long context on a stepped row: its whole window, past the step
    if not window:
        return None
    percent = limits.get("effectiveContextPercent")
    if percent:
        window = int(int(window) * int(percent) / 100)
    if tier and not use_max:
        # Proxy launchers reserve their output cap; Anthropic's client-specific reserve and buffer
        # are owned by anthropic_window below. Keep this math for the non-Anthropic consumers.
        # The proxy window is the input room plus that cap (a quarter of the window, or the smaller maximum).
        # A row's `limits.compactAtTokens` is a chosen operating point (it may sit past the step: frequent
        # compaction costs re-reads and quality); without one the input stays under the step.
        room = int(limits.get("compactAtTokens") or tier["thresholdInputTokens"] * TIER_ROOM_PERCENT // 100)
        bound = room * OUTPUT_WINDOW_DIVISOR // (OUTPUT_WINDOW_DIVISOR - 1)
        cap = limits.get("maxOutputTokens")
        window = min(int(window), room + int(cap) if cap and int(cap) < bound - room else bound)
    return int(window)


def effective_window(entry, use_max=False):
    """The registry window a launcher declares (max under `use_max`), held under its price step.

    Proxy consumers reserve the output cap. Anthropic launchers use anthropic_window instead:
    Claude Code does not honor MAX_CONTEXT_TOKENS for Claude names. None means no stated window.
    """
    return _effective_window(entry.get("limits") or {}, use_max, _tier_of(entry))


def _tier_of(entry):
    return long_context_tier(entry) if "longContextTier" in entry or entry.get("transport") else None


def login_compact_window():
    """The Claude Code user settings' top-level `autoCompactWindow`: `$CLAUDE_CONFIG_DIR/settings.json`, else
    `~/.claude/settings.json`. None when the file is absent or unreadable or the value is not a usable window
    (an int in 100000..1000000), so no cap applies."""
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        with open(os.path.join(root, "settings.json"), encoding="utf-8") as fh:
            value = json.load(fh).get("autoCompactWindow")
    except (OSError, ValueError, AttributeError):
        return None
    return value if type(value) is int and 100000 <= value <= 1000000 else None


def anthropic_window(entry, use_max=False, max_output=None, compact_override=None, full_window=False):
    r"""Claude Code 2.1.291 window contract, extracted from claude.exe on 2026-10-08.

    Binary: `var OEt=20000`; `z4`: `Math.min(e.maxOutputTokens,OEt)` then `return g-r`;
    `p2`: `let r=e-13000`; `iv`: `window:Math.min(g,ke),configured:ke,source:"env"`.
    Hence compact input = min(client belief, AUTO_COMPACT_WINDOW) - min(output, 20000) - 13000.
    AUTO_COMPACT_WINDOW accepts 100000..1000000. MAX_CONTEXT_TOKENS is gated by dv(model),
    false for canonical claude-* names; `[1m]` makes fv(model) return 1e6 before that gate.
    The suffix is a client window opt-in, not a served-model or registry price-tier change:
    binary `On(e){return e.replace(/\[(1|2)m\]/gi,"")}` strips it for canonical identity.

    compactAtTokens is an input operating point, even with --max, and so is compact_override (-K),
    which replaces it. Without either, keep the registry's effective window.
    A Claude row between 200K and 1M cannot prove the client's belief: report unknown rather
    than its catalog window. Non-Claude names retain the existing MAX_CONTEXT_TOKENS contract.

    Every Claude row compacts at the smaller of that window and the login's `autoCompactWindow`
    (login_compact_window); an explicit compact_override (-K) and `full_window` are not capped. `contextWindow` is the enforced
    window (equal to `autoCompactWindow`), which the run record and its turn-1 percentage report.
    """
    limits = entry.get("limits") or {}
    model = entry["id"]
    window = effective_window(entry, use_max)
    if not model.lower().startswith("claude-"):
        login = None if full_window else login_compact_window()
        if window and login and login < window:
            return {"model": model, "autoCompactWindow": login, "maxContextTokens": window,
                    "contextWindow": login, "contextWindowSource": "anthropic-auto-compact"}
        return {"model": model, "autoCompactWindow": None, "maxContextTokens": window,
                "contextWindow": window, "contextWindowSource": "launcher-declared" if window else "unknown"}
    operating_point = compact_override if compact_override is not None else limits.get("compactAtTokens")
    if operating_point:
        cap = max_output if max_output is not None else output_cap(limits, use_max, _tier_of(entry))
        window = int(operating_point) + min(cap if cap is not None else 32000, 20000) + 13000
        if window > int(limits.get("contextTokens") or 0):
            raise RegistryError(f"{model}: compact point {operating_point} plus the client reserve exceeds contextTokens")
    if compact_override is None and not full_window and window is not None:
        login = login_compact_window()
        if login:
            window = min(window, login)
    if window is not None and not 100000 <= window <= 1000000:
        raise RegistryError(f"{model}: Claude Code auto-compact window must be 100000..1000000, got {window}"
                            + (f" (compact point {operating_point} plus the client reserve)" if operating_point else ""))
    native_1m = int(limits.get("contextTokens") or 0) >= 1000000
    if window and window > 200000 and native_1m and not model.lower().endswith("[1m]"):
        model += "[1m]"
    known = window is not None and (window <= 200000 or native_1m)
    return {"model": model, "autoCompactWindow": window, "maxContextTokens": None,
            "contextWindow": window if known else None,
            "contextWindowSource": "anthropic-auto-compact" if known else "unknown"}


def _cmd_anthropic_window(argv):
    flags = argv[1:]
    if len(flags) >= 2 and flags[-2] == "--shape":  # accepted for anthropic_sidecar.sh, which passes it; no effect
        flags = flags[:-2]
    if not argv or flags not in ([], ["--max"]):
        print("usage: model_registry.py anthropic-window <model> [--max] [--shape <shape>]", file=sys.stderr)
        return 2
    cap = os.environ.get("SIDECAR_MAX_OUTPUT_TOKENS", "")
    override = os.environ.get("SIDECAR_COMPACT_AT_TOKENS", "")
    if override and not override.isdigit():
        raise RegistryError("SIDECAR_COMPACT_AT_TOKENS must be a positive integer")
    print(json.dumps(anthropic_window(resolve(argv[0]), flags == ["--max"],
                                      int(cap) if cap.isdigit() else None,
                                      int(override) if override else None,
                                      os.environ.get("SIDECAR_FULL_WINDOW") == "1")))
    return 0


def _cmd_context_window(argv):
    """Print a model's effective default or max context window, if declared.

    Its own command rather than another `sidecar-fields` column: that contract is a
    pipe-delimited positional read, and appending to it silently pollutes the LAST variable of
    any reader still expecting the old count -- measured, with no error raised. A separate
    command cannot corrupt an existing caller.
    """
    if len(argv) not in (1, 2) or (len(argv) == 2 and argv[1] != "--max"):
        print("usage: model_registry.py context-window <model> [--max]", file=sys.stderr)
        return 2
    window = effective_window(resolve(argv[0]), len(argv) == 2)
    if window is None:
        return 0
    print(window)
    return 0


# Proxy consumers reserve the output cap; Anthropic's reserve is owned by anthropic_window.
# Bound the proxy cap at a quarter of its window so it cannot compact before 75%, the measured
# floor below which compaction read as a defect (codex_proxy_sidecar.sh: 70% on luna).
OUTPUT_WINDOW_DIVISOR = 4


def output_cap(limits, use_max=False, tier=None):
    """The output cap a child should run with: the model's own maximum, bounded by a quarter of its effective
    window; the maximum alone when no window is stated; None when no maximum is."""
    cap = limits.get("maxOutputTokens")
    if not cap:
        return None
    window = _effective_window(limits, use_max, tier)
    return int(cap) if window is None else min(int(cap), window // OUTPUT_WINDOW_DIVISOR)


def usable_window(entry, use_max=False):
    """Tokens a child may hold before its client compacts: the effective window less the output cap, the
    compaction reserve; None when the row states no window."""
    limits = entry.get("limits") or {}
    tier = _tier_of(entry)
    window = _effective_window(limits, use_max, tier)
    if window is None:
        return None
    return window - (output_cap(limits, use_max, tier) or 0)


CLIENT_OUTPUT_RESERVE = 20000
CLIENT_RESERVE = 13000


def compaction_point(entry, job=None):
    """`(tokens, source)` of the input a child on `entry` holds before its client compacts. With `job`, on
    any transport, it is `anthropic_window`'s `autoCompactWindow`, less the client
    reserves its docstring gives (source `auto-compact`). Without `job`, or for a row with no
    auto-compact window, it is `usable_window` (source `usable-window`); `(None, ...)` when the row states no window."""
    if job is not None:
        auto = anthropic_window(entry, full_window=job.get("window") == "full")
        if auto.get("autoCompactWindow"):
            cap = output_cap(entry.get("limits") or {}, False, _tier_of(entry))
            reserve = min(cap if cap is not None else 32000, CLIENT_OUTPUT_RESERVE) + CLIENT_RESERVE
            return auto["autoCompactWindow"] - reserve, "auto-compact"
    return usable_window(entry), "usable-window"


def _cmd_max_output(argv):
    """Print `output_cap`, or nothing when the registry states no maximum. A child left at the client's
    default (32000) loses a paid reply that outgrows it, reasoning included (2026-10-01: six comparator
    children)."""
    if len(argv) not in (1, 2) or (len(argv) == 2 and argv[1] != "--max"):
        print("usage: model_registry.py max-output <model> [--max]", file=sys.stderr)
        return 2
    entry = resolve(argv[0])
    cap = output_cap(entry.get("limits") or {}, len(argv) == 2, _tier_of(entry))
    if cap is not None:
        print(cap)
    return 0


def _cmd_rail_tier(argv):
    """Print the rail tier (`detailed|condensed|minimal`) a model string reads at."""
    if len(argv) != 1:
        print("usage: model_registry.py rail-tier <model>", file=sys.stderr)
        return 2
    print(rail_tier(argv[0]))
    return 0


def _cmd_price_window(argv):
    """The billing window a dispatch of <model> starts in now, and the rates it would pay."""
    if len(argv) != 1:
        print("usage: model_registry.py price-window <model>", file=sys.stderr)
        return 2
    data = load()
    entry = resolve(argv[0], data)
    win = price_window(entry["transport"], data=data)
    price = entry.get("price") or {}
    rates = {k: price[k] * win["multiplier"]
             for k in ("cacheHitPer1M", "cacheMissPer1M", "outputPer1M") if k in price}
    policy = (entry.get("gate") or {}).get("peakPolicy", "allow")
    print(json.dumps(dict(win, model=entry["id"], peakPolicy=policy, rates=rates)))
    return 0


def transport_effort_values(transport, data=None):
    """The effort vocabulary a TRANSPORT forwards; [] = undeclared (any rung is passed through).

    The one home for "which rungs exist on this transport": a row's own list is narrowed to it,
    and a caller that checks a rung without having a row in hand (a fan-out job names its own
    transport) checks the same list rather than a copy of it. `effortValues` when the transport
    declares one, else the proxy's pin vocabulary.
    """
    data = data if data is not None else load()
    tcfg = transport_meta(transport, data) or {}
    values = tcfg.get("effortValues") or (tcfg.get("serverSidePins") or {}).get("effortValues") or []
    return [v for v in values if isinstance(v, str) and v]


def effort_values(entry, data):
    """Rungs a dispatch of this row may request; [] = undeclared (the launcher accepts any).

    A row's own `effortValues` (the vendor's per-model list) wins, narrowed to the rungs its
    transport can pass through, so a rung the model serves but the launcher cannot forward is
    refused rather than silently dropped. `none` is the no-variant rung: the launcher sends no
    variant for it, so no transport lists it and the narrowing keeps it. The rung COUNT comes from
    the ladder cell, never from here: this is a vocabulary, not a recommendation.
    """
    own = entry.get("effortValues")
    if own is None:
        return transport_effort_values(entry["transport"], data)
    vocab = transport_effort_values(entry["transport"], data)
    served = [v for v in own if v == "none" or not vocab or v in vocab]
    if not served:
        raise RegistryError(f"model {entry['id']} declares no effort rung its transport can forward")
    return served


def _cmd_effort_values(argv):
    """The effort rungs <model>'s transport actually serves, as a JSON array; [] = undeclared.

    Its own command, not a sidecar-fields column, for the reason _cmd_context_window states.
    """
    if len(argv) != 1:
        print("usage: model_registry.py effort-values <model>", file=sys.stderr)
        return 2
    data = load()
    entry = resolve(argv[0], data)
    print(json.dumps(effort_values(entry, data)))
    return 0


def _cmd_effort_avoid(argv):
    """The evidence for <model>'s avoiding <rung>, or nothing when the row does not avoid it.

    Exit 0 either way: a launcher reads the line, and an empty one means the rung is not avoided.
    """
    if len(argv) != 2:
        print("usage: model_registry.py effort-avoid <model> <rung>", file=sys.stderr)
        return 2
    data = load()
    entry = resolve(argv[0], data)
    if argv[1] in avoided_rungs(entry):
        print(avoid_evidence(entry))
    return 0


def _cmd_review_state(argv):
    """`probation: <basis>` or `probation: none`."""
    if len(argv) != 2:
        print("usage: model_registry.py review-state <alias> <tier>", file=sys.stderr)
        return 2
    data = load()
    entry = resolve(argv[0], data)
    tier = canonical_tier(argv[1])
    print(f"probation: {probation(entry['id'], tier, data) or 'none'}")
    return 0


def _record_ladder_slice(tiers):
    """True when the covered tiers reached this session's compaction-scoped hook state."""
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        return False
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
    import _hook_state
    return _hook_state.mark_ladder_slice(session_id, tiers)


def _cmd_ladder_slice(argv):
    """The ladder cut to the named tiers' rows; the dispatch gate reads the tiers it recorded."""
    if not argv:
        print("usage: model_registry.py ladder-slice <tier> [<tier>...]", file=sys.stderr)
        return 2
    tiers = sorted({canonical_tier(t) for t in argv})
    text = ladder_slice(tiers)
    recorded = _record_ladder_slice(tiers)
    sys.stdout.write(text)
    if not recorded:
        print("ladder-slice: tiers not recorded for the dispatch gate (no CLAUDE_CODE_SESSION_ID, or the "
              "session state was unwritable); read the ladder in full instead", file=sys.stderr)
    return 0


_COMMANDS = {
    "--check": _cmd_check,
    "ladder-slice": _cmd_ladder_slice,
    "price-window": _cmd_price_window,
    "effort-values": _cmd_effort_values,
    "effort-avoid": _cmd_effort_avoid,
    "context-window": _cmd_context_window,
    "anthropic-window": _cmd_anthropic_window,
    "max-output": _cmd_max_output,
    "rail-tier": _cmd_rail_tier,
    "resolve": _cmd_resolve,
    "for-role": _cmd_for_role,
    "price": _cmd_price,
    "band-satisfies": _cmd_band_satisfies,
    "sidecar-fields": _cmd_sidecar_fields,
    "plan-for-launcher": _cmd_plan_for_launcher,
    "transport-meta": _cmd_transport_meta,
    "transport-status": _cmd_transport_status,
    "set-status": _cmd_set_status,
    "available": _cmd_available,
    "review-state": _cmd_review_state,
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(usage(__doc__))
        return 0
    handler = _COMMANDS.get(argv[0])
    if handler is None:
        print(f"unknown command {argv[0]!r}; try: {', '.join(_COMMANDS)}", file=sys.stderr)
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except RegistryError as err:
        print(str(err), file=sys.stderr)
        sys.exit(2)
