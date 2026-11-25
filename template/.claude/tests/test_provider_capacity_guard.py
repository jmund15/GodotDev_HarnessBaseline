#!/usr/bin/env python3
"""Proof for the native Workflow/Agent provider-capacity decision.

`assess` returns the refusal and the warning together, so a caller that reads only the refusal
cannot drop an unreadable meter's warning. Point PROVIDER_CAPACITY_GUARD_UNDER_TEST at a copy of
the hook (with a sibling `tools/`) to run this proof against a mutant.
"""
import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = Path(os.environ.get("PROVIDER_CAPACITY_GUARD_UNDER_TEST")
            or HERE.parent / "hooks" / "provider_capacity_guard.py")


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

    def refused(transport, **kwargs):
        return guard.assess(transport, **kwargs)[0]

    cases = [
        ("available native capacity allows with no warning",
         guard.assess("codex", reader=reader("available")) == (None, None)),
        ("cache fallback remains visible but may allow",
         refused("codex", reader=reader("available", "cache")) is None),
        ("explicit unsupported amount allows free eligibility-only transport",
         refused("opencode", reader=reader("unsupported", "unsupported")) is None),
        ("exhausted native plan refuses",
         "exhausted" in (refused("anthropic", reader=reader("exhausted")) or "").lower()),
        ("insufficient native balance refuses",
         "insufficient" in (refused("deepseek", reader=reader("insufficient")) or "").lower()),
        ("authentication failure refuses",
         "authentication" in (refused("codex", reader=reader("auth-error")) or "").lower()),
        ("a status outside the known set refuses",
         "unknown" in (refused("codex", reader=reader("mystery")) or "").lower()),
    ]
    for status in ("rate-limited", "network-error", "malformed", "unknown"):
        reason, warning = guard.assess("codex", reader=reader(status))
        cases.append(("an unreadable %s reading allows on a warning" % status,
                      reason is None and bool(warning)))
    cases.append(("the unreadable statuses this proof names are provider_capacity.UNREADABLE",
                  {"rate-limited", "network-error", "malformed", "unknown"}
                  == set(guard.provider_capacity.UNREADABLE)))

    def raising(_transport, data=None):
        raise RuntimeError("probe exploded")
    cases.append(("a reader that raises still refuses",
                  "probe exploded" in (refused("codex", reader=raising) or "")))
    windowed = lambda _transport, data=None: {
        "status": "exhausted", "sourceKind": "live",
        "quota": {"dispatchBand": "Exhausted", "bindingWindow": "five_hour",
                  "windows": [{"name": "five_hour", "usedPercent": 98.0, "resetsAt": 1790280000},
                              {"name": "seven_day", "usedPercent": 56.0, "resetsAt": 1790532000}]}}
    reason = refused("anthropic", reader=windowed) or ""
    cases.append(("an exhausted refusal names the window, its use and its reset, so the caller knows when to retry",
                  "five_hour" in reason and "98" in reason and "resets" in reason.lower()))
    limited = lambda _transport, data=None: {
        "status": "network-error", "sourceKind": "live", "quota": None, "balance": None,
        "error": {"kind": "rate-limit", "message": "rate limited until 2027-01-15T09:00:00Z"}}
    fallback = lambda _transport, data=None: {
        "status": "network-error", "sourceKind": "live", "quota": None, "balance": None,
        "error": None, "liveFailure": {"kind": "rate-limit", "message": "rate limited until 12:00Z"}}
    allowed, warning = guard.assess("anthropic", reader=limited)
    cases.append(("a rate-limit reading allows, and its warning carries its cooldown end",
                  allowed is None and "rate limited until 2027-01-15T09:00:00Z" in (warning or "")))
    allowed, warning = guard.assess("anthropic", reader=fallback)
    cases.append(("a rate-limit liveFailure allows, and its warning names the cooldown",
                  allowed is None and "rate limited until 12:00Z" in (warning or "")))
    unknown = lambda _transport, data=None: {
        "status": "unknown", "sourceKind": "live", "quota": None, "balance": None,
        "error": {"message": "quota endpoint unavailable"}}
    allowed, warning = guard.assess("codex", reader=unknown)
    cases.append(("an unreadable reading's warning names its status and stated reason",
                  allowed is None and "unknown" in (warning or "") and "quota endpoint unavailable" in (warning or "")))
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
