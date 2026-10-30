#!/usr/bin/env python3
"""One-request liveness probe for an OpenCode Zen model. Exit 0 = the upstream answered.

WHY A PREFLIGHT EXISTS AT ALL. A dispatch that the upstream refuses costs ~220 seconds, not
because the model is slow but because the claude child retries ten times before giving up.
Across a 17-rung ladder on an exhausted free tier that is an hour of wall clock spent
learning one bit. This asks the same question in one request, so the driver can decline to
dispatch instead of paying the retry loop to discover the same refusal seventeen times.

IT IS ADVISORY, AND DEFEATABLE. The probe validates against the real transport -- run against
mimo / big-pickle / muse-spark it returned exactly the errors the sidecar dispatches had
returned (429 FreeUsageLimitError, 429, 500) -- but it is a DIFFERENT CLIENT from the one
litellm uses, so a future upstream could refuse the probe and serve the dispatch. Set
SDF_NO_PREFLIGHT=1 to bypass. A gate that can wedge a whole grid on its own false negative
has to have an off switch.

CLIENT FINGERPRINT IS LOAD-BEARING. A bare urllib request (no User-Agent, no Accept) is
answered by Cloudflare with 403 code 1010 before it ever reaches the provider -- a statement
about the probe, not about the tier, and one that reads as "arm unavailable" if believed.
The headers below are what make this probe measure the upstream.
"""
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

URL = "https://opencode.ai/zen/v1/chat/completions"
REGISTRY = pathlib.Path(__file__).resolve().parents[2] / "tools" / "model_registry.py"


def resolve(alias):
    """alias -> provider model id, via the same registry the launcher resolves through.

    Fails CLOSED to the alias itself: a registry that cannot answer is a configuration fault,
    and refusing to probe would turn it into a permanent grid halt.
    """
    try:
        out = subprocess.run([sys.executable, str(REGISTRY), "sidecar-fields", alias],
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().split("|")[0]
    except Exception:
        pass
    return alias


def main():
    if len(sys.argv) < 2:
        print("usage: preflight_opencode.py <alias-or-model-id>", file=sys.stderr)
        return 2
    model = resolve(sys.argv[1])
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "Reply with the digit 7 and nothing else."}],
                       "max_tokens": 16}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": "Bearer public", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "OpenAI/Python 1.0.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content")
        print(f"PREFLIGHT OK {model} {str(txt)[:40]!r}")
        return 0
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:180].replace("\n", " ")
        print(f"PREFLIGHT REFUSED {model} HTTP {e.code} {detail}")
        return 1
    except Exception as e:
        print(f"PREFLIGHT ERROR {model} {type(e).__name__}: {str(e)[:150]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
