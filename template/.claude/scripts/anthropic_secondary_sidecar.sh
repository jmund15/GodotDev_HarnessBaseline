#!/usr/bin/env bash
# anthropic_secondary_sidecar.sh — the `anthropic-secondary` plan's launcher: the same headless Claude
# Code child as anthropic_sidecar.sh, paid by the second Anthropic login (`configDir` in
# .claude/reference/external_models.json). This file is only a name: the base script resolves the
# paying plan from the invoked launcher path, so sourcing it from here is what selects the plan.
# Flags, exit codes and COST: anthropic_sidecar.sh and reference/sidecar_dispatch.md.
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# shellcheck source=anthropic_sidecar.sh
. "$SC_LAUNCHER_DIR/anthropic_sidecar.sh"
