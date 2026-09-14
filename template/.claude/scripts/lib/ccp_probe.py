#!/usr/bin/env python3
"""Liveness and attestation probes for raine/claude-code-proxy.

Split out of codex_proxy_sidecar.sh because both jobs need an HTTP client and a glob over
timestamped capture directories -- shell has neither without adding curl and find to the
launcher's dependency set, while python3 is already load-bearing for the run-record.

Subcommands:
  health <port>            exit 0 iff the proxy answers {"ok": true}
  wait <port> <seconds>    poll until healthy or the budget expires; exit 0 on ready
  freeport                 print a port the OS just confirmed is bindable
  attest <since_epoch>     print "<model>\t<effort>" from the newest upstream capture
                           written after <since_epoch>; exit 1 if there is none
"""
import glob
import json
import os
import socket
import sys
import time
import urllib.request

# `/healthz`, not `/health` -- measured 2026-08-20 against v0.1.35, where `/health` returns
# 404. A launcher polling the wrong path never sees ready and times out on a proxy that came
# up fine, which reads as "the proxy is broken" rather than "the path is wrong".
HEALTH_PATH = "/healthz"

# The proxy writes captures under the XDG state dir even on Windows, where it renders as a
# mixed separator (`C:\Users\x/.local/state/...`) -- glob handles both, os.path.join would not.
TRAFFIC_ROOT = os.path.expanduser("~/.local/state/claude-code-proxy/traffic")


def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=2) as r:
            return json.loads(r.read(256).decode("utf-8", "replace")).get("ok") is True
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
    """Ask the OS for a port, then release it.

    Inherently racy -- another process can claim it in the gap -- but the alternative is a
    fixed port, and a fixed port cannot be per-dispatch: the proxy reads its model and effort
    pins from its OWN environment at startup, so two dispatches wanting different pins need
    two proxies. A lost race surfaces immediately as a bind failure in the proxy log, which is
    a better failure than two dispatches silently sharing one pin.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def attest(since):
    """The proxy's own record of what reached OpenAI.

    `004-*-upstream-request.json` is written server-side and is uninfluenceable by the child,
    which is the whole point: a proxied child cannot report the model that served it (Claude
    Code's system prompt asserts a Claude identity, and a run with the model forced to
    gpt-5.6-luna still reported the child's own pin in modelUsage).

    Newest-after-`since` rather than newest-overall: a concurrent dispatch on the same proxy
    writes into the same tree, and the mtime floor is what keeps this run from attesting to
    a peer's request.
    """
    newest, newest_mt = None, since
    for path in glob.iglob(os.path.join(TRAFFIC_ROOT, "*", "*", "004-*-upstream-request.json")):
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if mt >= newest_mt:
            newest, newest_mt = path, mt
    if not newest:
        return None
    try:
        with open(newest, encoding="utf-8") as fh:
            body = json.load(fh)
    except Exception:
        return None
    effort = (body.get("reasoning") or {}).get("effort")
    return body.get("model"), effort


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "health":
        return 0 if healthy(int(argv[2])) else 1
    if cmd == "wait":
        return 0 if wait_healthy(int(argv[2]), float(argv[3])) else 1
    if cmd == "freeport":
        print(freeport())
        return 0
    if cmd == "attest":
        got = attest(float(argv[2]))
        if not got:
            return 1
        print(f"{got[0] or ''}\t{got[1] or ''}")
        return 0
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
