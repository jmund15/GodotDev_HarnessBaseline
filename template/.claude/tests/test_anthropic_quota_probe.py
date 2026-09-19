#!/usr/bin/env python3
"""Proof that Anthropic sidecar quota events feed cross-transport budget bands."""
import importlib.util
import json
import os
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "..", "scripts", "anthropic_quota_probe.py")
spec = importlib.util.spec_from_file_location("anthropic_quota_probe_test", MODULE_PATH)
aq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aq)


def main():
    root = tempfile.mkdtemp(prefix="anthropic_quota_")
    ledger = os.path.join(root, "sidecar.jsonl")
    now = 1_800_000_000
    row = {
        "transport": "anthropic",
        "timestamp": datetime.fromtimestamp(now - 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rateLimitInfo": {
            "unifiedWindows": {
                "five_hour": {"utilization": 0.1, "resetsAt": now + 3600},
                "seven_day": {"utilization": 0.98, "resetsAt": now + 86400},
            }
        },
    }
    with open(ledger, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row) + "\n")
    os.environ["ANTHROPIC_QUOTA_LEDGER"] = ledger
    original = aq.tempfile.gettempdir
    aq.tempfile.gettempdir = lambda: root
    try:
        reading, source = aq.reading(now)
        cases = [
            ("sidecar event is a usable budget source", reading is not None),
            ("fractional utilization becomes percent", reading and reading.get("usedPercent") == 98.0),
            ("sidecar source stays explicit", source and "sidecar.jsonl" in source),
            ("computed band is not unknown", reading and reading.get("band") is not None),
        ]
        unusable_cache = os.path.join(root, "cc-cachestat-current.json")
        with open(unusable_cache, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"rate_limits": {}}, handle)
        os.utime(unusable_cache, (now, now))
        fallback, fallback_source = aq.reading(now)
        cases.append((
            "newer unusable cache does not mask sidecar evidence",
            fallback is not None and fallback_source == ledger,
        ))
        invalid_cache_values = []
        invalid_cache_paths = []
        for index, value in enumerate((False, -0.1, 100.1, float("nan"), float("inf"))):
            invalid_cache = os.path.join(root, "cc-cachestat-invalid-%d.json" % index)
            invalid_cache_paths.append(invalid_cache)
            with open(invalid_cache, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({"rate_limits": {"seven_day": {
                    "used_percentage": value, "resets_at": now + 86400,
                }}}, handle)
            os.utime(invalid_cache, (now + index, now + index))
            invalid_cache_values.append(aq._cache_candidate(now) is None)
        cases.append((
            "malformed cache utilization stays unknown",
            all(invalid_cache_values),
        ))
        for invalid_cache in invalid_cache_paths:
            os.remove(invalid_cache)
        invalid_reset_values = []
        invalid_reset_paths = []
        for index, value in enumerate((False, float("nan"), float("inf"), float("-inf"))):
            invalid_cache = os.path.join(root, "cc-cachestat-reset-%d.json" % index)
            invalid_reset_paths.append(invalid_cache)
            with open(invalid_cache, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({"rate_limits": {"seven_day": {
                    "used_percentage": 98.0, "resets_at": value,
                }}}, handle)
            os.utime(invalid_cache, (now + index, now + index))
            invalid_reset_values.append(aq._cache_candidate(now) is None)
        cases.append((
            "malformed cache reset timestamps stay unknown",
            all(invalid_reset_values),
        ))
        for invalid_cache in invalid_reset_paths:
            os.remove(invalid_cache)
        raced_path = os.path.join(root, "cc-cachestat-vanished.json")
        original_glob = aq.glob.glob
        original_getmtime = aq.os.path.getmtime
        aq.glob.glob = lambda _pattern: [raced_path]

        def racing_getmtime(path):
            if path == raced_path:
                raise FileNotFoundError(path)
            return original_getmtime(path)

        aq.os.path.getmtime = racing_getmtime
        try:
            try:
                raced = aq._cache_candidate(now)
            except OSError:
                raced = "crash"
        finally:
            aq.glob.glob = original_glob
            aq.os.path.getmtime = original_getmtime
        cases.append(("a cache removed after globbing is skipped", raced is None))
        index_path = ledger + ".anthropic-index.json"

        def index_offset():
            try:
                with open(index_path, encoding="utf-8") as handle:
                    return json.load(handle).get("offset")
            except (OSError, ValueError, AttributeError):
                return None

        cases.append((
            "first scan persists a fixed-size incremental index",
            index_offset() == os.path.getsize(ledger),
        ))
        with open(index_path, encoding="utf-8") as handle:
            malformed_index = json.load(handle)
        malformed_index["candidate"] = {"observed": "bad"}
        with open(index_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(malformed_index, handle)
        try:
            recovered, _ = aq.reading(now)
        except Exception:
            recovered = None
        cases.append((
            "a malformed index falls back to the ledger",
            recovered is not None and recovered.get("usedPercent") == 98.0,
        ))
        malformed_candidates = []
        for field, values in (
            ("usedPercent", (False, -0.1, 100.1, float("nan"), float("inf"))),
            ("resetsAt", (False, float("nan"), float("inf"), float("-inf"))),
        ):
            for value in values:
                with open(index_path, encoding="utf-8") as handle:
                    malformed_index = json.load(handle)
                malformed_index["candidate"] = {
                    "observed": now - 60,
                    "limits": {"seven_day": {
                        "usedPercent": 98.0,
                        "resetsAt": now + 86400,
                    }},
                }
                malformed_index["candidate"]["limits"]["seven_day"][field] = value
                with open(index_path, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(malformed_index, handle)
                candidate, _ = aq.reading(now)
                malformed_candidates.append(
                    candidate is not None and candidate.get("usedPercent") == 98.0
                    and candidate.get("resetsAt") == now + 86400
                )
        cases.append((
            "malformed persisted quota evidence rescans the ledger",
            all(malformed_candidates),
        ))
        parsed = []
        original_parser = aq._sidecar_limits

        def counting_parser(value):
            parsed.append(value)
            return original_parser(value)

        aq._sidecar_limits = counting_parser
        unchanged, _ = aq.reading(now)
        aq._sidecar_limits = original_parser
        cases.append((
            "an unchanged ledger does not parse old rows again",
            unchanged is not None and parsed == [],
        ))
        newer = dict(row)
        newer["timestamp"] = datetime.fromtimestamp(now - 30, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        newer["rateLimitInfo"] = {
            "unifiedWindows": {
                "seven_day": {"utilization": 0.75, "resetsAt": now + 86400},
            }
        }
        with open(ledger, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"transport": "codex", "timestamp": newer["timestamp"]}) + "\n")
            handle.write(json.dumps(newer) + "\n")
        incremental, _ = aq.reading(now)
        cases.append((
            "appended rows advance the index and refresh Anthropic evidence",
            incremental is not None
            and incremental.get("usedPercent") == 75.0
            and index_offset() == os.path.getsize(ledger),
        ))
        invalid_utilization = []
        for value in (False, -0.1, 1.1, float("nan"), float("inf")):
            invalid_row = {
                "transport": "anthropic", "timestamp": row["timestamp"],
                "rateLimitInfo": {"unifiedWindows": {"seven_day": {
                    "utilization": value, "resetsAt": now + 86400,
                }}},
            }
            invalid_utilization.append(aq._sidecar_limits(invalid_row) is None)
        cases.append((
            "malformed sidecar utilization stays unknown",
            all(invalid_utilization),
        ))
        invalid_sidecar_resets = []
        for value in (False, float("nan"), float("inf"), float("-inf")):
            invalid_row = {
                "transport": "anthropic", "timestamp": row["timestamp"],
                "rateLimitInfo": {"unifiedWindows": {"seven_day": {
                    "utilization": 0.98, "resetsAt": value,
                }}},
            }
            invalid_sidecar_resets.append(aq._sidecar_limits(invalid_row) is None)
        cases.append((
            "malformed sidecar reset timestamps stay unknown",
            all(invalid_sidecar_resets),
        ))
        row["timestamp"] = datetime.fromtimestamp(
            now - aq.STALE_AFTER_SECONDS - 1, timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(ledger, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row) + "\n")
        stale, _ = aq.reading(now)
        cases.append(("stale sidecar event is rejected", stale is None))
        row["timestamp"] = datetime.fromtimestamp(now - 60, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        row["transport"] = "codex"
        with open(ledger, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row) + "\n")
        foreign, _ = aq.reading(now)
        cases.append(("foreign transport event is rejected", foreign is None))
    finally:
        aq.tempfile.gettempdir = original
        os.environ.pop("ANTHROPIC_QUOTA_LEDGER", None)
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
