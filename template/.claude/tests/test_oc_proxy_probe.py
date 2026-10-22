"""Proof for lib/oc_proxy_probe.py: `freeport` prints a bindable loopback port, `wait` returns ready against a planted
loopback health answer and fails when nothing listens, and a bad subcommand is refused. The health server is a
loopback http.server on this proof's own port; no network leaves the machine."""
import http.server
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".claude" / "scripts" / "lib" / "oc_proxy_probe.py"


def run(*args):
    return subprocess.run([sys.executable, str(PROBE), *args], capture_output=True, text=True, timeout=120)


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"I'm alive!"
        self.send_response(200 if self.path == "/health/liveliness" else 404)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def contract(temp_root):
    port_run = run("freeport")
    assert port_run.returncode == 0 and 0 < int(port_run.stdout.strip()) < 65536, (port_run.returncode, port_run.stdout)

    port = free_port()
    silent = run("wait", str(port), "1")
    assert silent.returncode == 1, (silent.returncode, silent.stderr[-300:])

    server = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ready = run("wait", str(server.server_address[1]), "5")
        assert ready.returncode == 0, (ready.returncode, ready.stderr[-300:])
    finally:
        server.shutdown()
        server.server_close()

    bad = run("bogus")
    assert bad.returncode == 2, bad.returncode


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="oc_proxy_probe_proof_") as tmp:
        contract(tmp)
    print("ok oc_proxy_probe.py")
