#!/usr/bin/env python3
"""
Hook: PreToolUse on Agent|Workflow — deny dispatch tools inside subagent sessions.

Delegates execute; they do not delegate (orchestration §0 and §11). A delegate-spawned agent bypasses PINS logging and /orchestration_metrics,
inherits the delegate's model with no effort pin, and can violate single-flight
(tests/LSP) inside one workflow slot. Deny; the delegate reports the fan-out need
under couldNotSatisfy and the orchestrator dispatches it pinned.

Two kinds of delegate, one rule, two detections:

  in-session subagent   transcript_path lives under a `subagents` directory.
  sidecar child         CLAUDE_CODE_SIDECAR is set. A sidecar runs as a SEPARATE `claude`
                        process with its own transcript, so it never carries the
                        `/subagents/` segment and the path check alone lets it dispatch
                        freely. Both launchers export the var.

Main-session calls carry neither signal and pass untouched. With no transcript_path and no
env var this guard cannot classify, so it passes (fail open) — re-verify both inputs on
harness upgrades.

The CLI `--disallowedTools` list is the companion layer, not a duplicate: it holds in the
`bare`/`pointer` disclosure tiers, where no project hooks fire and this guard cannot run.

Wired in: settings.json hooks.PreToolUse, "Workflow|Agent" matcher block.
"""

import json
import os
import sys


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Agent", "Workflow"):
        sys.exit(0)

    transcript = (input_data.get("transcript_path") or "").replace("\\", "/")
    is_subagent = "/subagents/" in transcript
    is_sidecar = bool(os.environ.get("CLAUDE_CODE_SIDECAR"))
    if not (is_subagent or is_sidecar):
        sys.exit(0)
    role = "delegated agent" if is_subagent else "sidecar child"

    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Dispatch tools are orchestrator-only: you are a {role}, and a "
                "delegate-spawned agent runs unpinned, unlogged, and outside the concurrency "
                "cap. Do the work directly with your own tools; if it genuinely needs a "
                "fan-out, finish what you can and report the need under couldNotSatisfy."
            ),
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
