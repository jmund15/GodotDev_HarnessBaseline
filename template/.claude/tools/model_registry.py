#!/usr/bin/env python3
"""Reader + validator for .claude/reference/external_models.json.

The registry is the SSOT for external-transport models: role resolution, prices,
limits, and authorization gates. This module is the ONE parser — hooks import it,
Bash and validate_commands.py call the CLI, PowerShell parses the JSON natively
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

Band names in `gate.minBand` / `gate.maxProviderBand` are validated and rank-compared
against quota_bands.BANDS by IMPORT. A local copy of the band list would drift from the
thresholds it gates on, which is the whole failure this registry removes.

The two band gates read in OPPOSITE directions and are not interchangeable:

  gate.minBand         FLOOR on THIS session's own pressure. A paid transport declares
                       `On pace` so it refuses to spend dollars at Surplus, where prepaid
                       quota is expiring unused.
  gate.maxProviderBand CEILING on THE PROVIDER's own pressure, for plan-quota transports.
                       A provider running Hot is the case to refuse — a floor would permit
                       dispatch precisely when its quota is most exhausted.

CLI:
    model_registry.py --check                       validate; exit 0 ok, 2 invalid
    model_registry.py resolve pro --field id        print one field; exit 0 / 2
    model_registry.py resolve pro                   print the whole entry as JSON
    model_registry.py role-map deepseek             print {role: id} as JSON
    model_registry.py price <id> <fresh> <cache_read> <output>
    model_registry.py band-satisfies <current> <required>   exit 0 yes, 1 no, 2 bad name
"""

import json
import os
import sys
from pathlib import Path

REGISTRY_RELPATH = Path(".claude") / "reference" / "external_models.json"
ENV_OVERRIDE = "PP_MODEL_REGISTRY"

_REQUIRED_PRICE_FIELDS = ("cacheHitPer1M", "cacheMissPer1M", "outputPer1M", "currency", "asOf")
_STALE_PRICE_DAYS = 90

# Availability gates the ROSTER, not the routing. An unavailable model is removed from the set a
# dispatcher chooses from; it is never paired with a substitute, because choosing the replacement is
# the dispatcher's job and depends on the task's shape and the budget — facts a config file does not
# have. `available` is the default, so an entry with no status resolves exactly as before.
#
# Declarable on a transport (gating every model it carries) or on a single model.
_LEGAL_TRANSPORT_STATES = {"available", "unavailable"}

# WHICH CURRENCY a transport spends, which is the axis suspension decisions turn on. `marginal-usd`
# bills real money per call; `plan-quota` draws on prepaid allowance that expires unused. The two
# are not comparable and the cheaper one flips with circumstance, so the cost model is recorded
# rather than inferred from price fields -- a plan-quota transport can carry per-token prices for
# accounting and still cost nothing marginal to run.
_LEGAL_COST_MODELS = {"marginal-usd", "plan-quota"}


class RegistryError(Exception):
    """Registry missing, unreadable, or violating a field contract."""


class UnknownModel(RegistryError):
    """Name matched no alias and no id."""


# --------------------------------------------------------------------------- bands

def _bands():
    """Import BANDS from the module that owns them. Never copy the list."""
    tools = Path(__file__).resolve().parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        from quota_bands import BANDS  # noqa: WPS433 - deliberate late import
    except Exception as exc:  # pragma: no cover - environment breakage
        raise RegistryError(f"cannot import quota_bands.BANDS (band SSOT): {exc}")
    return [name for _bound, name, _desc in BANDS]


def band_rank(name):
    """Index into the ascending-pressure band order. Raises on an unknown name."""
    names = _bands()
    if name not in names:
        raise RegistryError(f"unknown band {name!r}; legal bands: {', '.join(names)}")
    return names.index(name)


def band_satisfies(current, required):
    """FLOOR: true when `current` is at or above `required` burn rate. See module docstring."""
    return band_rank(current) >= band_rank(required)


def band_within_ceiling(current, ceiling):
    """CEILING: true when `current` is at or below `ceiling` burn rate. See module docstring."""
    return band_rank(current) <= band_rank(ceiling)


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
    _validate(data, target)
    _cache[key] = data
    return data


