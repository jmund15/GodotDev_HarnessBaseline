#!/usr/bin/env python3
"""Proof for the native Workflow/Agent provider-capacity decision."""
import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "provider_capacity_guard.py"


def main():
    if not HOOK.is_file():
        print("FAIL provider_capacity_guard.py exists")
        return 1
    spec = importlib.util.spec_from_file_location("provider_capacity_guard_test", HOOK)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

    def reader(status, source="live"):
        return lambda _transport, data=None: {
            "status": status, "sourceKind": source,
            "quota": {"dispatchBand": "On pace"} if status in ("available", "exhausted") else None,
            "balance": {"amount": 1.0, "currency": "USD"} if status == "available" else None,
        }

    cases = [
        ("available native capacity allows", guard.refusal("codex", reader=reader("available")) is None),
        ("cache fallback remains visible but may allow",
         guard.refusal("codex", reader=reader("available", "cache")) is None),
        ("explicit unsupported amount allows free eligibility-only transport",
         guard.refusal("opencode", reader=reader("unsupported", "unsupported")) is None),
        ("exhausted native plan refuses",
         "exhausted" in (guard.refusal("anthropic", reader=reader("exhausted")) or "").lower()),
        ("insufficient native balance refuses",
         "insufficient" in (guard.refusal("deepseek", reader=reader("insufficient")) or "").lower()),
        ("authentication failure refuses",
         "authentication" in (guard.refusal("codex", reader=reader("auth-error")) or "").lower()),
        ("network failure refuses",
         "network" in (guard.refusal("codex", reader=reader("network-error")) or "").lower()),
        ("malformed reading refuses",
         "malformed" in (guard.refusal("codex", reader=reader("malformed")) or "").lower()),
        ("unknown status refuses",
         "unknown" in (guard.refusal("codex", reader=reader("mystery")) or "").lower()),
    ]
    windowed = lambda _transport, data=None: {
        "status": "exhausted", "sourceKind": "live",
        "quota": {"dispatchBand": "Exhausted", "bindingWindow": "five_hour",
                  "windows": [{"name": "five_hour", "usedPercent": 98.0, "resetsAt": 1790280000},
                              {"name": "seven_day", "usedPercent": 56.0, "resetsAt": 1790532000}]}}
    reason = guard.refusal("anthropic", reader=windowed) or ""
    cases.append(("an exhausted refusal names the window, its use and its reset, so the caller knows when to retry",
                  "five_hour" in reason and "98" in reason and "resets" in reason.lower()))
    limited = lambda _transport, data=None: {
        "status": "network-error", "sourceKind": "live", "quota": None, "balance": None,
        "error": {"kind": "rate-limit", "message": "rate limited until 2027-01-15T09:00:00Z"}}
    fallback = lambda _transport, data=None: {
        "status": "network-error", "sourceKind": "live", "quota": None, "balance": None,
        "error": None, "liveFailure": {"kind": "rate-limit", "message": "rate limited until 12:00Z"}}
    reason = guard.refusal("anthropic", reader=limited) or ""
    cases.append(("a rate-limit reading names the rate limit and carries its cooldown end",
                  "rate limited" in reason and "2027-01-15T09:00:00Z" in reason and "network" not in reason.lower()))
    reason = guard.refusal("anthropic", reader=fallback) or ""
    cases.append(("a rate-limit liveFailure names the rate limit too",
                  "rate limited" in reason and "12:00Z" in reason))
    cases.append(("a plain network error keeps the old text",
                  guard.refusal("codex", reader=reader("network-error"))
                  == "Provider network capacity check failed for codex with no valid fallback"))
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
