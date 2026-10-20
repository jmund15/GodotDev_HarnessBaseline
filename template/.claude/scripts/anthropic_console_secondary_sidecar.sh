#!/usr/bin/env bash
# anthropic_console_secondary_sidecar.sh — the `anthropic-console-secondary` plan's launcher: the
# same headless Claude Code child as anthropic_sidecar.sh, paid by the secondary Console
# organization's promotional credit through an inference key instead of a login (`credentialVar` in
# .claude/reference/external_models.json). This file is only a name: the base script resolves the
# paying plan from the invoked launcher path, so sourcing it from here is what selects the plan.
#
# SIDECAR_LEDGER_PATH is dropped BEFORE the base script sources lib/sidecar_common.sh, which reads it
# once to compute the default ledger. A Console plan's spend is measured by offsetting the one
# machine-global ledger against its credit, so its rows always land in
# $HOME/.claude/sidecar_ledger.jsonl whatever the caller's environment or -L says (D5).
# Flags, exit codes and COST: anthropic_sidecar.sh and reference/sidecar_dispatch.md.
unset SIDECAR_LEDGER_PATH
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# shellcheck source=anthropic_sidecar.sh
. "$SC_LAUNCHER_DIR/anthropic_sidecar.sh"
