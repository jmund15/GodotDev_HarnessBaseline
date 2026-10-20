"""The free pool: the registry rows carrying `pool: free`, one ladder row (`free pool`) across them.

`sync` ingests the live opencode CLI catalog. A new `-free` id becomes a registry row with no `pool`, so
it stays out of the pool until its published score is set (`pubScore`) and `pool: free` is added. A missing
id is marked unavailable, dated. `status`, `ready_count()`, `member_state()` and `cool()` read and write member markers: the
same `members/<transport>/<member>.json` file `sidecar_common.sh` writes through `sc_mark_exhausted <member>`.
"""
import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_registry as mr  # noqa: E402

TRANSPORT = "opencode-cli"
POOL = "free"
# Placeholder until the S2 spike measures a member's cooldown: the length of `provider_capacity`'s lockout.
COOLDOWN_SECONDS = 420
MODELS_DEV_URL = "https://models.dev/api.json"
CATALOG_ARGS = ["models", "opencode", "--verbose"]
ROW_KEYS = ("id", "upstreamId", "alias", "transport", "label", "family", "version", "lifecycle", "order",
            "costRank", "prior", "status", "roles", "limits", "price", "effort", "effortValues", "authTier", "gate")


class SyncError(Exception):
    """Ingest refused: nothing was written."""


def _exhausted_dir():
    return Path(os.environ.get("SIDECAR_EXHAUSTED_DIR") or Path.home() / ".claude" / "sidecar-exhausted")


def _member_marker(member):
    return _exhausted_dir() / "members" / TRANSPORT / f"{member}.json"


