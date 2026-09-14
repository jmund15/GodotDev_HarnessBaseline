#!/usr/bin/env python3
"""Refresh the opencode (OpenCode Zen FREE) roster in reference/external_models.json.

Free models come and go frequently; this is the one command that makes the roster match
reality again. Evidence classes are handled asymmetrically on purpose:

  DEFINITIVE LIVE   Zen catalog lists the id AND a minimal completion returns choices.
                    -> row added or kept; context window updated from models.dev when present.
  DEFINITIVE DEAD   id absent from Zen's catalog, OR the upstream answers that the model is
                    unavailable/not found (the deepseek-v4-flash-free failure shape,
                    2026-08-22). -> row removed.
  UNKNOWN           401/403 (auth), 429/5xx after a retry, network errors. -> row left
                    UNTOUCHED. Ambiguity never mutates the roster; a mass auth failure
                    aborts the whole run without changes.

Merge policy:
  - non-opencode rows are untouched and stay in position;
  - live opencode rows keep their authored fields (alias, roles, effort, gate) and get
    limits/asOf refreshed only where models.dev has data;
  - new live ids are appended with a generated [a-z0-9-] alias unique across ALL
    transports, self-identity placeholder roles, $0 prices, Surplus gate;
  - the file is validated with tools/model_registry.py --check and RESTORED from backup
    if validation fails.

Usage:
  python3 scripts/opencode_refresh_models.py [--dry-run]

ASCII-only console output (Windows cp1252 consoles).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ZEN_BASE = "https://opencode.ai/zen/v1"
MODELS_DEV_URL = "https://models.dev/api.json"
HTTP_TIMEOUT = 45
RETRY_SLEEP_S = 15

ROOT = Path(__file__).resolve().parents[2]  # .claude/scripts/<this>.py -> repo root
REGISTRY = ROOT / ".claude" / "reference" / "external_models.json"
VALIDATOR = ROOT / ".claude" / "tools" / "model_registry.py"

DEAD_BODY_SIGNS = ("unavailable", "does not exist", "not found", "invalid model",
                   "no such model")


def credential():
    key = os.environ.get("OPENCODE_API_KEY", "").strip()
    return key if key and key.lower() not in ("public", "<redacted>", "your-key-here") \
        else "public"


def http_json(url, key=None, method="GET", payload=None, timeout=HTTP_TIMEOUT):
    headers = {"User-Agent": "harness-harness-opencode-refresh/1.0",
               "Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer %s" % key
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8", "replace"))


def zen_catalog_ids(key):
    """Live model ids from Zen's OpenAI-surface /models. Defensive about response shape."""
    status, body = http_json(ZEN_BASE + "/models", key=key)
    ids = set()
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        items = body["data"]
    elif isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        items = [{"id": k} for k in body.keys()]
    else:
        items = []
    for item in items:
        mid = item.get("id") if isinstance(item, dict) else None
        if isinstance(mid, str):
            ids.add(mid)
    if not ids:
        raise RuntimeError("zen /models returned an unrecognized shape; aborting")
    return ids


def modelsdev_index():
    """{model_id: {'context': int|None, 'output': int|None, 'free': bool}} across providers."""
    try:
        _, api = http_json(MODELS_DEV_URL, timeout=90)
    except Exception as exc:
        print("WARN: models.dev fetch failed (%s); limits for NEW ids will be null" % exc)
        return {}
    index = {}
    if not isinstance(api, dict):
        return index
    for provider in api.values():
        models = provider.get("models") if isinstance(provider, dict) else None
        if not isinstance(models, dict):
            continue
        for mid, info in models.items():
            if not isinstance(info, dict) or mid in index:
                continue
            limit = info.get("limit") or {}
            cost = info.get("cost")
            ctx = limit.get("context") if isinstance(limit, dict) else None
            out = limit.get("output") if isinstance(limit, dict) else None
            free = False
            if isinstance(cost, dict) and cost:
                vals = [v for v in cost.values() if isinstance(v, (int, float))]
                free = bool(vals) and all(v == 0 for v in vals)
            index[mid] = {
                "context": ctx if isinstance(ctx, int) else None,
                "output": out if isinstance(out, int) else None,
                "free": free,
            }
    return index


