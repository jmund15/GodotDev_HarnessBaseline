#!/usr/bin/env python3
"""Proof: the codex sidecar's `claude` child waits at least as long as the text-only shim waits on the proxy.

A compaction request crosses the shim, which holds the client with pings while the proxy runs its server
compaction. Claude Code ends any request at API_TIMEOUT_MS (default 600000 ms) and re-sends it, superseding
the proxy's attempt, so the child's API_TIMEOUT_MS must cover the shim's own upstream bound.

    python3 .claude/tests/test_codex_child_timeout.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import text_only_shim as shim  # noqa: E402


def child_env_block(text):
    """The `env` assignments of run_claude(), up to the child invocation."""
    m = re.search(r"run_claude\(\) \{(.*?)\"\$SC_CLAUDE\" -p", text, re.S)
    return m.group(1) if m else ""


def main():
    launcher = (ROOT / "scripts" / "codex_proxy_sidecar.sh").read_text(encoding="utf-8")
    block = child_env_block(launcher)
    fails = 0
    def ck(name, cond, detail=""):
        nonlocal fails
        print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"\n        {detail}"))
        fails += 0 if cond else 1
    ck("run_claude's env block is found", bool(block), "no run_claude() env block before the child call")
    m = re.search(r"API_TIMEOUT_MS=\"?(\d+)", block)
    ck("the codex child sets API_TIMEOUT_MS", m is not None, block[:300])
    if m:
        ck("API_TIMEOUT_MS covers the shim's upstream bound",
           int(m.group(1)) >= shim.Shim.upstream_timeout_s * 1000,
           f"{m.group(1)} ms < {shim.Shim.upstream_timeout_s} s")
    # The client's stream watchdogs end a request that gets no parsed content event for their window
    # (default 600000 ms), pings or not, so each must cover the shim's bound too.
    for var in ("CLAUDE_STREAM_IDLE_TIMEOUT_MS", "CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS"):
        w = re.search(var + r"=\"?(\d+)", block)
        ck(f"the codex child sets {var}", w is not None, block[:300])
        if w:
            ck(f"{var} covers the shim's upstream bound",
               int(w.group(1)) >= shim.Shim.upstream_timeout_s * 1000,
               f"{w.group(1)} ms < {shim.Shim.upstream_timeout_s} s")
    # negative control: a block without the variable is caught
    planted = child_env_block('run_claude() {\n  env CLAUDE_CODE_ENTRYPOINT=cli \\\n    "$SC_CLAUDE" -p')
    ck("NEGATIVE: an env block without API_TIMEOUT_MS is detected",
       bool(planted) and re.search(r"API_TIMEOUT_MS=\"?(\d+)", planted) is None)
    print(f"\ntest_codex_child_timeout: {'0 failed' if not fails else f'{fails} failed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
