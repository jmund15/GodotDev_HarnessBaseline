#!/usr/bin/env python3
"""Read every plan-quota transport's OWN band, and compare it against this session's.

Three currencies exist, and two of them expire unused: this session's Anthropic plan quota,
a provider's plan quota (Codex), and real dollars (DeepSeek). Plan-quota-vs-plan-quota is a
BAND COMPARISON, not a spend decision -- neither side bills marginally, so the question is
only which allowance is being wasted faster. That comparison needs every plan-quota
transport's own band, which only its `quotaProbe` can supply.

Lives here rather than inside workflow_provider_guard.py because the hook is not the only
caller that needs it: the pre-dispatch checklist asks the same question for a sidecar
invocation, which no PreToolUse hook sees.

FAIL POSTURE: every failure returns an empty/None reading. A budget advisory that raises
would block a dispatch over a hint, and a probe that spawns a subprocess has many ways to
fail that have nothing to do with the allowance it was asked about.

CACHE. Probing spawns a provider CLI (`codex app-server` for Codex), which a multi-agent
dispatch would otherwise re-spawn once per agent. Readings are cached with a TTL in the
gitignored `.claude/.cache/provider_bands.json`; a band moves on the scale of a quota window,
never on the scale of one fan-out.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_registry  # noqa: E402
import quota_bands  # noqa: E402

DEFAULT_TTL_S = int(os.environ.get("PROVIDER_BAND_TTL", "600"))


def _repo_root():
    """The registry's own location anchors the repo, not $CLAUDE_PROJECT_DIR.

    That env var can arrive as an MSYS path a native Windows python3 cannot open, and a
    probe launched with a broken cwd fails in a way that reads as an exhausted allowance.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(model_registry.find_registry())))


def _cache_path():
    return os.path.join(_repo_root(), ".claude", ".cache", "provider_bands.json")


def _cache_read(transport, ttl):
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            entry = json.load(fh).get(transport)
        if entry and (time.time() - entry.get("at", 0)) < ttl:
            return entry.get("reading")
    except Exception:
        pass
    return None


def _cache_write(transport, reading):
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except Exception:
            blob = {}
        blob[transport] = {"at": time.time(), "reading": reading}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, indent=2)
    except Exception:
        pass  # a cache that cannot be written is slow, not wrong


def probe(transport, data=None):
    """Run a transport's quotaProbe and return its reading dict, or None."""
    try:
        spec = model_registry.quota_probe_for(transport, data)
    except Exception:
        return None
    if not spec:
        return None
    cmd = [spec["command"]] + list(spec.get("args") or [])
    # The probe's argv holds repo-relative paths, so cwd is load-bearing.
    if cmd[0] in ("python", "python3"):
        cmd[0] = sys.executable
    try:
        out = subprocess.run(cmd, cwd=_repo_root(), capture_output=True, text=True, timeout=45)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        reading = json.loads(out.stdout)
    except Exception:
        return None
    return reading if isinstance(reading, dict) else None


def reading(transport, ttl=DEFAULT_TTL_S, data=None):
    cached = _cache_read(transport, ttl)
    if cached is not None:
        return cached
    got = probe(transport, data)
    if got is not None:
        _cache_write(transport, got)
    return got


def plan_quota_transports(data=None):
    """Transports that spend a plan allowance AND have at least one selectable model.

    A transport with no available model is out of consideration entirely -- advising a route
    to it would name a launcher that refuses at its own availability gate.
    """
    data = data if data is not None else model_registry.load()
    live = {m["transport"] for m in model_registry.available_models(data)}
    out = []
    for name in (data.get("transports") or {}):
        if name not in live:
            continue
        try:
            if model_registry.cost_model_for(name, data) == "plan-quota":
                out.append(name)
        except Exception:
            continue
    return sorted(out)


def slacker_than(session_band, ttl=DEFAULT_TTL_S, data=None):
    """Plan-quota transports whose own band has MORE headroom than this session's.

    Returns [{transport, band, pressure, planType, launchers}], most-slack first. An
    uncomputable provider band is omitted rather than treated as Surplus: Surplus is the most
    permissive answer, and defaulting an unknown to it would advise routing work toward an
    allowance nobody measured.
    """
    data = data if data is not None else model_registry.load()
    try:
        here = quota_bands.band_rank(session_band)
    except Exception:
        return []
    rows = []
    for name in plan_quota_transports(data):
        got = reading(name, ttl, data)
        band = (got or {}).get("band")
        if not band:
            continue
        try:
            rank = quota_bands.band_rank(band)
        except Exception:
            continue
        if rank >= here:
            continue
        cfg = (data.get("transports") or {}).get(name) or {}
        launchers = [cfg[k] for k in ("launcher", "launcherProxy") if cfg.get(k)]
        rows.append({
            "transport": name, "band": band, "pressure": (got or {}).get("pressure"),
            "planType": (got or {}).get("planType"), "launchers": launchers, "rank": rank,
        })
    return sorted(rows, key=lambda r: r["rank"])


def main(argv):
    if not argv or argv[0] == "list":
        for name in plan_quota_transports():
            got = reading(name) or {}
            print(f"{name}\t{got.get('band') or 'uncomputable'}\t{got.get('pressure')}"
                  f"\t{got.get('planType') or '?'}")
        return 0
    if argv[0] == "compare":
        if len(argv) != 2:
            print("usage: provider_bands.py compare <session-band>", file=sys.stderr)
            return 2
        for row in slacker_than(argv[1]):
            print(f"{row['transport']}\t{row['band']}\t{row['pressure']}\t"
                  f"{','.join(row['launchers'])}")
        return 0
    if argv[0] == "probe":
        if len(argv) != 2:
            print("usage: provider_bands.py probe <transport>", file=sys.stderr)
            return 2
        got = probe(argv[1])
        print(json.dumps(got, indent=2) if got else "null")
        return 0 if got else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