def _validate(data, target):
    if not isinstance(data, dict):
        raise RegistryError(f"{target}: top level must be an object")
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
        cost = tcfg.get("costModel")
        if cost not in _LEGAL_COST_MODELS:
            raise RegistryError(
                f"{target}: transport {tname} costModel {cost!r} must be one of "
                f"{', '.join(sorted(_LEGAL_COST_MODELS))}")
        # Required rather than optional now that a consumer branches on it. An absent
        # costModel used to be harmless because nothing read the field; once the sidecar
        # decides whether to run a dollar-balance check from it, "absent" would silently
        # pick one billing shape for a transport that never declared one.
        probe = tcfg.get("quotaProbe")
        if cost == "plan-quota":
            if not isinstance(probe, dict):
                raise RegistryError(
                    f"{target}: plan-quota transport {tname} needs a 'quotaProbe' object - "
                    f"its remaining allowance is not observable any other way")
            if not probe.get("command") or not isinstance(probe.get("args"), list):
                raise RegistryError(
                    f"{target}: transport {tname} quotaProbe needs a 'command' string "
                    f"and an 'args' array")
        elif probe is not None:
            raise RegistryError(
                f"{target}: transport {tname} is {cost} and must not declare a 'quotaProbe' - "
                f"a dollar-billed transport is gated on balance, not on a band")
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

    seen_names = {}
    roles_by_transport = {}
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

        for name in (mid, alias):
            if name in seen_names:
                raise RegistryError(f"{target}: duplicate id/alias {name!r}")
            seen_names[name] = mid

        # Price is required only where a call costs money. A plan-quota model has no
        # per-token rate to record, and demanding one would force invented numbers into the
        # SSOT -- the exact failure the file exists to prevent. It stays ALLOWED there,
        # because a plan-quota provider may publish rates for accounting while still costing
        # nothing marginal to run; if present it must be complete.
        cost_model = transports[transport].get("costModel")
        price = entry.get("price")
        if price is None and cost_model == "plan-quota":
            pass
        elif not isinstance(price, dict):
            raise RegistryError(f"{target}: model {mid} missing 'price' object")
        else:
            for field in _REQUIRED_PRICE_FIELDS:
                if field not in price:
                    raise RegistryError(f"{target}: model {mid} price missing {field!r}")

        roles = entry.get("roles")
        if not isinstance(roles, list) or not roles:
            raise RegistryError(f"{target}: model {mid} must declare a non-empty 'roles' array")
        claimed = roles_by_transport.setdefault(transport, {})
        for role in roles:
            if role in claimed:
                raise RegistryError(
                    f"{target}: role {role!r} claimed by both {claimed[role]} and {mid} "
                    f"on transport {transport!r} - role resolution would be ambiguous"
                )
            claimed[role] = mid

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

        max_provider = gate.get("maxProviderBand")
        if cost_model == "plan-quota":
            if not max_provider:
                raise RegistryError(
                    f"{target}: plan-quota model {mid} gate missing 'maxProviderBand' - "
                    f"without it nothing stops dispatch into an exhausted allowance")
            band_rank(max_provider)
        elif max_provider is not None:
            raise RegistryError(
                f"{target}: model {mid} is on a {cost_model} transport and must not declare "
                f"gate.maxProviderBand - there is no provider band to read")

        effort = entry.get("effort")
        if not isinstance(effort, dict) or effort.get("evidence") not in ("measured", "unmeasured"):
            raise RegistryError(f"{target}: model {mid} effort.evidence must be measured|unmeasured")


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

def resolve(name, data=None):
    """Accept an alias or a full id; return the model entry."""
    data = data or load()
    for entry in data["models"]:
        if name in (entry["id"], entry["alias"]):
            return entry
    legal = sorted({v for e in data["models"] for v in (e["id"], e["alias"])})
    raise UnknownModel(f"unknown model {name!r}; legal values: {', '.join(legal)}")


def models_for(transport, data=None):
    data = data or load()
    return [e for e in data["models"] if e["transport"] == transport]


def role_map(transport, data=None):
    """{anthropic_role: model_id} for one transport."""
    out = {}
    for entry in models_for(transport, data):
        for role in entry["roles"]:
            out[role] = entry["id"]
    return out


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


