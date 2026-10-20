#!/usr/bin/env python3
"""Refresh the OpenCode Go roster (transport `go`) in reference/external_models.json.

Free opencode CLI models sync with `python3 .claude/tools/free_pool.py sync` (/model_ingest); a run
without --go refuses and names it.

Usage:
  python3 scripts/opencode_refresh_models.py --go --protocol <file> --caps <file>
      [--catalog <file>] [--modelsdev <file>] [--served <file>] [--registry <file>] [--dry-run]

Inputs:
  membership    GET /zen/go/v1/models, or --catalog (the same OpenAI /models JSON, saved).
  apiMode       --protocol, the tool-bearing sweep {results: {id: {mode}}}; a missing or null
                mode registers the id `unavailable`.
  price         the models.dev `opencode-go` provider's `cost` (input, output, cache_read, optional
                cache_write; USD per 1M) becomes the row's list `price`. A row the provider lists no
                complete cost for gets a `priceGap` naming the catalog, unless it already holds a price.
  limits        only the models.dev `opencode-go` provider's entries (api.json, or --modelsdev as
                api.json or as {id: [{provider, context, output}]}); the cross-provider first hit
                disagrees for many Go ids and is reported, never used. A row with no Go-owned entry
                for its upstream id or a served id keeps null limits.
  planCapUSD    --caps {monthlyUSD: {id: <n>|null}}; null means the docs list the id as unlimited.
  identity      --served (JSONL: requested, modelHeaders.x-opencode-upstream-model-id) folds a
                requested id Go serves as another catalog id into that id's row as a servedId;
                existing servedIds on Go rows fold the same way. An upstream id another transport's
                row already names takes the row id `go/<upstream>` with `upstreamId`.
  family        the product line: the id without version tokens, grouped so a line inherits the
                family of any member another transport already registers; within a family the
                highest version is `current`, the rest `superseded`.
It never adds, removes or mutates an `opencode` row or alias; it sets the `opencode` transport
`unavailable` (Zen free answers only the real OpenCode client) and records `_limitsSource` on `go`.
A registered Go row the catalog no longer lists is kept and set `unavailable`. The new document is
validated in memory and spliced into the file object by object, so untouched rows keep their bytes;
any failure leaves the registry bytes unchanged. --dry-run prints the rows as JSON and writes nothing.

ASCII-only console output (Windows cp1252 consoles).
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MODELS_DEV_URL = "https://models.dev/api.json"
HTTP_TIMEOUT = 45

ROOT = Path(__file__).resolve().parents[2]  # .claude/scripts/<this>.py -> repo root
REGISTRY = ROOT / ".claude" / "reference" / "external_models.json"
VALIDATOR = ROOT / ".claude" / "tools" / "model_registry.py"

sys.path.insert(0, str(VALIDATOR.parent))
from model_registry import product_line  # noqa: E402

def http_json(url, key=None, method="GET", payload=None, timeout=HTTP_TIMEOUT):
    headers = {"User-Agent": "harness-opencode-refresh/1.0",
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


GO_BASE = "https://opencode.ai/zen/go/v1"
GO_PROVIDER = "opencode-go"
ENV_FILE = Path.home() / ".env.ai-worker.cmd"
OPENCODE_UNAVAILABLE_REASON = (
    "Zen's free tier answers only the real OpenCode client: copied client headers get 403 "
    "FreeTierError, and no launcher sends another client's identity. Rows stay so stored records "
    "resolve.")
GO_LIMITS_SOURCE = (
    "models.dev, the `opencode-go` provider's entries only (the cross-provider first hit disagrees "
    "for many Go ids) - refreshed by scripts/opencode_refresh_models.py --go. A row without a "
    "Go-owned entry keeps null limits.")
GO_PRICE_SOURCE = "https://models.dev/api.json (the `opencode-go` provider's cost, USD per 1M tokens)"
GO_PRICE_CHECKED = "https://models.dev/api.json"


class GoRefreshError(Exception):
    """A Go refresh input or result that must leave the registry untouched."""


def go_credential():
    key = os.environ.get("OPENCODE_API_KEY", "").strip()
    if not key:
        try:
            for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"\s*set\s+OPENCODE_API_KEY=(.*)$", line, re.I)
                if m:
                    key = m.group(1).strip().strip('"')
                    break
        except OSError:
            pass
    if not key or key.lower() in ("public", "<redacted>", "your-key-here"):
        raise GoRefreshError("auth failure: Go needs OPENCODE_API_KEY (env or %s)" % ENV_FILE)
    return key


def go_catalog_live():
    key = go_credential()
    req = urllib.request.Request(GO_BASE + "/models", headers={
        "Authorization": "Bearer %s" % key, "Accept": "application/json",
        "User-Agent": "harness-refresh/1.0", "x-opencode-session": "ses_harness_roster_refresh"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise GoRefreshError("auth failure: Go /models answered HTTP %d" % exc.code)
        raise GoRefreshError("Go /models answered HTTP %d" % exc.code)
    except (OSError, ValueError) as exc:
        raise GoRefreshError("Go /models unreachable (%s)" % type(exc).__name__)


def go_catalog_ids(body):
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        raise GoRefreshError("malformed catalog: expected {data: [{id}, ...]}")
    ids = []
    for item in items:
        mid = item.get("id") if isinstance(item, dict) else None
        if not isinstance(mid, str) or not mid.strip():
            raise GoRefreshError("malformed catalog: an entry has no string id")
        ids.append(mid)
    if len(ids) != len(set(ids)):
        raise GoRefreshError("malformed catalog: repeated id")
    return ids


def go_limit_sources(api):
    """({id: (context, output)} owned by opencode-go, {id: (context, output)} first provider hit)."""
    owned, first = {}, {}

    def pair(ctx, out):
        return (ctx if type(ctx) is int else None, out if type(out) is int else None)

    if isinstance(api, dict) and api and all(isinstance(v, list) for v in api.values()):
        for mid, hits in api.items():
            hits = [h for h in hits if isinstance(h, dict)]
            if hits:
                first[mid] = pair(hits[0].get("context"), hits[0].get("output"))
            for h in hits:
                if h.get("provider") == GO_PROVIDER:
                    owned[mid] = pair(h.get("context"), h.get("output"))
        return owned, first
    if not isinstance(api, dict):
        raise GoRefreshError("malformed models.dev input")
    for provider_id, provider in api.items():
        models = provider.get("models") if isinstance(provider, dict) else None
        if not isinstance(models, dict):
            continue
        for mid, info in models.items():
            limit = (info.get("limit") if isinstance(info, dict) else None) or {}
            value = pair(limit.get("context"), limit.get("output"))
            first.setdefault(mid, value)
            if provider_id == GO_PROVIDER:
                owned[mid] = value
    return owned, first


def go_price_sources(api):
    """{id: cost dict} for the models.dev `opencode-go` provider's entries, in either input shape."""
    owned = {}
    if isinstance(api, dict) and api and all(isinstance(v, list) for v in api.values()):
        for mid, hits in api.items():
            for h in hits:
                if isinstance(h, dict) and h.get("provider") == GO_PROVIDER and isinstance(h.get("cost"), dict):
                    owned[mid] = h["cost"]
        return owned
    provider = (api or {}).get(GO_PROVIDER) if isinstance(api, dict) else None
    models = provider.get("models") if isinstance(provider, dict) else None
    for mid, info in (models or {}).items():
        if isinstance(info, dict) and isinstance(info.get("cost"), dict):
            owned[mid] = info["cost"]
    return owned


