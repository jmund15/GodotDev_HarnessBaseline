#!/usr/bin/env python3
"""Which transport is THIS session running on? One home, read by every hook that branches on it.

Not a hook: a shared helper, sibling of `_model_tier.py`. Before this existed, three hooks each
re-derived the answer and each got it differently — `session_model_rails.py` matched a payload
model OR a base URL, `model_pin_translate.py` matched the base URL only, and
`workflow_provider_guard.py` never asked at all and assumed Anthropic. A codex session matched
none of them and was handed Anthropic rails telling it that no external model was reachable.

TWO SIGNALS, IN ORDER:

  1. `CLAUDE_CODE_TRANSPORT`, set by every launcher and profile function. This is the only signal
     that works for a LOOPBACK PROXY: the codex transport's endpoint is `http://127.0.0.1:<port>`,
     which carries no vendor name and whose port changes per session. A transport that cannot be
     identified from its URL must declare itself.
  2. The registry's `baseUrl` HOST, for transports that have one. Host, never substring: a URL
     mentioning a transport name in its path or query is not that transport, and a `in url` test
     matches the noun rather than the endpoint (`rules/harness_tooling.md`).

FAIL CLOSED. `unknown` is a return value, not an error, and callers DENY on it. An unreadable
registry also returns `unknown` — never `anthropic`. A hook that cannot read the roster must not
conclude the session is the permissive one; that is how a wrong pin ships as an affirmed decision.

The registry supplies endpoint/model and request-effort vocabulary; a launcher declares
loopback transport identity. Provider-specific branches do not belong here.
"""
import os
import sys
from urllib.parse import urlsplit

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

HOST_TRANSPORT = "anthropic"
UNKNOWN = "unknown"

# The registry's "anthropic" row carries no `baseUrl` (host transport, no proxy to record), so the
# baseUrl-host loop below can never match it. A session whose ANTHROPIC_BASE_URL IS the literal
# Anthropic API (not empty, not a sidecar proxy) fell through to UNKNOWN instead of matching itself
# -- denying every Workflow/Agent pin. Exact host only, so a lookalike (`....evil.test`) still fails
# closed.
ANTHROPIC_API_HOST = "api.anthropic.com"


def _registry():
    try:
        import model_registry
        return model_registry.load()
    except Exception:
        return None


def _host(url):
    """Hostname of a URL, lowercased, or None. `urlsplit` needs a scheme to populate
    `.hostname`, so a bare `host:port` is retried with one prepended."""
    if not url:
        return None
    try:
        parts = urlsplit(url if "//" in url else "//" + url, scheme="http")
        return (parts.hostname or "").lower() or None
    except Exception:
        return None


def resolve(env=None, data=None):
    """(transport, source). source is one of env | baseUrl | default | unmatched |
    registry-unreadable, and exists so a message can say HOW the session was identified —
    a deny that names its evidence is arguable; one that does not is a wall."""
    env = os.environ if env is None else env
    data = _registry() if data is None else data
    if not data:
        return UNKNOWN, "registry-unreadable"

    transports = data.get("transports") or {}

    declared = (env.get("CLAUDE_CODE_TRANSPORT") or "").strip().lower()
    if declared:
        # An unrecognized name fails closed rather than being trusted: a typo'd launcher export
        # would otherwise name a transport with no roster, and every pin would resolve against
        # an empty legal set.
        return (declared, "env") if declared in transports else (UNKNOWN, "unmatched")

    base = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    if not base:
        return HOST_TRANSPORT, "default"

    host = _host(base)
    if host == ANTHROPIC_API_HOST:
        return HOST_TRANSPORT, "baseUrl"

    if host:
        for name, cfg in transports.items():
            cfg_host = _host((cfg or {}).get("baseUrl"))
            if cfg_host and cfg_host == host:
                return name, "baseUrl"
    return UNKNOWN, "unmatched"


def _dispatchable_rows(transport, data, seat):
    """Rows on `transport` that `seat` may dispatch to, unavailable ones removed.

    `legal_model_ids` and `roster` MUST filter identically. Filtering only the first desyncs the
    guard's deny MESSAGE (built from the roster) from its deny BEHAVIOUR, so the refusal names the
    very pin it just refused -- the failure reads as a guard bug rather than an excluded model.
    """
    # Resolve the session seat; using the target transport disabled sidecar-scoped exclusions.
    seat = resolve(data=data)[0] if seat is None else seat
    rows = [m for m in (data.get("models") or []) if m.get("transport") == transport]
    try:
        import model_registry
    except Exception:
        return []           # Unknown availability cannot re-admit excluded models.
    out = []
    for m in rows:
        # Filter per row so one malformed row cannot re-admit every excluded model.
        try:
            if model_registry.dispatchable(m, seat, data):
                out.append(m)
        except Exception:
            continue
    return out


def legal_model_ids(transport, data=None, seat=None):
    """Every model id AND alias dispatchable on `transport`. Empty for `unknown`, which is what
    makes the fail-closed state deny every pin rather than accidentally allowing all of them.

    `seat` is the transport DOING the dispatching, resolved from the session against `data`
    when omitted; it only matters for a `sidecar`-scoped exclusion."""
    data = _registry() if data is None else data
    if not data or transport == UNKNOWN:
        return []
    out = []
    for m in _dispatchable_rows(transport, data, seat):
        out += [v for v in (m.get("id"), m.get("alias")) if v]
    return sorted(set(out))


def legal_effort_values(transport, data=None):
    """Ordered effort vocabulary declared by `transport`; empty when absent or unknown."""
    data = _registry() if data is None else data
    if not data or transport == UNKNOWN:
        return []
    cfg = (data.get("transports") or {}).get(transport) or {}
    if not isinstance(cfg, dict):
        return []
    values = (cfg.get("effortValues") if "effortValues" in cfg
              else (cfg.get("serverSidePins") or {}).get("effortValues", []))
    if not isinstance(values, list):
        return []
    out = []
    for value in values:
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def roster(transport, data=None, seat=None):
    """[(alias, id)] for `transport`, for a deny message that names what IS legal. Filters exactly
    as `legal_model_ids` does -- see `_dispatchable_rows`."""
    data = _registry() if data is None else data
    if not data or transport == UNKNOWN:
        return []
    return sorted((m.get("alias"), m.get("id"))
                  for m in _dispatchable_rows(transport, data, seat))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    t, s = resolve()
    print("%s\t%s" % (t, s))
