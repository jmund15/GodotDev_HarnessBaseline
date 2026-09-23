#!/usr/bin/env python3
"""Read DeepSeek's live account balance into the shared provider-capacity schema."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TRANSPORT = "deepseek"
COST_MODEL = "marginal-usd"
DEFAULT_URL = "https://api.deepseek.com/user/balance"


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def outcome(status, *, balance=None, error=None):
    return {
        "schemaVersion": 1,
        "transport": TRANSPORT,
        "costModel": COST_MODEL,
        "sourceKind": "live",
        "observedAt": _utc(),
        "status": status,
        "error": error,
        "liveFailure": None,
        "quota": None,
        "balance": balance,
    }


def summarize(payload):
    if not isinstance(payload, dict):
        return outcome("malformed", error={"kind": "malformed", "message": "response is not an object"})
    infos = payload.get("balance_infos")
    if not isinstance(infos, list):
        return outcome("malformed", error={"kind": "malformed", "message": "balance_infos is missing"})
    usd = next((row for row in infos if isinstance(row, dict)
                and str(row.get("currency") or "").upper() == "USD"), None)
    if usd is None:
        return outcome("malformed", error={"kind": "malformed", "message": "USD balance is missing"})
    try:
        amount = float(usd.get("total_balance"))
    except (TypeError, ValueError):
        return outcome("malformed", error={"kind": "malformed", "message": "USD balance is not numeric"})
    if amount < 0 or amount != amount or amount in (float("inf"), float("-inf")):
        return outcome("malformed", error={"kind": "malformed", "message": "USD balance is invalid"})
    available = payload.get("is_available") is not False and amount > 0
    return outcome("available" if available else "insufficient",
                   balance={"amount": amount, "currency": "USD"})


def read_live(url, token, timeout=10):
    if not token:
        return outcome("auth-error", error={"kind": "auth", "message": "DeepSeek API credential is missing"})
    request = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": "provider-capacity/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return outcome("auth-error", error={"kind": "auth", "message": "DeepSeek authentication failed"})
        return outcome("network-error", error={
            "kind": "server" if exc.code >= 500 else "network",
            "message": "DeepSeek balance endpoint returned HTTP %d" % exc.code,
        })
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return outcome("network-error", error={"kind": "network", "message": str(exc)[:300]})
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        return outcome("malformed", error={"kind": "malformed", "message": "DeepSeek balance response is not JSON"})
    return summarize(payload)


def main():
    url = os.environ.get("DEEPSEEK_BALANCE_URL") or DEFAULT_URL
    token = os.environ.get("PROVIDER_CAPACITY_TOKEN") or os.environ.get("DEEPSEEK_API_KEY")
    value = read_live(url, token)
    print(json.dumps(value, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