def probe(model_id, key):
    """LIVE | DEAD | AUTH | UNKNOWN. One retry for transient classes only."""
    payload = {"model": model_id,
               "messages": [{"role": "user",
                             "content": "Reply with the single word OK."}],
               "max_tokens": 16}
    last = ("UNKNOWN", "")
    for attempt in (1, 2):
        try:
            status, body = http_json(ZEN_BASE + "/chat/completions", key=key,
                                     method="POST", payload=payload, timeout=60)
            text = json.dumps(body).lower() if not isinstance(body, str) else body.lower()
            if status == 200:
                choices = body.get("choices") if isinstance(body, dict) else None
                if choices:
                    return "LIVE", ""
                if any(s in text for s in DEAD_BODY_SIGNS):
                    return "DEAD", text[:160]
                return "UNKNOWN", "200-with-empty-choices"
            if any(s in text for s in DEAD_BODY_SIGNS):
                return "DEAD", "%s %s" % (status, text[:160])
            if status in (400, 404, 422):
                return "DEAD", "%s %s" % (status, text[:160])
            if status in (401, 403):
                return "AUTH", str(status)
            last = ("UNKNOWN", str(status))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").lower()[:160]
            if any(s in detail for s in DEAD_BODY_SIGNS):
                return "DEAD", "%s %s" % (exc.code, detail)
            if exc.code in (401, 403):
                return "AUTH", str(exc.code)
            if exc.code in (400, 404, 422):
                return "DEAD", "%s %s" % (exc.code, detail)
            last = ("UNKNOWN", "%s %s" % (exc.code, detail))
        except Exception as exc:  # network/timeout class
            last = ("UNKNOWN", type(exc).__name__)
        if attempt == 1:
            time.sleep(RETRY_SLEEP_S)
    return last


def gen_alias(model_id, taken):
    base = re.sub(r"-free$", "", model_id.strip().lower())
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:18].strip("-") or "model"
    alias, n = base, 2
    while alias in taken:
        alias = "%s-%d" % (base, n)
        n += 1
    return alias


