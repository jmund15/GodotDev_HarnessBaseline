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
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
