#!/usr/bin/env python3
"""Read this machine's Anthropic plan allowance and reduce it to a band.

Claude Code statusline cache files and completed Anthropic sidecars both carry first-party
`rate_limit_info`. The probe selects the freshest usable reading. Sidecar events make Anthropic
posture visible from a non-Anthropic host without issuing another paid request. A fixed-size index
stores the last complete ledger offset, so later probes parse only appended rows.

WHY A HOST TRANSPORT NEEDS A PROBE AT ALL: the `anthropic` row is dispatchable FROM another
session. On a codex-hosted session, "how much Anthropic allowance is left" is a real provider
question with a real answer, and without a probe the transport drops silently out of
`provider_bands.slacker_than()` at its `if not band: continue` branch -- present in the roster,
invisible to every currency comparison. The same call from an Anthropic session compares the
transport against itself, which is incoherent; `provider_bands` excludes the caller's own
transport rather than having this probe lie about it.

`seven_day` only: it is the window that governs provider choice (`orchestration` §5b). The
5-hour window governs fan-out width, which is not a transport-selection question.

UNCOMPUTABLE IS NOT A BAND. A payload missing `usedPercent` or `resetsAt` reports
`band: null`, never Surplus -- Surplus is the most permissive band, so collapsing onto it would
open a gate on a payload that measured nothing (the rule `codex_quota_probe.py` states for the
same reason).

Exit codes:
    0   reading printed as one JSON line on stdout
    1   no fresh usable cache or Anthropic sidecar rate-limit event
CLI:
    anthropic_quota_probe.py            print the reading as one JSON line
    anthropic_quota_probe.py --check    availability probe; same exits, quiet on success
    anthropic_quota_probe.py --raw      print the selected local rate-limit block (diagnostics)
"""

import glob
import json
import os
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from quota_bands import compute_band  # noqa: E402

TRANSPORT = "anthropic"
SEVEN_DAY_SECONDS = 7 * 24 * 3600
# Beyond this the reading is another session's, not this one's.
STALE_AFTER_SECONDS = 5 * 3600


def _epoch(value):
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _cache_candidate(now):
    paths = sorted(
        glob.glob(os.path.join(tempfile.gettempdir(), "cc-cachestat-*.json")),
        key=os.path.getmtime,
        reverse=True,
    )
    for path in paths:
        age = now - os.path.getmtime(path)
        if not (-300 <= age < STALE_AFTER_SECONDS):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                limits = (json.load(fh) or {}).get("rate_limits")
        except Exception:
            continue
        seven = limits.get("seven_day") if isinstance(limits, dict) else None
        if not isinstance(seven, dict):
            continue
        used = seven.get("used_percentage", seven.get("usedPercent"))
        resets = _epoch(seven.get("resets_at", seven.get("resetsAt")))
        if not isinstance(used, (int, float)) or resets is None:
            continue
        normalized = dict(limits)
        normalized["seven_day"] = {"usedPercent": used, "resetsAt": resets}
        return normalized, path, os.path.getmtime(path)
    return None


def _sidecar_limits(row):
    if not isinstance(row, dict) or row.get("transport") != TRANSPORT:
        return None
    info = row.get("rateLimitInfo")
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    seven = windows.get("seven_day") if isinstance(windows, dict) else None
    observed = _epoch(row.get("timestamp"))
    if not isinstance(seven, dict) or observed is None:
        return None
    used = seven.get("utilization")
    resets = _epoch(seven.get("resetsAt"))
    if not isinstance(used, (int, float)) or resets is None:
        return None
    return {
        "limits": {
            "seven_day": {"usedPercent": used * 100.0, "resetsAt": resets},
            "planType": info.get("planType"),
        },
        "observed": observed,
    }


def _quota_index_path(ledger):
    return os.environ.get("ANTHROPIC_QUOTA_INDEX", ledger + ".anthropic-index.json")