def new_row(model_id, alias, md, today):
    src = "https://models.dev (opencode catalog)"
    row = {
        "id": model_id,
        "alias": alias,
        "transport": "opencode",
        "roles": [alias],
        "limits": {
            "contextTokens": md.get("context") if md else None,
            "maxOutputTokens": md.get("output") if md else None,
            "concurrency": None,
        },
        "price": {
            "cacheHitPer1M": 0.0,
            "cacheMissPer1M": 0.0,
            "outputPer1M": 0.0,
            "currency": "USD",
            "asOf": today,
            "source": src,
        },
        "effort": {"evidence": "unmeasured"},
        "authTier": "open",
        "gate": {"minBand": "Surplus"},
    }
    if md is None:
        row["_limitsComment"] = ("No models.dev entry at registration time; the launcher "
                                 "skips CLAUDE_CODE_MAX_CONTEXT_TOKENS until limits are "
                                 "authored (CLI default 200k applies).")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = credential()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        zen_ids = zen_catalog_ids(key)
    except Exception as exc:
        print("ABORT: zen catalog unreachable (%s); roster unchanged" % exc)
        return 2

    md_index = modelsdev_index()
    with open(REGISTRY, encoding="utf-8") as fh:
        reg = json.load(fh)

    models = reg.get("models")
    transports = reg.get("transports")
    if not isinstance(models, list) or not isinstance(transports, dict) \
            or "opencode" not in transports:
        print("ABORT: registry shape unrecognized; roster unchanged")
        return 2

    oc_rows = [(i, m) for i, m in enumerate(models) if m.get("transport") == "opencode"]
    oc_ids = [m["id"] for _, m in oc_rows]
    all_aliases = {m.get("alias") for m in models if m.get("alias")}

    # A registered id absent from the live catalog is definitive death regardless of whether it
    # gets probed below -- computed directly from oc_ids/zen_ids, never from `results`. The
    # candidates set (probed for LIVE/DEAD/UNKNOWN) is everything ELSE worth checking: already
    # registered AND still catalog-present, plus -free suffixed or catalog-zero-cost ids Zen
    # currently lists (big-pickle shows why the suffix rule alone is insufficient). Filtering
    # candidates to catalog-present ids before probing used to also (wrongly) exclude
    # catalog-absent registered ids from `results`, so they never reached the dead-detection
    # below and were kept as "unknown, left untouched" instead of removed.
    catalog_absent_dead = set(oc_ids) - zen_ids
    candidates = (set(oc_ids) & zen_ids)
    for mid in zen_ids:
        if mid.endswith("-free") or md_index.get(mid, {}).get("free"):
            candidates.add(mid)

    results, seen_auth = {}, 0
    for mid in sorted(candidates):
        verdict, detail = probe(mid, key)
        results[mid] = (verdict, detail)
        if verdict == "AUTH":
            seen_auth += 1
        tag = {"LIVE": "live   ", "DEAD": "dead   ", "AUTH": "auth?? ", "UNKNOWN": "unknown"}[verdict]
        print("[%s] %s %s" % (tag, mid, detail[:80]))

    live = {m for m, (v, _) in results.items() if v == "LIVE"}
    dead = catalog_absent_dead | {m for m, (v, _) in results.items() if v == "DEAD"}
    unknown = {m for m, (v, _) in results.items() if v in ("UNKNOWN",)}

    # Mass auth failure = environment fault (bad OPENCODE_API_KEY), not per-model death.
    if not live and seen_auth and seen_auth == len(results):
        print("ABORT: every probe failed with an auth status; refusing to mutate the roster")
        return 2

    added, removed, updated, kept, unknown_kept = [], [], [], [], []
    new_rows = []
    taken = set(all_aliases)
    last_oc_pos = max([i for i, _ in oc_rows], default=len(models) - 1)

    for mid in sorted(live - set(oc_ids)):
        md = md_index.get(mid)
        if md is not None and not md["free"]:
            print("SKIP %s: live but priced on models.dev; not auto-added (free tier only)" % mid)
            continue
        alias = gen_alias(mid, taken)
        taken.add(alias)
        row = new_row(mid, alias, md, today)
        if row["limits"]["contextTokens"] is None:
            print("WARN %s: no models.dev window; row added with null limits" % mid)
        new_rows.append(row)
        added.append("%s -> alias '%s'" % (mid, alias))

    rebuilt = []
    for i, m in enumerate(models):
        if m.get("transport") != "opencode":
            rebuilt.append(m)
            continue
        mid = m["id"]
        if mid in dead:
            removed.append(mid)
            continue
        if mid in unknown or results.get(mid, ("UNKNOWN", ""))[0] in ("UNKNOWN", "AUTH"):
            unknown_kept.append(mid)
            rebuilt.append(m)
            continue
        # live: refresh limits/asOf where models.dev speaks
        md = md_index.get(mid)
        lim = m.setdefault("limits", {})
        changed = []
        if md:
            if md["context"] and lim.get("contextTokens") != md["context"]:
                lim["contextTokens"] = md["context"]; changed.append("ctx=%d" % md["context"])
            if md["output"] and lim.get("maxOutputTokens") != md["output"]:
                lim["maxOutputTokens"] = md["output"]; changed.append("out=%d" % md["output"])
        price = m.setdefault("price", {})
        if price.get("asOf") != today:
            price["asOf"] = today
            changed.append("asOf")
        (updated if changed else kept).append(
            "%s%s" % (mid, (" (" + ",".join(changed) + ")") if changed else ""))
        rebuilt.append(m)

    insert_at = min(last_oc_pos + 1, len(rebuilt))
    rebuilt[insert_at:insert_at] = new_rows
    reg["models"] = rebuilt
    reg["revision"] = today
    reg["_rosterComment"] = (
        "The opencode rows are the Zen FREE tier verified live by "
        ".claude/scripts/opencode_refresh_models.py on %s (%d live, %d removed, "
        "%d added this run). Windows/prices are FIRST-PARTY metadata from models.dev "
        "(the OpenCode team's own catalog), not benchmarks. Each row's `roles` is its own "
        "alias - a self-identity placeholder asserting NO Anthropic-tier equivalence; tier "
        "claims land only on scored evidence from the model-effort battery. Until scored, "
        "nothing routes to these by role - dispatch by -m alias or id. Refresh any time "
        "free models churn."
        % (today, len(live), len(removed), len(added))
    )

    print("")
    print("plan: %d live kept/updated, %d removed, %d added, %d unknown-left-untouched"
          % (len(live & set(oc_ids)), len(removed), len(added), len(unknown_kept)))
    for line in added:
        print("  ADDED   " + line)
    for line in removed:
        print("  REMOVED " + line)
    for line in updated:
        print("  UPDATED " + line)
    for line in unknown_kept:
        print("  KEPT(?) " + line)
    for line in kept:
        print("  KEPT    " + line)

    if args.dry_run:
        print("dry-run: registry NOT written")
        return 0

    backup = REGISTRY.with_suffix(".json.bak-%s"
                                  % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    shutil.copyfile(REGISTRY, backup)
    tmp = REGISTRY.with_suffix(".json.tmp-refresh")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(reg, fh, indent=2, ensure_ascii=True)
        fh.write("\n")
    os.replace(tmp, REGISTRY)

    val = subprocess.run([sys.executable, str(VALIDATOR), "--check"],
                         capture_output=True, text=True)
    if val.returncode != 0:
        shutil.copyfile(backup, REGISTRY)
        print("VALIDATION FAILED -- registry RESTORED from %s" % backup.name)
        print(val.stdout[-800:], val.stderr[-800:])
        return 1
    print("registry written and validated (backup: %s)" % backup.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
