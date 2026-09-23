#!/usr/bin/env bash
# Proof for the live-read retry in tools/provider_capacity.py: a transient probe failure retries
# before the sidecar refuses. Measured 2026-09-15: one 15 s curl timeout voided benchmark cell
# ARM-T7-FLASH-r1 at $0, while the endpoint answered in 0.5 s a minute later.
#   1. one network failure, then a balance -> available on the second try;
#   2. no live answer on any try -> network-error after exactly 3 tries, no cache to fall back to;
#   3. an insufficient balance is an answer, not a failure -> one try;
#   4. a healthy first answer -> available, one try.
# The gate this once lived in (sc_gate_balance in lib/sidecar_common.sh) was retired for the
# normalized sc_gate_capacity runner, so the retry is proven at its new home. The probe adapter is
# injected and time.sleep is planted; every case must produce output.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/../tools/provider_capacity.py"
[ -f "$TOOL" ] || { echo "FAIL: capacity runner missing at $TOOL"; exit 1; }

python3 - "$TOOL" <<'PY'
import importlib.util
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("provider_capacity_retry_test", sys.argv[1])
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

slept = []
pc.time.sleep = lambda seconds: slept.append(seconds)

DATA = {"transports": {"deepseek": {
    "costModel": "marginal-usd",
    "capacityProbe": {"command": "python", "args": ["deepseek.py"], "kind": "balance"},
}}}


def failure():
    return {"schemaVersion": 1, "transport": "deepseek", "costModel": "marginal-usd",
            "sourceKind": "live", "observedAt": "2027-01-15T08:00:00Z", "status": "network-error",
            "error": {"kind": "network", "message": "probe timed out"},
            "liveFailure": None, "quota": None, "balance": None}


def answer(amount):
    return {"schemaVersion": 1, "transport": "deepseek", "costModel": "marginal-usd",
            "sourceKind": "live", "observedAt": "2027-01-15T08:00:00Z",
            "status": "available" if amount > 0 else "insufficient", "error": None,
            "liveFailure": None, "quota": None, "balance": {"amount": amount, "currency": "USD"}}


def probe(answers):
    """Return (status, tries) for a run whose probe returns `answers` in order."""
    tries = []
    cache = Path(tempfile.mkdtemp(prefix="capacity_retry_")) / "capacity.json"

    def runner(_spec, _transport):
        tries.append(1)
        return answers[len(tries) - 1]

    got = pc.reading("deepseek", data=DATA, now=1_800_000_000.0, cache_path=str(cache),
                     runner=runner)
    return "status=%s tries=%d" % (got.get("status"), len(tries))


cases = [
    ("network failure then a balance -> available on try 2", "status=available tries=2",
     probe([failure(), answer(9.57)])),
    ("never answers -> network-error after 3 tries", "status=network-error tries=3",
     probe([failure(), failure(), failure()])),
    ("low balance -> insufficient, no retry", "status=insufficient tries=1",
     probe([answer(0.0)])),
    ("healthy first answer -> available, 1 try", "status=available tries=1",
     probe([answer(9.57)])),
]
failed = 0
for name, want, got in cases:
    if want == got:
        print("OK   %s -> %s" % (name, got))
    else:
        print("FAIL %s: expected '%s', got '%s'" % (name, want, got))
        failed += 1
if slept != [pc.RETRY_SLEEP_SECONDS] * 3:
    print("FAIL retries wait between tries: expected 3 sleeps, got %r" % (slept,))
    failed += 1
else:
    print("OK   each retry waits %ss before the next try" % pc.RETRY_SLEEP_SECONDS)
print("PASS test_sidecar_balance_retry" if not failed else "FAIL test_sidecar_balance_retry")
sys.exit(1 if failed else 0)
PY