def _load_quota_index(index_path, ledger, stat):
    try:
        with open(index_path, encoding="utf-8") as handle:
            index = json.load(handle)
    except Exception:
        return None
    if not isinstance(index, dict) or index.get("version") != 1:
        return None
    if index.get("ledger") != os.path.abspath(ledger):
        return None
    if index.get("device") != stat.st_dev or index.get("inode") != stat.st_ino:
        return None
    offset = index.get("offset")
    if not isinstance(offset, int) or not 0 <= offset <= stat.st_size:
        return None
    if offset == stat.st_size and index.get("mtimeNs") != stat.st_mtime_ns:
        return None
    candidate = index.get("candidate")
    if candidate is not None and (
        not isinstance(candidate, dict)
        or not isinstance(candidate.get("observed"), (int, float))
        or not isinstance(candidate.get("limits"), dict)
    ):
        return None
    return index


def _write_quota_index(index_path, index):
    directory = os.path.dirname(index_path) or "."
    temp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".anthropic-index-", dir=directory, text=True)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(index, handle, separators=(",", ":"))
            handle.write("\n")
        os.replace(temp_path, index_path)
    except Exception:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _sidecar_candidate(now):
    path = os.environ.get(
        "ANTHROPIC_QUOTA_LEDGER",
        os.path.join(os.path.expanduser("~"), ".claude", "deepseek_spend.jsonl"),
    )
    try:
        stat = os.stat(path)
    except OSError:
        return None

    index_path = _quota_index_path(path)
    index = _load_quota_index(index_path, path, stat)
    offset = index["offset"] if index else 0
    best = index.get("candidate") if index else None
    snapshot_size = stat.st_size
    next_offset = offset
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            block = handle.read(snapshot_size - offset)
    except OSError:
        return None

    complete = block.rfind(b"\n")
    if complete >= 0:
        for raw in block[:complete].splitlines():
            try:
                candidate = _sidecar_limits(json.loads(raw.decode("utf-8")))
            except Exception:
                continue
            if candidate and (best is None or candidate["observed"] > best["observed"]):
                best = candidate
        next_offset += complete + 1

    _write_quota_index(index_path, {
        "version": 1,
        "ledger": os.path.abspath(path),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "offset": next_offset,
        "mtimeNs": stat.st_mtime_ns,
        "candidate": best,
    })
    if best is None:
        return None
    age = now - best["observed"]
    if not (-300 <= age < STALE_AFTER_SECONDS):
        return None
    return best["limits"], path, best["observed"]


def read_rate_limits(now=None):
    now = time.time() if now is None else now
    candidates = [item for item in (_cache_candidate(now), _sidecar_candidate(now)) if item]
    if not candidates:
        return None, None
    limits, path, _observed = max(candidates, key=lambda item: item[2])
    return limits, path


def reading(now=None):
    now = time.time() if now is None else now
    rl, path = read_rate_limits(now)
    if not isinstance(rl, dict):
        return None, path
    seven = rl.get("seven_day")
    if not isinstance(seven, dict):
        return None, path
    used = seven.get("used_percentage", seven.get("usedPercent"))
    resets = seven.get("resets_at", seven.get("resetsAt"))
    # compute_band returns (pressure, band_name); provider_bands.slacker_than reads `band` as a
    # STRING and `pressure` as its own field, so unpack rather than emitting the tuple. An
    # uncomputable window yields (None, None) and `band: null` reaches the caller as unknown.
    pressure, band = (None, None)
    if used is not None and resets is not None:
        pressure, band = compute_band(used, resets, SEVEN_DAY_SECONDS, now)
    return {
        "transport": TRANSPORT,
        "band": band,
        "pressure": pressure,
        "usedPercent": used,
        "windowDurationMins": SEVEN_DAY_SECONDS // 60,
        "resetsAt": resets,
        "planType": rl.get("plan_type") or rl.get("planType"),
        "source": path,
    }, path


def main(argv):
    raw = "--raw" in argv
    check = "--check" in argv
    if raw:
        rl, path = read_rate_limits()
        print(json.dumps({"source": path, "rate_limits": rl}))
        return 0 if rl else 1
    r, path = reading()
    if r is None:
        if not check:
            where = path or "no fresh cache or Anthropic sidecar rate-limit event"
            print(json.dumps({"transport": TRANSPORT, "band": None,
                              "error": "no usable rate_limits block", "source": where}))
        return 1
    if not check:
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
