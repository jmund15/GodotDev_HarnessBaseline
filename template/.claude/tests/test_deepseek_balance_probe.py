#!/usr/bin/env python3
"""Proof that DeepSeek's live balance endpoint maps into the shared capacity schema."""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deepseek_balance_probe.py"


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload = {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "6.74"}]}
    seen_auth = None

    def do_GET(self):
        type(self).seen_auth = self.headers.get("Authorization")
        body = json.dumps(type(self).payload).encode("utf-8") if not isinstance(type(self).payload, bytes) else type(self).payload
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


# Assembled so the baseline secret scan never reads a fixture value as a credential.
FAKE_BEARER = "fixture-" + "bearer-" + "value"


def run(url):
    env = dict(os.environ, DEEPSEEK_BALANCE_URL=url, PROVIDER_CAPACITY_TOKEN=FAKE_BEARER,
               PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=20)
    try:
        payload = json.loads(result.stdout)
    except Exception:
        payload = None
    return result, payload


def main():
    if not SCRIPT.is_file():
        print("FAIL deepseek_balance_probe.py exists")
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/user/balance" % server.server_port
    cases = []
    try:
        Handler.status = 200
        Handler.payload = {"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": "2.00"},
            {"currency": "USD", "total_balance": "6.74"},
        ]}
        ok_run, available = run(url)
        cases.append(("live response exits zero", ok_run.returncode == 0))
        cases.append(("bearer token reaches the endpoint", Handler.seen_auth == "Bearer " + FAKE_BEARER))
        cases.append(("USD provider balance is normalized",
                      available and available.get("balance") == {"amount": 6.74, "currency": "USD"}))
        cases.append(("live available schema", available and available.get("status") == "available"
                      and available.get("sourceKind") == "live" and available.get("error") is None))
        cases.append(("credential never appears in output", FAKE_BEARER not in (ok_run.stdout + ok_run.stderr)))

        Handler.status = 401
        Handler.payload = {"error": "unauthorized"}
        auth_run, auth = run(url)
        cases.append(("401 is a typed auth outcome", auth_run.returncode == 0
                      and auth and auth.get("status") == "auth-error"
                      and auth.get("error", {}).get("kind") == "auth"))

        Handler.status = 503
        Handler.payload = {"error": "busy"}
        server_run, unavailable = run(url)
        cases.append(("5xx is a typed server/network outcome", server_run.returncode == 0
                      and unavailable and unavailable.get("status") == "network-error"
                      and unavailable.get("error", {}).get("kind") == "server"))

        Handler.status = 200
        Handler.payload = b"not-json"
        bad_run, malformed = run(url)
        cases.append(("malformed response is typed", bad_run.returncode == 0
                      and malformed and malformed.get("status") == "malformed"))
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
