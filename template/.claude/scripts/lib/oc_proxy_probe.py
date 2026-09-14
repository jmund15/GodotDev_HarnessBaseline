#!/usr/bin/env python3
"""Liveness probes for the per-dispatch LiteLLM proxy serving the opencode transport.

Sibling of ccp_probe.py (raine/claude-code-proxy), kept separate rather than generalized
because the two proxies expose DIFFERENT health surfaces: litellm answers at
/health/liveliness with a plain-text body ("I'm alive!", not JSON), and it writes no
traffic captures, so there is no attest subcommand here. Identity on this transport is a
CONFIG property -- the per-dispatch config file pins exactly one deployment under the
requested id, and an unknown id fails routing loudly instead of aliasing -- so the proxy
cannot silently serve a model other than the pin the way the codex one can.

Subcommands:
  freeport                 print a port the OS just confirmed is bindable
  wait <port> <seconds>    poll until healthy or the budget expires; exit 0 on ready

The generous default budget in callers (90s) is not defensive: LiteLLM is a Python
server and its cold start measured 10-15s on this workstation, against ~0.2s for the
Rust codex proxy. Waiting less just re-labels a slow start as a broken proxy.
"""
import json
import socket
import sys
import time
import urllib.request

HEALTH_PATH = "/health/liveliness"


def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def wait_healthy(port, budget_s):
    deadline = time.time() + budget_s
    while time.time() < deadline:
        if healthy(port):
            return True
        time.sleep(0.4)
    return healthy(port)


def freeport():
    # Same race as ccp_probe.freeport; read its docstring for why per-dispatch ports are
    # forced rather than merely tidy.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "freeport":
        print(freeport())
        return 0
    if cmd == "wait":
        ok = len(argv) >= 4 and wait_healthy(int(argv[2]), float(argv[3]))
        return 0 if ok else 1
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