def _list_price(cost, today):
    """The registry `price` object for one models.dev cost dict, or None when it lacks a required rate."""
    def rate(key):
        v = cost.get(key)
        return v if type(v) in (int, float) else None
    miss, hit, out, write = rate("input"), rate("cache_read"), rate("output"), rate("cache_write")
    if miss is None or hit is None or out is None:
        return None
    price = {"cacheMissPer1M": miss, "cacheHitPer1M": hit, "outputPer1M": out}
    if write is not None:
        price["cacheWritePer1M"] = write
    price.update(currency="USD", asOf=today, source=GO_PRICE_SOURCE)
    return price


def go_served_folds(path, catalog):
    """{requested id: served catalog id} where Go answered a request as a different catalog id."""
    folds = {}
    if not path:
        return folds
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        requested = event.get("requested") if isinstance(event, dict) else None
        upstream = ((event.get("modelHeaders") or {}).get("x-opencode-upstream-model-id")
                    if isinstance(event, dict) else None)
        if requested in catalog and upstream in catalog and upstream != requested:
            folds[requested] = upstream
    return folds


def version_key(names):
    return max(tuple(int(n) for n in re.findall(r"\d+", name)) for name in names)


def go_plan(reg, catalog, protocol, owned, first, caps, folds, today, protocol_name, prices=None):
    """-> (new registry document, report). Pure: reads its arguments, writes nothing."""
    import copy
    doc = copy.deepcopy(reg)
    models = doc["models"]
    go_rows = [m for m in models if m.get("transport") == "go"]
    other_names = {}
    for m in models:
        if m.get("transport") == "go":
            continue
        for name in (m.get("id"), m.get("alias"), m.get("upstreamId"), *(m.get("servedIds") or [])):
            if name:
                other_names.setdefault(name, m)
    by_upstream = {}
    for m in go_rows:
        by_upstream[m.get("upstreamId") or m["id"]] = m
        for served in m.get("servedIds") or []:
            folds.setdefault(served, m.get("upstreamId") or m["id"])
    catalog_set = set(catalog)
    heads = [mid for mid in catalog if mid not in folds or folds[mid] not in catalog_set]
    served_by = {}
    for requested, upstream in folds.items():
        if requested in catalog_set and upstream in catalog_set:
            served_by.setdefault(upstream, []).append(requested)

    def inherited(mid):
        m = other_names.get(mid)
        return m.get("family") if m and mid in (m.get("id"), m.get("upstreamId"),
                                                   *(m.get("servedIds") or [])) else None

    line_family = {}
    for mid in heads:
        fam = inherited(mid)
        if fam:
            old = line_family.setdefault(product_line(mid), fam)
            if old != fam:
                raise GoRefreshError("product line %s inherits two families (%s, %s)"
                                     % (product_line(mid), old, fam))

    modes = (protocol or {}).get("results") if isinstance(protocol, dict) else None
    if not isinstance(modes, dict):
        raise GoRefreshError("malformed protocol file: expected {results: {id: {mode}}}")
    monthly = (caps or {}).get("monthlyUSD") if isinstance(caps, dict) else None
    if not isinstance(monthly, dict):
        raise GoRefreshError("malformed caps file: expected {monthlyUSD: {id: <n>|null}}")

    taken = {n for m in models for n in (m.get("id"), m.get("alias"), *(m.get("servedIds") or [])) if n}
    next_order = max(m["order"] for m in models) + 1
    template = {"costRank": 1, "prior": "mid", "roles": [], "effort": {"evidence": "unmeasured"},
                "authTier": "open", "gate": {"minBand": "Surplus"}}
    report = {"mode": "go", "catalogCount": len(catalog), "added": [], "updated": [], "unchanged": [],
              "markedAbsent": [], "folded": {}, "limitConflictsIgnored": [], "noGoOwnedLimits": [],
              "priced": 0, "priceGaps": 0}
    prices = prices or {}
    planned = []
    for mid in heads:
        served = sorted(served_by.get(mid, []))
        row = by_upstream.get(mid)
        is_new = row is None
        if is_new:
            row_id = ("go/" + mid) if mid in other_names else mid
            alias = re.sub(r"[^a-z0-9]+", "-", ("go-" + mid).lower()).strip("-")
            if alias in taken or row_id in taken:
                raise GoRefreshError("generated name for %s collides with a registered name" % mid)
            row = {"id": row_id, "alias": alias, "transport": "go"}
            if row_id != mid:
                row["upstreamId"] = mid
            row.update(label="%s (Go)" % mid, family="", version=mid, lifecycle="current",
                       order=next_order, **copy.deepcopy(template))
            next_order += 1
            models.append(row)
        before = copy.deepcopy(row)
        if served:
            row["servedIds"] = sorted(set(row.get("servedIds") or []) | set(served))
            for s in served:
                report["folded"][s] = row["id"]
        mode = (modes.get(mid) or {}).get("mode") if isinstance(modes.get(mid), dict) else None
        if mode in ("messages", "chat", "responses"):
            row["apiMode"] = mode
        else:
            row.pop("apiMode", None)
            row["status"] = {"state": "unavailable", "since": today, "reason": (
                "No verified tool-bearing apiMode in %s; excluded until a protocol sweep verifies one."
                % protocol_name)}
        limits = None
        for name in [mid] + served:
            if name in owned:
                limits, source = owned[name], name
                break
        row["limits"] = {"contextTokens": limits[0] if limits else None,
                         "maxOutputTokens": limits[1] if limits else None, "concurrency": None}
        row.pop("_limitsComment", None)
        row.pop("windowGap", None)
        if limits is None:
            row["windowGap"] = {"reason": "No %s models.dev entry for this id." % GO_PROVIDER,
                                "checked": "https://models.dev", "asOf": today}
            row["_limitsComment"] = "No %s models.dev entry for %s; limits stay null." % (
                GO_PROVIDER, ", ".join([mid] + served))
            report["noGoOwnedLimits"].append(mid)
        elif source != mid:
            row["_limitsComment"] = ("Go serves %s as upstream %s; limits are the %s entry for %s."
                                     % (source, mid, GO_PROVIDER, source))
        for name in [mid] + served:
            if name in owned and name in first and first[name] != owned[name]:
                report["limitConflictsIgnored"].append(
                    {"id": name, "crossProvider": list(first[name]), "goOwned": list(owned[name])})
        if mid not in monthly and not any(s in monthly for s in served):
            raise GoRefreshError("caps file has no monthly cap entry for %s" % mid)
        cap = monthly.get(mid, next((monthly[s] for s in served if s in monthly), None))
        listed = next((_list_price(prices[n], today) for n in [mid] + served if n in prices), None)
        if listed is not None:
            row["price"] = listed
            row.pop("priceGap", None)
            report["priced"] += 1
        elif "price" not in row:
            row["priceGap"] = {"reason": "models.dev lists no complete opencode-go cost for %s."
                               % ", ".join([mid] + served),
                               "checked": GO_PRICE_CHECKED, "asOf": today}
            report["priceGaps"] += 1
        row["planCapUSD"] = {"monthly": cap} if cap is not None else {"monthly": None, "unlimited": True}
        row["family"] = line_family.get(product_line(mid), product_line(mid))
        planned.append((row, before, is_new, [mid] + served))

    for fam in {r["family"] for r, _, _, _ in planned}:
        members = [(r, names) for r, _, _, names in planned if r["family"] == fam]
        top = max(members, key=lambda item: version_key(item[1]))
        ties = [r for r, names in members if version_key(names) == version_key(top[1])]
        if len(ties) > 1:
            raise GoRefreshError("family %s has two rows at one version: %s"
                                 % (fam, ", ".join(r["id"] for r in ties)))
        for r, _ in members:
            r["lifecycle"] = "current" if r is top[0] else "superseded"

    for row, before, is_new, _ in planned:
        if is_new:
            report["added"].append(row)
        elif row != before:
            report["updated"].append(row)
        else:
            report["unchanged"].append(row["id"])

    listed = set(heads) | set(folds)
    for m in go_rows:
        upstream = m.get("upstreamId") or m["id"]
        if upstream in listed or m.get("lifecycle") == "retired" \
                or (m.get("status") or {}).get("state") == "unavailable":
            continue
        m["status"] = {"state": "unavailable", "since": today,
                       "reason": "Absent from the Go catalog on %s; kept so stored records resolve." % today}
        report["markedAbsent"].append(m)

    for m in go_rows:
        if "price" in m or "priceGap" in m or (m.get("lifecycle") == "retired"
                                                and (m.get("status") or {}).get("state") == "unavailable"):
            continue
        m["priceGap"] = {"reason": "The Go catalog no longer lists %s and models.dev holds no cost for it."
                                   % (m.get("upstreamId") or m["id"]),
                         "checked": GO_PRICE_CHECKED, "asOf": today}
        report["priceGaps"] += 1

    go_t = doc["transports"]["go"]
    go_t["_limitsSource"] = GO_LIMITS_SOURCE
    oc_status = doc["transports"]["opencode"].setdefault("status", {})
    if oc_status.get("state") != "unavailable" or oc_status.get("reason") != OPENCODE_UNAVAILABLE_REASON:
        doc["transports"]["opencode"]["status"] = {"state": "unavailable", "since": today,
                                                   "reason": OPENCODE_UNAVAILABLE_REASON}
    doc["revision"] = today
    report["transports"] = {"go": {"_limitsSource": go_t["_limitsSource"]},
                            "opencode": {"status": doc["transports"]["opencode"]["status"]}}
    report["revision"] = today
    return doc, report


