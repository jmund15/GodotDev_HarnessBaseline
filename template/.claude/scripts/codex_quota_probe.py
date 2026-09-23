#!/usr/bin/env python3
"""Read the Codex plan's remaining allowance and reduce it to a band.

The dual-band gate needs one number the provider will not volunteer: how fast THIS
account is burning its ChatGPT plan quota. `codex app-server` exposes it over
JSON-RPC on stdio (newline-delimited, no `jsonrpc` field): `initialize` ->
`initialized` -> `account/rateLimits/read`. This module is the only place that
speaks that protocol; everything downstream reads the JSON line it prints.

The response nests, and three properties of its shape are load-bearing:

  * TWO windows, and the tightest one is what actually blocks work. Reduce over both.
  * WHICH SLOT holds which window is not fixed by the schema. The 5-hour cap arrived in
    `secondary` on this account and the 7-day in `primary`, but that is an observation,
    not a contract -- read `windowDurationMins`, never the slot name.
  * `usedPercent` is required while `windowDurationMins` and `resetsAt` are NULLABLE, and
    a band needs all three. A window missing either is UNCOMPUTABLE, which is a third
    value and not a band: reported as `unknown`, never collapsed onto Surplus. Surplus is
    the most permissive band, so collapsing would open the gate on the strength of a
    payload that measured nothing.

`credits` is a second axis, not a refinement of the first. Purchased credits keep work
running after the plan allowance is spent, so an exhausted band with `hasCredits: true`
is not a blocked dispatch -- the caller gets both and decides.

Exit codes:
    0   normalized provider outcome printed, including auth/network/malformed outcomes
    2   `codex` binary not found or CLI usage error
CLI:
    codex_quota_probe.py            print the reading as one JSON line
    codex_quota_probe.py --check    availability probe; same exits, quiet on success
    codex_quota_probe.py --raw      print the untouched RPC response (diagnostics)
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from quota_bands import compute_band, worst_of, is_exhausted, EXHAUSTED_BAND  # noqa: E402

TRANSPORT = "codex"
HANDSHAKE_TIMEOUT = 25
READ_TIMEOUT = 30


def find_codex():
    """The executable, or None.

    On Windows an npm global bin is a `.cmd` shim and neither Python's subprocess nor
    Node's spawn appends the extension, so the bare name raises WinError 2 -- which
    surfaces as "codex is not installed" when it is installed and on PATH.
    """
    return (os.environ.get("CODEX_BIN")
            or shutil.which("codex.cmd")
            or shutil.which("codex"))


class AppServer:
    """One `codex app-server` subprocess, spoken to over stdio."""

    def __init__(self, binary):
        self.proc = subprocess.Popen(
            [binary, "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self._lines = []
        self._seen = 0
        self.stderr = []
        threading.Thread(target=self._pump, args=(self.proc.stdout, self._lines), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, self.stderr), daemon=True).start()

    @staticmethod
    def _pump(stream, sink):
        for raw in stream:
            sink.append(raw.rstrip("\n"))

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def await_id(self, req_id, timeout):
        """The response with this id. Notifications interleave and are skipped."""
        end = time.time() + timeout
        while time.time() < end:
            while self._seen < len(self._lines):
                raw = self._lines[self._seen]
                self._seen += 1
                if not raw.strip():
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == req_id:
                    return msg
            time.sleep(0.05)
        return None

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def read_rate_limits(binary):
    """The raw `account/rateLimits/read` result. Raises RuntimeError with the reason."""
    server = AppServer(binary)
    try:
        server.send({"id": 1, "method": "initialize",
                     "params": {"clientInfo": {"name": "harness-quota-probe", "version": "1.0.0"}}})
        if server.await_id(1, HANDSHAKE_TIMEOUT) is None:
            raise RuntimeError("initialize timed out; " + _stderr_hint(server))
        server.send({"method": "initialized"})
        server.send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        msg = server.await_id(2, READ_TIMEOUT)
        if msg is None:
            raise RuntimeError("account/rateLimits/read timed out; " + _stderr_hint(server))
        if "error" in msg:
            raise RuntimeError(f"RPC error: {msg['error']}")
        result = msg.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"unusable result: {result!r}")
        return result
    finally:
        server.close()


def _stderr_hint(server):
    tail = [line for line in server.stderr if line.strip()][-3:]
    return " | ".join(tail) if tail else "no stderr output (is `codex login` still valid?)"


def failure(exc, now=None):
    """Normalize expected app-server failures; callers still reserve exit 2 for missing binaries."""
    text = str(exc)
    lower = text.lower()
    if any(token in lower for token in ("401", "403", "unauthorized", "authentication")):
        status, kind = "auth-error", "auth"
    elif any(token in lower for token in ("timed out", "connection", "os error", "rpc error")):
        status, kind = "network-error", "server" if "rpc error" in lower else "network"
    else:
        status, kind = "malformed", "malformed"
    observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))
    return {
        "schemaVersion": 1, "transport": TRANSPORT, "costModel": "plan-quota",
        "sourceKind": "live", "observedAt": observed, "status": status,
        "error": {"kind": kind, "message": text[:300]}, "liveFailure": None,
        "quota": None, "balance": None,
    }


def summarize(result, now=None):
    """Reduce the RPC result to the shared provider-capacity schema."""
    now = time.time() if now is None else now
    snapshot = result.get("rateLimits") or {}
    windows = []
    for slot in ("primary", "secondary"):
        win = snapshot.get(slot)
        if not isinstance(win, dict):
            continue
        mins = win.get("windowDurationMins")
        pressure, band = compute_band(
            win.get("usedPercent"),
            win.get("resetsAt"),
            mins * 60 if isinstance(mins, (int, float)) else None,
            now,
        )
        name = "five_hour" if mins == 300 else "seven_day" if mins == 10080 else slot
        windows.append({
            "name": name,
            "usedPercent": win.get("usedPercent"),
            "windowDurationMins": mins,
            "resetsAt": win.get("resetsAt"),
            "pressure": round(pressure, 4) if pressure is not None else None,
            "band": band,
        })

    if not windows:
        return failure(ValueError("rateLimits contains no quota windows"), now)
    pressure, legacy_band = worst_of((w["pressure"], w["band"]) for w in windows)
    credits_raw = snapshot.get("credits") or {}
    peaks = [w["usedPercent"] for w in windows if isinstance(w["usedPercent"], (int, float))]
    peak = max(peaks) if peaks else None
    exhausted = is_exhausted(peak, credits_raw)
    if exhausted:
        binding = next((w for w in windows if w["usedPercent"] == peak), None)
    elif pressure is not None:
        binding = next((w for w in windows if w["band"] == legacy_band
                        and w["pressure"] == pressure), None)
    else:
        binding = windows[0] if windows else None
    dispatch_band = EXHAUSTED_BAND if exhausted else legacy_band
    weekly = next((w for w in windows if w["name"] == "seven_day" and w["band"]), None)
    routing_band = weekly["band"] if weekly else dispatch_band
    credit_available = bool(credits_raw.get("unlimited"))
    if credits_raw.get("hasCredits"):
        try:
            credit_available = float(credits_raw.get("balance")) > 0
        except (TypeError, ValueError):
            credit_available = True
    credits = {
        "enabled": bool(credits_raw.get("hasCredits") or credits_raw.get("unlimited")),
        "available": credit_available,
        "balance": credits_raw.get("balance"),
    }
    observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    return {
        "schemaVersion": 1,
        "transport": TRANSPORT,
        "costModel": "plan-quota",
        "sourceKind": "live",
        "observedAt": observed,
        "status": "exhausted" if exhausted else "available",
        "error": None,
        "liveFailure": None,
        "quota": {
            "windows": windows,
            "dispatchBand": dispatch_band,
            "routingBand": routing_band,
            "bindingWindow": binding["name"] if binding else "unknown",
            "credits": credits,
        },
        "balance": None,
        # Compatibility fields until provider_bands and the shell wire migrate.
        "planType": snapshot.get("planType"),
        "limitId": snapshot.get("limitId"),
        "pressure": round(pressure, 4) if pressure is not None else None,
        "band": dispatch_band,
        "bindingWindowMins": binding["windowDurationMins"] if binding else None,
        "resetsAt": binding["resetsAt"] if binding else None,
        "credits": credits_raw,
        "windows": windows,
    }


def main(argv):
    binary = find_codex()
    if binary is None:
        print("codex binary not found; run `npm install -g @openai/codex` and `codex login`",
              file=sys.stderr)
        return 2
    try:
        result = read_rate_limits(binary)
    except (RuntimeError, OSError) as exc:
        print(json.dumps(failure(exc), separators=(",", ":")))
        return 0

    if "--raw" in argv:
        print(json.dumps(result, indent=2))
        return 0
    reading = summarize(result)
    if "--check" in argv:
        # Availability, not a value: the round-trip worked and the payload parsed. An
        # uncomputable band is still a successful probe -- the gate reads `band: null` and
        # refuses on its own terms rather than mistaking a broken login for a full quota.
        return 0
    print(json.dumps(reading))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
