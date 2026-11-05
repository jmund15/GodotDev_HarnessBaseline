#!/usr/bin/env bash
# CLAUDE_CODE_SHELL_PREFIX for a bench arm: the CLI hands every command it spawns (Bash tool, hooks, MCP
# servers) here as one shell string. Git Bash's nice maps to the Windows below-normal priority class and
# children inherit it, so the arm's local work yields to interactive sessions while the CLI keeps its own
# priority and the model's API time stays free of machine load.
# nice is relative and Git Bash reads a below-normal caller as nice 8, where +10 lands on idle; so the work
# moves to nice 10 (below normal) from any higher-priority caller and keeps a lower caller's priority.
n=$(nice)
exec nice -n $(( n < 10 ? 10 - n : 0 )) bash -c "$1"
