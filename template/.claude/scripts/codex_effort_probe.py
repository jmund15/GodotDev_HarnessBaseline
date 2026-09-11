#!/usr/bin/env python3
"""What model and reasoning effort is a codex session ACTUALLY spending?

`/model` and `/effort` set CLIENT request fields (`model`, `output_config.effort`). Whether they
reach the model depends on how the PROXY was started, and the two regimes look identical from
inside the session. Measured 2026-09-08, same machine, both directions:

  server started with CCP_CODEX_EFFORT=max  -> client "medium" became upstream "max"  (OVERRIDDEN)
  server started plainly                    -> client "low"    stayed  upstream "low" (PASSTHROUGH)

So `/effort` is not inert in general — it is inert exactly when `CCP_CODEX_EFFORT` is set, which is
what the SIDECAR launcher does on every dispatch (one pinned proxy per dispatch). An interactive
proxy started by hand usually sets neither, and there both slash commands work.

The upstream capture is the only evidence that separates the two, and it exists only when the proxy
was started with `CCP_TRAFFIC_LOG=1` — which the sidecar always sets and a hand-started proxy
usually does not.

    python3 .claude/scripts/codex_effort_probe.py            # this session, else newest capture
    python3 .claude/scripts/codex_effort_probe.py --json

Exit 0 with an attested reading, 3 when no capture exists (NOT a failure of this script -- the
answer "structurally unattestable here" is the finding).
"""
import json
import os
import re
import sys

TRAFFIC = os.path.join(os.path.expanduser("~"), ".local", "state", "claude-code-proxy", "traffic")
EFFORT = re.compile(r'"effort"\s*:\s*"([a-z]+)"')
MODEL = re.compile(r'"model"\s*:\s*"([^"]+)"')


def first(pat, path):
    """First capture of `pat` in a file, streamed -- these bodies run to hundreds of KB."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for chunk in iter(lambda: fh.read(65536), ""):
                m = pat.search(chunk)
                if m:
                    return m.group(1)
    except OSError:
        return None
    return None


def _upstream(d):
    """The upstream-request capture. Its stage number shifts (003- vs 004-) with the client body,
    so match the suffix rather than one hard-coded name."""
    for n in sorted(os.listdir(d)):
        if n.endswith("-upstream-request.json"):
            return os.path.join(d, n)
    return None


def newest_request_dir(session_id=None):
    """The newest per-request capture dir, preferring this session's own traffic tree."""
    if not os.path.isdir(TRAFFIC):
        return None
    roots = []
    if session_id:
        own = os.path.join(TRAFFIC, session_id)
        if os.path.isdir(own):
            roots = [own]
    if not roots:
        roots = [os.path.join(TRAFFIC, d) for d in os.listdir(TRAFFIC)
                 if os.path.isdir(os.path.join(TRAFFIC, d))]
    best = None
    for root in roots:
        for d in os.listdir(root):
            p = os.path.join(root, d)
            if not os.path.isdir(p):
                continue
            # A dir with no upstream capture attests nothing; skipping it here keeps the caller
            # from reporting "no effort found" for a request that simply had not finished.
            if not _upstream(p):
                continue
            if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                best = p
    return best


def probe(session_id=None):
    d = newest_request_dir(session_id)
    if not d:
        return {"attested": False,
                "why": "no upstream capture — the proxy was started without CCP_TRAFFIC_LOG=1",
                "dir": None}
    up = _upstream(d)
    cl = os.path.join(d, "002-010-anthropic-request.json")
    ue, ce = first(EFFORT, up), first(EFFORT, cl)
    um, cm = first(MODEL, up), first(MODEL, cl)
    return {"attested": True, "dir": d,
            "upstreamEffort": ue, "clientEffort": ce,
            "upstreamModel": um, "clientModel": cm,
            # Only a DIFFERENCE is definitive. Equal values are passthrough OR an override set
            # to the same value, and no capture can separate those two.
            "effortOverridden": (ce is not None and ue is not None and ce != ue),
            "modelOverridden": (cm is not None and um is not None and cm != um),
            "own": bool(session_id and os.path.basename(os.path.dirname(d)) == session_id)}


def main(argv):
    if "--help" in argv or "-h" in argv:
        print("usage: codex_effort_probe.py [--json]")
        print("Report the active Codex session's attested model and reasoning effort.")
        return 0
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    r = probe(sid)
    # Exit 0 means "this is YOUR attested effort". A capture from a peer session is real evidence
    # about that session and none about this one, so it exits 3 on both paths -- a caller that
    # branches on the exit code must not read a neighbour's reading as its own.
    ok = bool(r["attested"] and r.get("own"))
    if "--json" in argv:
        print(json.dumps(r))
        return 0 if ok else 3

    if not r["attested"]:
        print("MODEL/EFFORT NOT ATTESTABLE on this session.")
        print("  %s." % r["why"])
        print("  `/model` and `/effort` reach the model only if the proxy was started WITHOUT")
        print("  CCP_CODEX_MODEL / CCP_CODEX_EFFORT; with either set, the server overrides you.")
        print("  Both regimes look identical from in here, so this is not guessable.")
        print("  To attest it, restart the proxy with capture on:")
        print("      CCP_TRAFFIC_LOG=1 claude-code-proxy serve")
        print("  ...and add CCP_CODEX_EFFORT=<low|medium|high|xhigh|max> to PIN effort instead.")
        return 3

    up, cl = r["upstreamEffort"], r["clientEffort"]
    scope = "this session" if r["own"] else "another session on this machine — NOT yours"
    print("ATTESTED effort: %s   (model %s, from %s)" % (up or "absent", r["upstreamModel"], scope))
    if r["effortOverridden"]:
        print("  `/effort` is OVERRIDDEN: you asked %r, the server sent %r "
              "(CCP_CODEX_EFFORT is set)." % (cl, up))
    elif cl and up:
        print("  `/effort` agrees (%r): either it passed through, or the server is pinned to the "
              "same value. Change it and re-probe to tell them apart." % cl)
    if r.get("clientModel"):
        cm, um2 = r["clientModel"], r["upstreamModel"]
        if r["modelOverridden"]:
            print("  `/model` is OVERRIDDEN: you asked %r, the server sent %r "
                  "(CCP_CODEX_MODEL is set)." % (cm, um2))
        else:
            print("  `/model` agrees (%r): passed through, or pinned to the same value." % cm)
    print("  evidence: %s" % _upstream(r["dir"]))
    if not r["own"]:
        print("  This is NOT an attestation of your own effort — it is the newest capture on the")
        print("  machine. Relaunch your proxy with CCP_TRAFFIC_LOG=1 to attest this session.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
