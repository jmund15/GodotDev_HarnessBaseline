"""A roster seat names its key and effort; review_ledger fills its mandates and tier from the lens registry."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))

import lens  # noqa: E402
import review_ledger  # noqa: E402

LENS = "sa-architecture-sweep"
MERGED = "sa-robustness-performance+sa-intuitiveness-testability"


def load(seats):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "roster.json"
        path.write_text(json.dumps(seats), encoding="utf-8")
        return review_ledger._load_roster(str(path), review_ledger.load_catalog())


def main():
    failures = []

    seat = load([{"key": LENS, "effort": "high"}])[0]
    if seat.get("mandates") != lens.seat_mandates(LENS):
        failures.append(f"one lens: mandates {seat.get('mandates')} != registry {lens.seat_mandates(LENS)}")
    if seat.get("tier") != lens.seat_tier(LENS):
        failures.append(f"one lens: tier {seat.get('tier')} != registry {lens.seat_tier(LENS)}")

    seat = load([{"key": MERGED, "effort": "medium"}])[0]
    if seat.get("mandates") != lens.seat_mandates(MERGED) or seat.get("tier") != lens.seat_tier(MERGED):
        failures.append(f"merged seat: {seat}")

    seat = load([{"key": LENS, "tier": "fanout", "effort": "high"}])[0]
    if seat.get("tier") != lens.seat_tier(LENS):
        failures.append(f"a written tier overrode the lens tier: {seat.get('tier')}")

    try:
        load([{"key": "not-a-lens", "effort": "high"}])
        failures.append("a seat with no lens and no mandates loaded")
    except review_ledger.ToolError:
        pass

    seat = load([{"key": "code-review", "mandates": [], "tier": "executor", "effort": "high"}])[0]
    if seat.get("tier") != "executor":
        failures.append(f"a non-lens seat lost its written tier: {seat}")

    for line in failures:
        print("FAIL", line)
    print(f"{'FAIL' if failures else 'PASS'} test_review_ledger_roster ({5 - len(failures)}/5)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
