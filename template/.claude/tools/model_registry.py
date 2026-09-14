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
    model_registry.py for-role executor             rows serving a tier, in-transport and across
    model_registry.py for-role fanout --from codex  ...as seen from another seat
    model_registry.py price <id> <fresh> <cache_read> <output>
    model_registry.py band-satisfies <current> <required>   exit 0 yes, 1 no, 2 bad name
"""

import json
import os
import sys
from pathlib import Path

REGISTRY_RELPATH = Path(".claude") / "reference" / "external_models.json"
ENV_OVERRIDE = "HARNESS_MODEL_REGISTRY"

_REQUIRED_PRICE_FIELDS = ("cacheHitPer1M", "cacheMissPer1M", "outputPer1M", "currency", "asOf")
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

# Which OpenAI-compatible call shape a model's backend actually answers on. Absent means the
# ordinary Chat Completions surface (/v1/chat/completions) -- the default every OpenAI-compatible
# launcher already assumes. "responses" means the backend only answers on the newer Responses API
# (/v1/responses); a launcher reads this to choose litellm's "openai/responses/<id>" model prefix
# over the plain "openai/<id>" one. Discovered the hard way: muse-spark-1.3-contributor-free 500s
# instantly on every Chat Completions call regardless of client, headers, or payload shape, and
# answers cleanly the moment the call shape changes (verified 2026-09-04).
_LEGAL_API_MODES = {"responses"}


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

        # An UNMEASURED row may not claim a role. `roles` is a routing assertion and
        # `evidence: unmeasured` says nothing has been measured -- the two cannot both hold, and
        # a row carrying both would route real work on a claim its own effort block disowns.
        # A row's OWN alias in `roles` is a self-identity placeholder, not a tier claim -- the
        # opencode roster uses it that way by design. What needs evidence is a role naming
        # somebody ELSE's tier: that is the assertion that routes real work.
        effort = entry.get("effort") or {}
        foreign = [r for r in (entry.get("roles") or []) if r != alias]
        if effort.get("evidence") == "unmeasured" and foreign:
            raise RegistryError(
                f"{target}: model {mid} has effort.evidence 'unmeasured' but claims roles "
                f"{foreign!r} - a roles array naming another tier is a measured routing "
                f"assertion. Either score the row and record the evidence, or leave roles empty "
                f"(alias-only dispatch) / self-identity only")

        # A `limits` block must name where its numbers came from. First-party metadata
        # (~/.codex/models_cache.json, models.dev) is a fact; an unsourced number is a guess that
        # reads identically once it is in the file, and a wrong context window truncates silently.
        # Provenance may be recorded per row or once per transport (a whole roster refreshed
        # from one catalog documents itself at the transport level; repeating it per row would
        # be the duplication this file exists to avoid).
        limits = entry.get("limits")
        if isinstance(limits, dict) and any(
            limits.get(k) is not None for k in ("contextTokens", "maxContextTokens", "maxOutputTokens")
        ) and not (entry.get("_limitsSource") or entry.get("_limitsComment")
                   or transports[transport].get("_limitsSource")):
            raise RegistryError(
                f"{target}: model {mid} authors 'limits' values with no '_limitsSource' naming "
                f"where they came from - an unsourced window is a guess. Record it on the row, "
                f"or once on transport {transport} when one catalog sourced the whole roster")

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

        # `roles` must be PRESENT, but MAY be empty. Empty is a meaningful state, not an
        # omission: the row is dispatchable by explicit alias (-m terra) and never auto-selected
        # to fill a role. Requiring non-empty forced every registered id to assert a routing
        # equivalence, which is how an unmeasured row ends up claiming a tier it has never been
        # scored against -- the failure the sibling `evidence: unmeasured` check above rejects.
        roles = entry.get("roles")
        if not isinstance(roles, list):
            raise RegistryError(
                f"{target}: model {mid} must declare a 'roles' array (empty = alias-only dispatch)")
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
        # `see-ladder` is the third state, for a transport whose `roleSource` names an external
        # owner of role semantics (the host transport's rows). Recording measured/unmeasured here
        # would duplicate the ladder and drift from it; the row asserts dispatchability only.
        if not isinstance(effort, dict) or effort.get("evidence") not in ("measured", "unmeasured", "see-ladder"):
            raise RegistryError(
                f"{target}: model {mid} effort.evidence must be measured|unmeasured|see-ladder")
        if effort.get("evidence") == "see-ladder" and not transports[transport].get("roleSource"):
            raise RegistryError(
                f"{target}: model {mid} claims effort.evidence 'see-ladder' but transport "
                f"{transport} declares no 'roleSource' naming who owns those semantics")

        api_mode = entry.get("apiMode")
        if api_mode is not None and api_mode not in _LEGAL_API_MODES:
            raise RegistryError(
                f"{target}: model {mid} apiMode {api_mode!r} must be one of "
                f"{', '.join(sorted(_LEGAL_API_MODES))}")


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


def transports(data=None):
    """Every registered transport name, host included."""
    data = data or load()
    return sorted(data["transports"])


def transport_meta(name, data=None):
    """One transport's config row, or None. The resolver in hooks/_session_transport.py reads
    `baseUrl` from this to match a session endpoint, so a transport without one (codex's
    per-session loopback proxy, anthropic's direct connection) is identified by
    CLAUDE_CODE_TRANSPORT alone -- by construction, not by omission."""
    data = data or load()
    cfg = data["transports"].get(name)
    return dict(cfg) if isinstance(cfg, dict) else None


def role_map(transport, data=None):
    """{anthropic_role: model_id} for one transport."""
    out = {}
    for entry in models_for(transport, data):
        for role in entry["roles"]:
            out[role] = entry["id"]
    return out


LADDER_RELPATH = Path(".claude") / "reference" / "model_ladder_evidence.md"

# The transport whose effort rungs the LADDER owns rather than this file.
HOST_ROLE_SOURCE = "anthropic"

# Ordered strongest-first. `validation` is deliberately absent: it is the `fanout` row at a lower
# effort pin, not a row of its own, and minting a fifth tier would imply otherwise.
TIER_ORDER = ("orchestrator", "executor", "fanout", "scout")
_TIER_ALIASES = {"validation": "fanout"}

_LADDER_HEADING = "## Role guidance"
_LADDER_COLUMNS = ("model", "role")


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


def parse_role_tiers(raw_lines):
    """{tier: [ladder model name]} from the tier token each Anthropic role cell opens with.

    The ladder owns role SEMANTICS and the Anthropic-transport mapping (registry `_comment`), so the
    tokens are read from there and never mirrored into the registry.
    """
    tiers = {}
    for row in parse_ladder_rows(raw_lines, _LADDER_COLUMNS):
        role = row["role"]
        if not role.startswith("`"):
            continue                                # a row with no tier token claims no tier
        token = role[1:].split("`", 1)[0]
        if token in TIER_ORDER:
            tiers.setdefault(token, []).append(row["model"].strip())
    return tiers


def role_tiers(path=None, start=None):
    """parse_role_tiers over the live ladder. Raises rather than returning a partial map: a silent
    empty here would make every `for-role` answer look like 'no row serves this tier'."""
    path = path or ladder_path(start)
    try:
        with open(path, encoding="utf-8") as fh:
            tiers = parse_role_tiers(fh)
    except OSError as err:
        raise RegistryError(f"cannot read the role ladder at {path}: {err}")
    missing = [t for t in TIER_ORDER if t not in tiers]
    if missing:
        raise RegistryError(
            f"{path} has no ladder row claiming: {', '.join(missing)}. "
            f"Each tier's token opens the `role` cell of its row (see the ladder's Role definitions)."
        )
    return tiers


def canonical_tier(name):
    """`validation` folds onto `fanout`; anything else must be a tier name."""
    tier = _TIER_ALIASES.get(name, name)
    if tier not in TIER_ORDER:
        raise RegistryError(
            f"unknown role {name!r}; legal: {', '.join(TIER_ORDER)} "
            f"(plus `validation`, which resolves to the fanout rows at a lower effort pin)"
        )
    return tier


def rows_for_tier(tier, data=None, tiers=None, seat=None):
    """Every row `seat` may DISPATCH to that serves `tier`, on any transport.

    Two ways to serve one: BE the ladder row for the tier (the Anthropic alias itself), or claim
    that alias in `roles`. The second is the whole agnostic mechanism -- a row claiming `sonnet` IS
    the off-quota route for the fanout tier, whatever the roster holds today. `roles` values stay
    Anthropic aliases: workflow_provider_guard.claimed_roles keys its currency-deny dict by them,
    so a row claiming a TIER name instead would silently stop matching an Anthropic pin.

    Availability is asked SEAT-AWARE, through the same `dispatchable` predicate the provider guard
    uses. `available_models` is seat-blind, so a `status.scope: sidecar` row -- excluded for the hop
    but deliberately still pinnable from its own transport -- vanished from the role resolution of
    the one seat that may use it, while the guard went on allowing the pin. `seat=None` keeps the
    seat-blind answer for callers reporting on no particular session.
    """
    data = data or load()
    tiers = tiers if tiers is not None else role_tiers()
    wanted = set(tiers.get(tier) or [])
    pool = available_models(data) if seat is None else [
        m for m in data["models"] if dispatchable(m, seat, data)]
    hits = []
    for m in pool:
        if m.get("alias") in wanted or (set(m.get("roles") or []) & wanted):
            hits.append(m)
    return hits


def _ladder_effort(alias, path=None):
    """The ladder's effort rung for an Anthropic row, or None. One bare token only.

    The `effort` cell is prose ("xhigh for the hardest design"), and a launcher takes one rung, so
    anything past the first word is dropped rather than guessed at.
    """
    try:
        with open(path or ladder_path(), encoding="utf-8") as fh:
            for row in parse_ladder_rows(fh, ("model", "role", "effort")):
                if row["model"].strip().strip("`") == alias:
                    token = row["effort"].strip().strip("`").split()[0].strip("`.,")
                    return token if token in ("low", "medium", "high", "xhigh") else None
    except Exception:
        return None
    return None


def _sidecar_line(m, data):
    launcher = (data["transports"].get(m["transport"]) or {}).get("launcher")
    if not launcher:
        return f"(no launcher registered for transport {m['transport']} -- cannot hop)"
    eff = m.get("effort") or {}
    # An Anthropic row carries no `effort` block -- the LADDER owns its rung. Without the fallback
    # the printed hop command omitted `-e` entirely, so a copy-pasted Anthropic hop ran at the
    # launcher default while every provider hop beside it printed its pin.
    rung = eff.get("open") or eff.get("converged") or _ladder_effort(m["alias"])
    flag = f" -e {rung}" if rung else ""
    return f"bash {launcher} -m {m['alias']}{flag} -f <prompt-file> -R <record.json> -l <label>"


def for_role(role, transport, data=None, tiers=None):
    """{tier, seat, inTransport, crossTransport, nearest, note} -- a REPORT, never a pin.

    Deliberately never collapses to one row. A single sorted answer reads as 'the' model and lets a
    seat quietly downgrade itself to stay in-transport, which is the exact failure this exists to
    remove: capability picks the row, and the hop is printed rather than weighed for the reader.
    Vendor ids remain the only legal pin on a provider session -- nothing here resolves one.
    """
    data = data or load()
    tiers = tiers if tiers is not None else role_tiers()
    tier = canonical_tier(role)
    serving = rows_for_tier(tier, data, tiers, seat=transport)
    inside = [m for m in serving if m["transport"] == transport]
    outside = [m for m in serving if m["transport"] != transport]

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
            found = [m for m in rows_for_tier(other, data, tiers, seat=transport)
                     if m["transport"] == transport]
            if found:
                delta = TIER_ORDER.index(other) - here      # >0 == a weaker tier
                nearest = [(m, other, delta) for m in found]
                break

    note = None
    if role in _TIER_ALIASES:
        note = (f"`{role}` is not a row -- it is the `{tier}` row at a LOWER effort pin "
                f"(orchestration section 5). Pin the same model, drop the rung.")
    return {"tier": tier, "seat": transport, "inTransport": inside,
            "crossTransport": outside, "nearest": nearest, "note": note}


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


def _cmd_for_role(argv):
    """Rows serving a role tier, from this seat and across the hop. Reports; never resolves a pin."""
    # Strip `--from <name>` BEFORE the arity check: `for-role --from codex` (no role) used to pass
    # a check run on the unstripped list and then raise IndexError at args[0]. `main()` catches only
    # RegistryError, so the caller got a traceback instead of the usage line.
    seat = None
    argv = list(argv)
    if "--from" in argv:
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
        seat = _session_transport.resolve()[0]
    if seat not in data["transports"]:
        print(f"unknown transport {seat!r}; registered: {', '.join(transports(data))}",
              file=sys.stderr)
        return 2

    res = for_role(args[0], seat, data)
    tiers = role_tiers()
    print(f"role `{res['tier']}` -- ladder row(s): {', '.join(tiers[res['tier']])}")
    print(f"seat: {seat}" + ("  (resolved from this session)" if "--from" not in argv else ""))
    if res["note"]:
        print(f"note: {res['note']}")

    def describe(m):
        eff = m.get("effort") or {}
        rungs = "/".join(f"{k}={eff[k]}" for k in ("converged", "open") if eff.get(k))
        if not rungs:
            # The ladder owns every Anthropic row's effort cell; a bare "unstated" reads as a gap.
            rungs = "per the ladder row" if m["transport"] == HOST_ROLE_SOURCE else "unstated"
        cost = (data["transports"].get(m["transport"]) or {}).get("costModel", "unstated")
        claims = ",".join(m.get("roles") or []) or "-"
        return f"  {m['alias']:8s} {m['id']:22s} {m['transport']:10s} effort {rungs}; {cost}; claims {claims}"

    print("\nIN-TRANSPORT (Workflow/Agent pin, no hop):")
    if res["inTransport"]:
        for m in res["inTransport"]:
            print(describe(m))
    else:
        print(f"  (none -- no available {seat} row serves `{res['tier']}`)")

    print("\nCROSS-TRANSPORT (sidecar hop; spends the TARGET's currency, so read its band):")
    if res["crossTransport"]:
        for m in res["crossTransport"]:
            print(describe(m))
            print(f"           {_sidecar_line(m, data)}")
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
    # Scope stated up front: an empty list here means no EXTERNAL model is selectable, not that there
    # is nothing to dispatch to. The Anthropic rows are the ladder's, not this file's.
    print("Every transport, host included — Anthropic role SEMANTICS still resolve from reference/model_ladder_evidence.md.")
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
    "for-role": _cmd_for_role,
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