# ---- byte-preserving writer: splice changed objects into the file text, keep every other byte ----

_DECODER = json.JSONDecoder()


def _skip_ws(text, i):
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _object_members(text, start):
    """[(key, value_start, value_end)] of the JSON object at text[start] == '{', and its end."""
    i, out = _skip_ws(text, start + 1), []
    if text[i] == "}":
        return out, i + 1
    while True:
        key, j = _DECODER.raw_decode(text, i)
        j = _skip_ws(text, j)
        j = _skip_ws(text, j + 1)
        _, k = _DECODER.raw_decode(text, j)
        out.append((key, j, k))
        k = _skip_ws(text, k)
        if text[k] == ",":
            i = _skip_ws(text, k + 1)
            continue
        return out, k + 1


def _array_elements(text, start):
    i, out = _skip_ws(text, start + 1), []
    if text[i] == "]":
        return out, i + 1
    while True:
        _, k = _DECODER.raw_decode(text, i)
        out.append((i, k))
        k = _skip_ws(text, k)
        if text[k] == ",":
            i = _skip_ws(text, k + 1)
            continue
        return out, k + 1


def _indent_at(text, pos):
    line_start = text.rfind("\n", 0, pos) + 1
    return text[line_start:pos]


def _format_object(obj, indent):
    inner = indent + "  "
    lines = ["%s%s: %s" % (inner, json.dumps(k), json.dumps(v, ensure_ascii=False)) for k, v in obj.items()]
    return "{\n" + ",\n".join(lines) + "\n" + indent + "}"