def set_transport_state(transport, state, path=None, data=None):
    """Flip a transport between available and unavailable, in place.

    A one-command operation rather than hand-editing JSON: an orchestrator should never have to
    reason about the file's shape to act on a decision that has already been made, and a hand edit is
    where the schema gets violated in a way only the next dispatch discovers.
    """
    if state not in _LEGAL_TRANSPORT_STATES:
        raise RegistryError(f"state must be one of {', '.join(sorted(_LEGAL_TRANSPORT_STATES))}")
    p = Path(path) if path else Path(find_registry())
    doc = json.loads(p.read_text(encoding="utf-8"))
    cfg = (doc.get("transports") or {}).get(transport)
    if cfg is None:
        raise RegistryError(f"unknown transport {transport!r}")
    cfg.setdefault("status", {})["state"] = state
    _validate(doc, str(p))          # never write a document the loader would reject
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
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


def quota_probe_for(transport, data=None):
    """{command, args} for a plan-quota transport's usage probe, or None.

    None means "this transport has no observable allowance", which for a marginal-usd
    transport is correct rather than a gap: its gate reads a dollar balance instead.
    """
    data = data if data is not None else load()
    cfg = (data.get("transports") or {}).get(transport)
    if cfg is None:
        raise RegistryError(f"unknown transport {transport!r}")
    return cfg.get("quotaProbe")


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


def price_run(model_id, fresh, cache_read, output, data=None):
    """The ONE cost formula. Every consumer calls this; nobody re-authors a rate.

    Raises on a priceless plan-quota model rather than returning 0.0: a zero would be
    indistinguishable from a real free call and would land in a run record's costUSD as a
    measured figure. The absence of a marginal cost is reported as `costUSD: null` by the
    record writer, which is a different claim from "this run cost nothing to make".
    """
    entry = resolve(model_id, data)
    price = entry.get("price")
    if not isinstance(price, dict):
        raise RegistryError(
            f"{model_id} carries no price - it is on a "
            f"{cost_model_for(entry['transport'], data)} transport; report costUSD as null")
    return (
        float(fresh) * price["cacheMissPer1M"] / 1e6
        + float(cache_read) * price["cacheHitPer1M"] / 1e6
        + float(output) * price["outputPer1M"] / 1e6
    )


# ----------------------------------------------------------------------------- CLI

def _cmd_check(argv):
    path = argv[0] if argv else None
    data = load(path)
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


def _cmd_role_map(argv):
    if not argv:
        raise RegistryError("role-map needs a transport name")
    print(json.dumps(role_map(argv[0]), indent=2))
    return 0


def _cmd_price(argv):
    if len(argv) != 4:
        raise RegistryError("price needs: <model> <fresh> <cache_read> <output>")
    print(f"{price_run(argv[0], argv[1], argv[2], argv[3]):.6f}")
    return 0


def _cmd_band_satisfies(argv):
    if len(argv) != 2:
        raise RegistryError("band-satisfies needs: <current-band> <required-band>")
    return 0 if band_satisfies(argv[0], argv[1]) else 1


def _cmd_band_within_ceiling(argv):
    """The CEILING comparison, as a CLI so a shell gate never re-implements it.

    A shell caller that imported this module instead would have to put a path on sys.path,
    and on Windows the natural one is an MSYS path that native Python cannot resolve — the
    import fails, the gate reads the nonzero exit as "over ceiling", and it refuses every
    dispatch including the ones it should pass.
    """
    if len(argv) != 2:
        raise RegistryError("band-within-ceiling needs: <current-band> <ceiling-band>")
    return 0 if band_within_ceiling(argv[0], argv[1]) else 1


def _cmd_sidecar_fields(argv):
    """Everything deepseek_sidecar.sh needs, pipe-delimited, in ONE call.

    Python startup dominates here; five `resolve --field` calls would cost the
    common dispatch path most of a second for data that is one dict lookup.
    """
    if not argv:
        raise RegistryError("sidecar-fields needs a model name")
    data = load()
    entry = resolve(argv[0], data)
    gate = entry["gate"]
    transport = data["transports"][entry["transport"]]
    print("|".join(str(v) for v in (
        entry["id"],
        entry["alias"],
        entry["authTier"],
        gate["minBand"],
        gate.get("minBalanceUSD", ""),
        (entry.get("price") or {}).get("cacheMissPer1M", ""),
        transport.get("balanceUrl", ""),
        # Appended rather than inserted so an older consumer reading only the first seven fields
        # keeps working. The suspension travels on THIS call because the sidecar already pays one
        # Python startup here and a second process spawn to ask "may I run at all" would double
        # the cost of the check on the common path.
        "available" if model_available(entry["id"], data) else "unavailable",
        (transport.get("status") or {}).get("reason", ""),
        # Fields 10-11: which currency this dispatch spends, and the provider-band ceiling
        # its own quota gate compares against. A launcher branches on field 10 to decide
        # whether a dollar-balance check runs at all; without it, a plan-quota dispatch
        # would look for a balance URL that does not exist and fail closed on nothing.
        transport["costModel"],
        gate.get("maxProviderBand", ""),
    )))
    return 0


