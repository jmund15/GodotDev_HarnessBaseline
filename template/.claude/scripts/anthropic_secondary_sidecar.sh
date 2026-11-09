#!/usr/bin/env bash
# anthropic_secondary_sidecar.sh — the `anthropic-secondary` plan's launcher: the same headless Claude
# Code child as anthropic_sidecar.sh, paid by the second Anthropic login (`configDir` in
# .claude/reference/external_models.json). This file is only a name: the base script resolves the
# paying plan from the invoked launcher path, so sourcing it from here is what selects the plan.
# Flags, exit codes and COST: anthropic_sidecar.sh and reference/sidecar_dispatch.md.
_sc_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# An inherited SC_LAUNCHER_DIR that equals SC_LAUNCHER_DIR_RUN is a parent launcher's own export, not a caller's choice.
case "$_sc_here" in */sidecar-snapshots) ;; *) if [ "${SC_LAUNCHER_DIR:-}" = "${SC_LAUNCHER_DIR_RUN:-}" ]; then unset SC_LAUNCHER_DIR; fi ;; esac
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$_sc_here}"
# shellcheck source=anthropic_sidecar.sh
. "$SC_LAUNCHER_DIR/anthropic_sidecar.sh"
