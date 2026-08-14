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

Band names in `gate.minBand` are validated and rank-compared against
budget_posture.BANDS by IMPORT. A local copy of the band list would drift from the
thresholds it gates on, which is the whole failure this registry removes.

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


class RegistryError(Exception):
    """Registry missing, unreadable, or violating a field contract."""


class UnknownModel(RegistryError):
    """Name matched no alias and no id."""


# --------------------------------------------------------------------------- bands

def _bands():
    """Import BANDS from the hook that owns them. Never copy the list."""
    hooks = Path(__file__).resolve().parents[1] / "hooks"
    if str(hooks) not in sys.path:
        sys.path.insert(0, str(hooks))
    try:
        from budget_posture import BANDS  # noqa: WPS433 - deliberate late import
    except Exception as exc:  # pragma: no cover - environment breakage
        raise RegistryError(f"cannot import budget_posture.BANDS (band SSOT): {exc}")
    return [name for _bound, name, _desc in BANDS]


def band_rank(name):
    """Index into the ascending-pressure band order. Raises on an unknown name."""
    names = _bands()
    if name not in names:
        raise RegistryError(f"unknown band {name!r}; legal bands: {', '.join(names)}")
    return names.index(name)


def band_satisfies(current, required):
    """True when `current` is at or above `required` in band order."""
    return band_rank(current) >= band_rank(required)


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

        price = entry.get("price")
        if not isinstance(price, dict):
            raise RegistryError(f"{target}: model {mid} missing 'price' object")
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
        as_of = entry["price"].get("asOf")
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


def price_run(model_id, fresh, cache_read, output, data=None):
    """The ONE cost formula. Every consumer calls this; nobody re-authors a rate."""
    price = resolve(model_id, data)["price"]
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
        entry["price"]["cacheMissPer1M"],
        transport.get("balanceUrl", ""),
    )))
    return 0


_COMMANDS = {
    "--check": _cmd_check,
    "resolve": _cmd_resolve,
    "role-map": _cmd_role_map,
    "price": _cmd_price,
    "band-satisfies": _cmd_band_satisfies,
    "sidecar-fields": _cmd_sidecar_fields,
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