def _read_marker(member):
    try:
        with open(_member_marker(member), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _marker_live(marker, now):
    try:
        return float(marker["expiresAt"]) > now
    except (KeyError, TypeError, ValueError):
        return False


def cool(member, seconds=COOLDOWN_SECONDS):
    """Cool one pool member for `seconds`. A live marker stands, as `sc_mark_exhausted` keeps its first stop's.
    Returns the marker written or kept."""
    now = time.time()
    prior = _read_marker(member)
    if prior and _marker_live(prior, now):
        return prior
    reset = int(now + seconds)
    data = {"schemaVersion": 1, "transport": TRANSPORT, "writtenAt": int(now), "expiresAt": reset,
            "resetsAt": None, "label": None, "sessionId": None, "member": member,
            "failures": int((prior or {}).get("failures", 0)) + 1}
    path = _member_marker(member)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".exhausted-", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(temp_path, path)
    return data


def _members(registry_path=None):
    path = Path(registry_path) if registry_path else mr.find_registry()
    mr._cache.pop(str(path), None)   # the registry changes under sync; never read a cached copy
    data = mr.load(path)
    return [m for m in data["models"]
            if m.get("pool") == POOL and m.get("transport") == TRANSPORT
            and (m.get("status") or {}).get("state") == "available"]


def _cooled(member, now):
    marker = _read_marker(member)
    return bool(marker) and _marker_live(marker, now)


def ready_count(registry_path=None):
    """(ready, members): `members` lists every pool member's CLI id; `ready` counts those not cooled."""
    now = time.time()
    members = sorted(m["upstreamId"] for m in _members(registry_path))
    return sum(1 for mid in members if not _cooled(mid, now)), members


def member_state(alias, registry_path=None):
    """(state, detail) for the registry row whose alias is `alias`: ("unavailable", the registry's reason),
    ("cooled", whole seconds until a pool member's marker expires) or ("ready", ""). An unknown alias is ready:
    the launcher owns its refusal."""
    path = Path(registry_path) if registry_path else mr.find_registry()
    mr._cache.pop(str(path), None)
    row = mr.row_for_alias(alias, mr.load(path))
    if row is None:
        return "ready", ""
    status = row.get("status") or {}
    if status.get("state") == "unavailable":
        return "unavailable", str(status.get("reason") or "no reason recorded")
    if row.get("pool") == POOL and row.get("transport") == TRANSPORT:
        marker = _read_marker(row["upstreamId"])
        now = time.time()
        if marker and _marker_live(marker, now):
            return "cooled", str(max(1, int(float(marker["expiresAt"]) - now)))
    return "ready", ""


def ready_text(registry_path=None):
    """The posture clause: `free pool: <ready>/<members> ready`."""
    ready, members = ready_count(registry_path)
    return f"free pool: {ready}/{len(members)} ready"


def _score_text(entry):
    score = entry.get("pubScore")
    if not score:
        return "pubScore none"
    return f"pubScore {score['value']} ({score['source']}, {score['date']})"


def status_lines(registry_path=None):
    now = time.time()
    lines = []
    for entry in sorted(_members(registry_path), key=lambda m: m["upstreamId"]):
        mid = entry["upstreamId"]
        marker = _read_marker(mid)
        failures = int((marker or {}).get("failures", 0))
        if marker and _marker_live(marker, now):
            state = f"cooled until {time.strftime('%H:%M:%S', time.localtime(float(marker['expiresAt'])))}"
        else:
            state = "ready"
        lines.append(f"{mid}: {state}  {_score_text(entry)}  failures {failures}")
    return lines


def _catalog(text):
    """{CLI id: effort values or None}. Accepts the plain list (`opencode/<id>` lines) or `--verbose`, where
    each header is followed by the model's JSON object, whose `variants` keys are its effort values. An
    entry with no variants is `["none"]`; the plain list carries no variants, so it is None."""
    catalog = {}
    decoder = json.JSONDecoder()
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        match = re.match(r"^opencode/(\S+)\s*$", line)
        if not match:
            continue
        rest = "".join(lines[i + 1:])
        values = None
        if rest.lstrip().startswith("{"):
            try:
                obj, _ = decoder.raw_decode(rest.lstrip())
                values = list((obj.get("variants") or {}).keys()) or ["none"]
            except ValueError:
                values = None
        catalog[match.group(1)] = values
    return catalog


def _live_catalog():
    exe = shutil.which("opencode")
    if not exe:
        raise SyncError("opencode is not on PATH; pass --catalog-file to sync from a saved catalog")
    out = subprocess.run([exe, *CATALOG_ARGS], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         timeout=120)
    if out.returncode != 0:
        raise SyncError(f"`opencode {' '.join(CATALOG_ARGS)}` exited {out.returncode}: {out.stderr.strip()}")
    return out.stdout


def _models_dev(path):
    try:
        if path:
            text = Path(path).read_text(encoding="utf-8")
        else:
            # models.dev answers 403 to urllib's default User-Agent.
            req = urllib.request.Request(MODELS_DEV_URL, headers={"User-Agent": "free_pool/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8")
        data = json.loads(text)
    except (OSError, ValueError) as err:
        raise SyncError(f"models.dev is unreadable: {err}")
    models = ((data.get("opencode") or {}).get("models") if isinstance(data, dict) else None)
    if not isinstance(models, dict):
        raise SyncError("models.dev has no opencode.models object")
    return models


def _rates(entry, cli_id):
    cost, limit = entry.get("cost"), entry.get("limit")
    if not isinstance(cost, dict) or not isinstance(limit, dict):
        raise SyncError(f"models.dev opencode/{cli_id} has no cost or limit block")
    try:
        rates = {"cacheHitPer1M": float(cost["cache_read"]), "cacheMissPer1M": float(cost["input"]),
                 "outputPer1M": float(cost["output"])}
        window, output = int(limit["context"]), int(limit["output"])
    except (KeyError, TypeError, ValueError):
        raise SyncError(f"models.dev opencode/{cli_id} lacks cost.input/output/cache_read or limit.context/output")
    return rates, window, output


def _label(name):
    return re.sub(r"\s+free$", "", name, flags=re.IGNORECASE) + " (CLI)"


def _line_families(data):
    """{product line: family} over every registered row whose line names exactly one family."""
    seen = {}
    for m in data["models"]:
        upstream = str(m.get("upstreamId") or m.get("id") or "").rsplit("/", 1)[-1]
        if upstream and m.get("family"):
            seen.setdefault(mr.product_line(re.sub(r"-free$", "", upstream)), set()).add(m["family"])
    return {line: next(iter(fams)) for line, fams in seen.items() if len(fams) == 1}


def _cli_row(cli_id, entry, values, today, order, data):
    rates, window, output = _rates(entry, cli_id)
    base = cli_id[:-len("-free")]
    line = mr.product_line(base)
    family = _line_families(data).get(line, line)
    alias = "cli-" + re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    if any(alias == m.get("alias") for m in data["models"]):
        raise SyncError(f"the generated alias {alias} for {cli_id} is already registered")
    row = {
        "id": f"opencode-cli/{cli_id}", "upstreamId": cli_id, "alias": alias, "transport": TRANSPORT,
        "label": _label(entry.get("name") or cli_id), "family": family, "version": cli_id,
        "lifecycle": "current", "order": order, "costRank": 0, "prior": "mid",
        "status": {"state": "available"}, "roles": [],
        "limits": {"contextTokens": window, "maxOutputTokens": output, "concurrency": None},
        "price": {**rates, "currency": "USD", "asOf": today,
                  "source": f"opencode models opencode (live CLI catalog); models.dev opencode/{cli_id}"},
        "effort": {"evidence": "unmeasured"},
    }
    if values:
        row["effortValues"] = values
    row["authTier"] = "open"
    row["gate"] = {"minBand": "Surplus"}
    return row


def _row_text(row):
    fields = [f'      {json.dumps(key)}: {json.dumps(row[key])}' for key in ROW_KEYS if key in row]
    return "    {\n" + ",\n".join(fields) + "\n    }"


def _models_array_end(text):
    """Index of the `]` closing the top-level `models` array. Brackets inside strings are skipped."""
    depth, in_string, escaped = 0, False, False
    for i in range(text.index('"models": [') + len('"models": '), len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return i
    raise SyncError("registry has no closing bracket for its models array")


def _append_rows(text, rows):
    """Insert `rows` at the end of the `models` array, in the file's own layout (one field per line)."""
    close = _models_array_end(text)
    last_brace = text.rindex("}", 0, close)
    block = "".join(f",\n{_row_text(row)}" for row in rows)
    return text[:last_brace + 1] + block + text[last_brace + 1:]


def _mark_vanished(text, cli_id, today):
    at = text.index(f'"id": "opencode-cli/{cli_id}"')
    status = re.compile(r'"status": \{"state": "available"\}').search(text, at)
    if status is None:
        raise SyncError(f"opencode-cli/{cli_id} has no single-line available status to mark")
    replacement = '"status": ' + json.dumps({"state": "unavailable",
                                             "reason": f"left the live opencode CLI catalog on {today}"})
    return text[:status.start()] + replacement + text[status.end():]


def _diff(data, path, catalog):
    """(added, vanished) against the registry: each `-free` id in `catalog` with no opencode-cli row, and each
    available pool member the catalog no longer lists."""
    known = {m["upstreamId"] for m in data["models"] if m.get("transport") == TRANSPORT and m.get("upstreamId")}
    added = sorted(cid for cid in catalog if cid not in known)
    vanished = sorted(m["upstreamId"] for m in _members(path) if m["upstreamId"] not in catalog)
    return added, vanished


def catalog_line(registry_path=None, catalog_text=None):
    """The SessionStart line `free catalog: +<id> -<id>` when the live catalog (or `catalog_text`) differs from
    the registry, else "". Writes nothing; an unreadable catalog is a line, never a silent pass."""
    path = Path(registry_path) if registry_path else mr.find_registry()
    try:
        text = catalog_text if catalog_text is not None else _live_catalog()
    except SyncError as err:
        return f"free catalog: unreadable ({err})"
    catalog = {cid for cid in _catalog(text) if cid.endswith("-free")}
    added, vanished = _diff(mr.load(path), path, catalog)
    marks = [f"+{cid}" for cid in added] + [f"-{cid}" for cid in vanished]
    return f"free catalog: {' '.join(marks)}" if marks else ""


def sync(registry_path=None, catalog_file=None, models_dev_file=None, dry_run=False, today=None):
    """Diff the live catalog against the registry's opencode-cli rows and write the difference.

    Adds each `-free` id with no opencode-cli row yet (no `pool`: it is a stealth member until its
    `pubScore` lands). Marks each `pool: free` row whose id left the catalog `unavailable`, dated.
    Returns {"added": [...], "vanished": [...], "intake": [...], "dryRun": bool}.
    """
    path = Path(registry_path) if registry_path else mr.find_registry()
    today = today or date.today().isoformat()
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as err:
        raise SyncError(f"registry unreadable at {path}: {err}")
    catalog_text = Path(catalog_file).read_text(encoding="utf-8") if catalog_file else _live_catalog()
    catalog = {cid: values for cid, values in _catalog(catalog_text).items() if cid.endswith("-free")}
    added, vanished = _diff(data, path, catalog)

    new_rows = []
    if added:
        models = _models_dev(models_dev_file)
        order = max(int(m.get("order", 0)) for m in data["models"]) + 1
        for cid in added:
            if cid not in models:
                raise SyncError(f"models.dev has no opencode/{cid} entry for the new id {cid}")
            new_rows.append(_cli_row(cid, models[cid], catalog[cid], today, order,
                                     {"models": data["models"] + new_rows}))
            order += 1
    updated = _append_rows(text, new_rows) if new_rows else text
    for cid in vanished:
        updated = _mark_vanished(updated, cid, today)

    report = {"added": added, "vanished": vanished,
              "intake": [f"run the bench suite's `intake` subcommand for {row['alias']}"
                         for row in new_rows],
              "dryRun": dry_run}
    if updated == text:
        return report
    try:
        new_data = json.loads(updated)
        mr._validate(new_data, path)
    except (ValueError, mr.RegistryError) as err:
        raise SyncError(f"the synced registry would not validate: {err}")
    if not dry_run:
        path.write_text(updated, encoding="utf-8", newline="\n")
    return report


def _cli_sync(args):
    try:
        report = sync(args.registry, args.catalog_file, args.models_dev_file, args.dry_run)
    except SyncError as err:
        print(f"free_pool: {err}", file=sys.stderr)
        return 2
    verb = "would" if args.dry_run else "did"
    for cid in report["added"]:
        print(f"+{cid} (stealth: no pool until pubScore is set)")
    for cid in report["vanished"]:
        print(f"-{cid} (marked unavailable)")
    for line in report["intake"]:
        print(f"intake: {line}")
    if not report["added"] and not report["vanished"]:
        print("free catalog: no change")
    else:
        print(f"free catalog: {verb} write {len(report['added'])} added, {len(report['vanished'])} vanished")
    return 0


def _cli_status(args):
    for line in status_lines(args.registry):
        print(line)
    print(ready_text(args.registry))
    return 0


def _cli_state(args):
    state, detail = member_state(args.alias, args.registry)
    print(f"{state} {' '.join(detail.split())}".strip())
    return 0


def _cli_cool(args):
    marker = cool(args.member)
    print(f"{args.member}: cooled until {time.strftime('%H:%M:%S', time.localtime(float(marker['expiresAt'])))}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="free_pool.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="ingest the live opencode CLI catalog into the registry")
    s.add_argument("--registry", help="registry file (default: the live registry)")
    s.add_argument("--catalog-file", help="saved `opencode models opencode --verbose` output (default: live)")
    s.add_argument("--models-dev-file", help="saved models.dev api.json (default: fetched)")
    s.add_argument("--dry-run", action="store_true", help="report the diff without writing")
    s.set_defaults(func=_cli_sync)
    st = sub.add_parser("status", help="each pool member's room, published score and failure count")
    st.add_argument("--registry", help="registry file (default: the live registry)")
    st.set_defaults(func=_cli_status)
    sa = sub.add_parser("state", help="one line for a registry alias: ready, cooled <seconds> or unavailable <reason>")
    sa.add_argument("alias", help="the registry alias, e.g. cli-exo")
    sa.add_argument("--registry", help="registry file (default: the live registry)")
    sa.set_defaults(func=_cli_state)
    c = sub.add_parser("cool", help="cool one member: the opencode launcher calls it when the member answers a rate limit")
    c.add_argument("member", help="the member's CLI id, e.g. mimo-v2.6-flash-free")
    c.set_defaults(func=_cli_cool)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