def splice_registry(text, old, new):
    """The registry text with each changed model row, transport key and top-level value replaced."""
    edits = []
    top, _ = _object_members(text, _skip_ws(text, 0))
    spans = {key: (a, b) for key, a, b in top}
    for key in ("revision",):
        if old.get(key) != new.get(key):
            a, b = spans[key]
            edits.append((a, b, json.dumps(new[key])))
    t_members, _ = _object_members(text, spans["transports"][0])
    for name, a, _b in t_members:
        if old["transports"].get(name) == new["transports"].get(name):
            continue
        members, end = _object_members(text, a)
        present = {k: (va, vb) for k, va, vb in members}
        indent = _indent_at(text, members[0][1]) if members else "      "
        indent = indent[:len(indent) - len(indent.lstrip())]
        for k, v in new["transports"][name].items():
            if old["transports"][name].get(k) == v:
                continue
            if k in present:
                edits.append((present[k][0], present[k][1], json.dumps(v, ensure_ascii=False)))
            else:
                last_end = members[-1][2]
                edits.append((last_end, last_end, ",\n%s%s: %s" % (indent, json.dumps(k),
                                                                   json.dumps(v, ensure_ascii=False))))
    elements, _ = _array_elements(text, spans["models"][0])
    if len(elements) != len(old["models"]):
        raise GoRefreshError("registry text and parsed models disagree")
    for (a, b), before, after in zip(elements, old["models"], new["models"]):
        if before != after:
            edits.append((a, b, _format_object(after, _indent_at(text, a))))
    extra = new["models"][len(old["models"]):]
    if extra:
        last_a, last_b = elements[-1]
        indent = _indent_at(text, last_a)
        edits.append((last_b, last_b, "".join(",\n" + indent + _format_object(r, indent) for r in extra)))
    for a, b, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:a] + replacement + text[b:]
    return text


