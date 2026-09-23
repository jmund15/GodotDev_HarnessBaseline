#!/usr/bin/env python3
"""Proof for Anthropic's live OAuth usage capacity adapter."""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "anthropic_capacity_probe.py"


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload = {}
    headers_seen = {}

    def do_GET(self):
        type(self).headers_seen = dict(self.headers)
        raw = type(self).payload
        body = json.dumps(raw).encode("utf-8") if not isinstance(raw, bytes) else raw
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def base_payload(extra_enabled=False):
    return {
        "five_hour": {"utilization": 0.0, "resets_at": None},
        "seven_day": {"utilization": 100.0, "resets_at": "2027-01-20T18:00:00+00:00"},
        "limits": [
            {"group": "weekly", "kind": "weekly_all", "percent": 100,
             "resets_at": "2027-01-20T18:00:00+00:00", "is_active": True,
             "severity": "critical", "scope": None},
            {"group": "weekly", "kind": "weekly_scoped", "percent": 75,
             "resets_at": "2027-01-20T18:00:00+00:00", "is_active": False,
             "severity": "warning", "scope": {"model": {"display_name": "Fable"}}},
        ],
        "extra_usage": {
            "is_enabled": extra_enabled, "user_disabled": not extra_enabled,
            "spend_limit_reached": False, "used_credits": 0,
        },
    }


def run(url, cred_path):
    env = dict(os.environ, ANTHROPIC_USAGE_URL=url,
               ANTHROPIC_CREDENTIALS_PATH=str(cred_path), PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=20)
    try:
        payload = json.loads(result.stdout)
    except Exception:
        payload = None
    return result, payload


def main():
    if not SCRIPT.is_file():
        print("FAIL anthropic_capacity_probe.py exists")
        return 1
    root = Path(tempfile.mkdtemp(prefix="anthropic_capacity_"))
    cred = root / "credentials.json"
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "secret-oauth-token"}}), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/api/oauth/usage" % server.server_port
    cases = []
    try:
        Handler.status = 200
        Handler.payload = base_payload(False)
        spent_run, spent = run(url, cred)
        headers = {key.lower(): value for key, value in Handler.headers_seen.items()}
        cases.append(("live OAuth endpoint exits zero", spent_run.returncode == 0))
        cases.append(("OAuth and first-party beta headers reach the endpoint",
                      headers.get("authorization") == "Bearer secret-oauth-token"
                      and "oauth-2025-04-20" in headers.get("anthropic-beta", "")))
        cases.append(("spent weekly allowance is exhausted",
                      spent and spent.get("status") == "exhausted"
                      and spent.get("quota", {}).get("dispatchBand") == "Exhausted"))
        cases.append(("five-hour and seven-day windows are preserved",
                      {w.get("name") for w in spent.get("quota", {}).get("windows", [])}
                      == {"five_hour", "seven_day"}))
        cases.append(("seven-day window owns routing band",
                      spent.get("quota", {}).get("routingBand")
                      == next(w for w in spent["quota"]["windows"] if w["name"] == "seven_day")["band"]))
        cases.append(("credential never appears in output",
                      "secret-oauth-token" not in (spent_run.stdout + spent_run.stderr)))

        Handler.payload = base_payload(True)
        credit_run, credited = run(url, cred)
        cases.append(("usable extra usage excuses plan exhaustion", credit_run.returncode == 0
                      and credited.get("status") == "available"
                      and credited.get("quota", {}).get("credits", {}).get("available") is True))

        Handler.status = 401
        Handler.payload = {"error": "unauthorized"}
        auth_run, auth = run(url, cred)
        cases.append(("401 is a typed auth outcome", auth_run.returncode == 0
                      and auth.get("status") == "auth-error"))

        Handler.status = 503
        Handler.payload = {"error": "busy"}
        server_run, unavailable = run(url, cred)
        cases.append(("5xx is a typed server outcome", server_run.returncode == 0
                      and unavailable.get("status") == "network-error"
                      and unavailable.get("error", {}).get("kind") == "server"))

        Handler.status = 200
        Handler.payload = b"not-json"
        bad_run, malformed = run(url, cred)
        cases.append(("malformed response is typed", bad_run.returncode == 0
                      and malformed.get("status") == "malformed"))
    finally:
        server.shutdown()
        server.server_close()

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
