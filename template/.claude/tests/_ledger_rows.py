"""Shared proof helper: one review-ledger row in `tools/review_ledger.py`'s row schema.

A proof that plants a covering row builds it here, so a row-schema change is made once. The caller
passes the rule hash, because a proof that derives it independently keeps that derivation its own.
"""


def ledger_row(mandate, rule_hash, head, files, **over):
    """A clean candidate panel row for `mandate` over `files` ({path: blob}) at `head`; `over` replaces fields."""
    row = {"v": 1, "id": "run1:seat:" + mandate, "ts": "2026-09-30T00:00:00Z", "stage": "candidate",
           "subject": "session:s1", "mandate": mandate, "ruleHash": rule_hash, "head": head, "mergeBase": None,
           "reviewer": "panel", "family": "anthropic", "model": "m1", "modelObserved": True, "tier": "executor",
           "effort": "high", "seat": "seat", "seatMandates": [mandate], "files": dict(files), "outcome": "clean",
           "findings": 0, "degraded": False, "authorModel": "unknown", "task": None, "session": "s1",
           "noNewDecision": False}
    row.update(over)
    return row