def _read_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoRefreshError("malformed %s %s (%s)" % (what, path, type(exc).__name__))


def go_main(args):
    registry = Path(args.registry) if args.registry else REGISTRY
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    original = registry.read_bytes()
    try:
        catalog = go_catalog_ids(_read_json(args.catalog, "catalog") if args.catalog else go_catalog_live())
        if args.modelsdev:
            api = _read_json(args.modelsdev, "models.dev input")
        else:
            try:
                _, api = http_json(MODELS_DEV_URL, timeout=90)
            except Exception as exc:
                raise GoRefreshError("models.dev unreachable (%s)" % type(exc).__name__)
        owned, first = go_limit_sources(api)
        protocol = _read_json(args.protocol, "protocol file")
        caps = _read_json(args.caps, "caps file")
        folds = go_served_folds(args.served, set(catalog))
        text = original.decode("utf-8")
        reg = json.loads(text)
        if "go" not in reg.get("transports", {}) or "opencode" not in reg.get("transports", {}):
            raise GoRefreshError("registry has no go or opencode transport")
        doc, report = go_plan(reg, catalog, protocol, owned, first, caps, folds, today,
                              Path(args.protocol).name, prices=go_price_sources(api))
        sys.path.insert(0, str(VALIDATOR.parent))
        import model_registry
        ladder = ROOT / model_registry.LADDER_RELPATH
        model_registry._validate(doc, str(registry))
        model_registry.check_ladder(path=str(ladder), data=doc)
        new_text = splice_registry(text, reg, doc)
        if json.loads(new_text) != doc:
            raise GoRefreshError("spliced registry text does not parse back to the planned document")
    except Exception as exc:
        print("ABORT: %s; registry unchanged" % exc, file=sys.stderr)
        return 2
    report["registry"] = str(registry)
    if args.dry_run:
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0
    tmp = registry.with_name(registry.name + ".tmp-go-refresh")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
        os.replace(tmp, registry)
        written = json.loads(registry.read_text(encoding="utf-8"))
        model_registry._validate(written, str(registry))
        model_registry.check_ladder(path=str(ladder), data=written)
    except Exception as exc:
        registry.write_bytes(original)
        print("ABORT: %s; registry bytes restored" % exc, file=sys.stderr)
        return 1
    finally:
        if tmp.exists():
            tmp.unlink()
    print("go roster: %d added, %d updated, %d unchanged, %d marked absent, %d priced from the catalog, "
          "%d price gaps; registry written"
          % (len(report["added"]), len(report["updated"]), len(report["unchanged"]),
             len(report["markedAbsent"]), report["priced"], report["priceGaps"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--go", action="store_true", help="refresh the OpenCode Go roster")
    ap.add_argument("--catalog")
    ap.add_argument("--modelsdev")
    ap.add_argument("--protocol")
    ap.add_argument("--caps")
    ap.add_argument("--served")
    ap.add_argument("--registry")
    args = ap.parse_args(argv)
    if not args.go:
        ap.error("this script refreshes only the OpenCode Go roster (--go); free opencode CLI models "
                 "register through /model_ingest")
    if not args.protocol or not args.caps:
        ap.error("--go needs --protocol and --caps")
    return go_main(args)


if __name__ == "__main__":
    sys.exit(main())
