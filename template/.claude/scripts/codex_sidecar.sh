#!/usr/bin/env bash
# codex_sidecar.sh — RETIRED 2026-08-20. The codex transport has ONE launcher:
#
#   .claude/scripts/codex_proxy_sidecar.sh     (same flag surface; registry `launcher` field)
#
# Why retired, so nobody resurrects it without weighing both:
#   1. `codex exec` receives the assembled prompt as a command-line ARGUMENT. On Windows the
#      .cmd shim tops out at ~8K chars, so any real dispatch — a mandate plus one -C context
#      file — dies with "The command line is too long" before anything runs. The proxy path
#      hands the prompt to a `claude` child intact at any size.
#   2. The `codex exec` child loads none of `.claude` — no CLAUDE.md, skills, memory, hooks,
#      or MCP — so every dispatch relied on hand-curated pointer discipline the proxy path
#      gets for free at `-D full`.
#
# Sidecar agent types (-D disclosure × -G shape recipes): reference/sidecar_dispatch.md.
# The pre-retirement implementation (flag translations, --sandbox mapping, JSONL parse) is in
# git history at this path, tag point: fix(sidecar) 26393ee81 era.

echo "codex_sidecar.sh is RETIRED (2026-08-20): 'codex exec' passes the prompt as argv and dies on the Windows command-line length limit, and its child loads none of the .claude harness." >&2
echo "Dispatch with the same flags via: bash .claude/scripts/codex_proxy_sidecar.sh" >&2
exit 2