def _cmd_set_status(argv):
    if len(argv) != 2:
        raise RegistryError("set-status needs: <transport> <available|unavailable>")
    status = set_transport_state(argv[0], argv[1])
    print(json.dumps(status, indent=2))
    return 0


def _cmd_available(argv):
    """The selectable roster, and the excluded set with reasons. One call answers both."""
    data = load()
    avail = available_models(data)
    # Scope stated up front: an empty list here means no EXTERNAL model is selectable, not that there
    # is nothing to dispatch to. The Anthropic rows are the ladder's, not this file's.
    print("EXTERNAL transports only — Anthropic roles resolve from reference/model_ladder_evidence.md.")
    print(f"available ({len(avail)}):" if avail else "available: none")
    # roles + effort + price together, because those are the three things a routing decision
    # needs and splitting them across three lookups is what makes doctrine restate them from
    # memory. `roles` is the agnostic capability answer: a model claiming a role IS the
    # off-quota route for that role, whatever the roster happens to hold today.
    for m in avail:
        eff = m.get("effort") or {}
        rungs = "/".join(
            f"{k}={eff[k]}" for k in ("converged", "open") if eff.get(k)
        ) or "unstated"
        avoid = ",".join(eff.get("avoid") or [])
        evid = eff.get("evidence") or "unstated"
        rate = (m.get("price") or {}).get("cacheMissPer1M")
        # The currency prints beside the rate because the two are not comparable and the
        # cheaper one flips with circumstance: a dollar rate is only meaningful against a
        # balance, a plan-quota row against an allowance that expires unused either way.
        cost = data["transports"][m["transport"]]["costModel"]
        money = f"; ${rate}/1M in" if rate is not None else ""
        print(f"  {m['alias']:8s} {m['id']:20s} roles={','.join(m.get('roles') or [])}")
        print(f"  {'':8s} effort {rungs} ({evid})" + (f"; avoid {avoid}" if avoid else "")
              + money + f"; {cost}")
    gone = unavailable_reasons(data)
    if gone:
        print("excluded from selection (do not substitute by rule — re-select under the ladder):")
        for mid, why in gone.items():
            print(f"  {mid:20s} {why}")
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


def _cmd_context_window(argv):
    """Print a model's declared context window, or nothing when the registry states none.

    Its own command rather than a 12th `sidecar-fields` column: that contract is a
    pipe-delimited positional read, and appending to it silently pollutes the LAST variable of
    any reader still expecting the old count -- measured, with no error raised. A separate
    command cannot corrupt an existing caller.
    """
    if len(argv) != 1:
        print("usage: model_registry.py context-window <model>", file=sys.stderr)
        return 2
    entry = resolve(argv[0])
    limits = entry.get("limits") or {}
    window = limits.get("contextTokens")
    if not window:
        return 0
    # The EFFECTIVE window is what the provider actually enforces, and it is derived here rather
    # than authored as a third number: contextTokens and effectiveContextPercent are the authored
    # pair. Confirmed 2026-08-20 against the Codex CLI's own statusline, which reports a "258K
    # window" for a model whose catalog contextTokens is 272000 -- 272000 x 0.95 exactly.
    # Declaring the raw window would tell the client it has ~13.6K more room than the provider
    # will accept.
    percent = limits.get("effectiveContextPercent")
    if percent:
        window = int(int(window) * int(percent) / 100)
    print(int(window))
    return 0


_COMMANDS = {
    "--check": _cmd_check,
    "context-window": _cmd_context_window,
    "resolve": _cmd_resolve,
    "role-map": _cmd_role_map,
    "price": _cmd_price,
    "band-satisfies": _cmd_band_satisfies,
    "band-within-ceiling": _cmd_band_within_ceiling,
    "sidecar-fields": _cmd_sidecar_fields,
    "transport-status": _cmd_transport_status,
    "set-status": _cmd_set_status,
    "available": _cmd_available,
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
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
