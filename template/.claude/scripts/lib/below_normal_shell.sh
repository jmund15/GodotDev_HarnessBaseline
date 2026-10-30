#!/usr/bin/env bash
# CLAUDE_CODE_SHELL_PREFIX for a bench arm: the CLI hands every command it spawns (Bash tool, hooks, MCP
# servers) here as one shell string. Git Bash's nice maps to the Windows below-normal priority class and
# children inherit it, so the arm's local work yields to interactive sessions while the CLI keeps its own
# priority and the model's API time stays free of machine load.
exec nice -n 10 bash -c "$1"
