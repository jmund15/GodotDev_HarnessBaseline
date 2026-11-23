#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the read/search tool family.

One settings entry runs CHAIN in one interpreter, then the project's own members named in
`adaptation.json` `hook_members.pre_read`. CHAIN holds the shared member:
  - semantic_search_scope_guard.process — a scope its values settle is corrected via `updatedInput`; one that
    leaves the project is blocked.

Member contract: `process(payload)` returns None, `{"deny": text}` or `{"block": text}` (stderr, exit 2),
`{"rewrite": tool_input}` (forwarded as `updatedInput`, merged over earlier rewrites), and `{"note": text}` or
`{"advisory": text}` (joined into one `additionalContext`). Each member self-gates on `tool_name`.

Output contract:
- A block writes stderr and exits 2. An advisory or rewrite exits 0 with `hookSpecificOutput`.
- One member fault stays fail-open and does not disable later members; its import or runtime fault names the
  lost check and repair in `additionalContext`. A project member whose file is absent is silent.

Wired in: settings.json hooks.PreToolUse with matcher
"Read|Grep|Glob|mcp__plugin_semantic-search_semantic-search__search".
The members keep their own `main()` for standalone proofs.
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _optional_hooks  # noqa: E402

CHAIN = (("semantic_search_scope_guard", "advisory"),)


def dispatch(input_data, chain=None):
    """Run the members over one payload. Returns (exit_code, stdout_text, stderr_text)."""
    rewrite, contexts = None, []
    for name, member in (chain if chain is not None else _optional_hooks.chain("pre_read", CHAIN, "advisory")):
        try:
            process = member if callable(member) else importlib.import_module(name).process
            result = process(input_data) or {}
        except Exception as exc:
            contexts.append("%s failed (%s: %s); its checks do not run. Repair hooks/%s.py."
                            % (name, type(exc).__name__, exc, name))
            continue
        blocked = result.get("block") or result.get("deny")
        if blocked:
            return 2, "", str(blocked) + "\n"
        if result.get("rewrite"):
            rewrite = dict(rewrite or {}, **result["rewrite"])
        contexts.extend(str(t) for t in (result.get("note"), result.get("advisory")) if t)
    if not (rewrite or contexts):
        return 0, "", ""
    output = {"hookEventName": "PreToolUse"}
    if rewrite:
        output["updatedInput"] = rewrite
    if contexts:
        output["additionalContext"] = "\n".join(contexts)
    return 0, json.dumps({"hookSpecificOutput": output}), ""


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    code, stdout, stderr = dispatch(input_data)
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()
