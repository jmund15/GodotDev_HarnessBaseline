#!/usr/bin/env bash
# =============================================================================
# {{PROJECT_NAME}} Cloud Environment Install
# =============================================================================
# Called automatically by session_context_loader.py when CLAUDE_CODE_REMOTE=true
# and dependencies are missing. Can also be run manually.
#
# Design: Each step is independent and non-fatal. A network failure in step 1
# does NOT prevent steps 2-9 from running. Summary reports at the end.
#
# Proxy domains required (add to Claude Code web allowlist):
#   builds.dotnet.microsoft.com  — .NET SDK download
#   dotnetcli.azureedge.net      — .NET SDK CDN (fallback)
#   github.com                   — Godot download, gh CLI download, submodules
#   registry.npmjs.org           — dreb semantic-search plugin / npx tooling
#
# What you LOSE on cloud (inherently local):
#   - Godot MCP (run_project, get_debug_output, create_scene, add_node, etc.)
#   - Obsidian MCP (vault access, design doc search)
#   - Visual playtesting
#   - csharp-ls plugin (needs .NET 10)
#
# What still WORKS on cloud:
#   - All code editing, reading, searching
#   - dotnet build + dotnet test (full GdUnit4 test suite)
#   - Git operations + GitHub PRs
#   - Auto-memory (file-based, in-repo; semantic-search recall)
#   - Context7 plugin (documentation lookup)
#   - All hooks, skills, and commands
#   - WebSearch / WebFetch
# =============================================================================

set -u  # Catch undefined variables, but do NOT exit on errors (steps are independent)

# ── Guard: only run on cloud ─────────────────────────────────────────────────
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    echo "Not a cloud environment (CLAUDE_CODE_REMOTE != true). Skipping."
    exit 0
fi

# ── Configuration ─────────────────────────────────────────────────────────────
GODOT_VERSION="4.6.2"
GODOT_RELEASE="stable"
DOTNET_VERSION_PROJECT="9.0"

# Derived paths
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GODOT_INSTALL_DIR="${HOME}/.local/godot"
GODOT_BIN="${GODOT_INSTALL_DIR}/Godot_v${GODOT_VERSION}-${GODOT_RELEASE}_mono_linux_x86_64/Godot_v${GODOT_VERSION}-${GODOT_RELEASE}_mono_linux.x86_64"

echo "================================================================="
echo "  {{PROJECT_NAME}} Cloud Install"
echo "  Project: ${PROJECT_ROOT}"
echo "================================================================="
echo ""

# ── Helpers ───────────────────────────────────────────────────────────────────
FAILED_STEPS=()
STEP_COUNT=0
STEP_TOTAL=10

step() {
    STEP_COUNT=$((STEP_COUNT + 1))
    echo ""
    echo "--- ${STEP_COUNT}/${STEP_TOTAL}  $1 ---"
}

step_failed() {
    echo "  FAILED: $1"
    FAILED_STEPS+=("$1")
}

check_cmd() {
    command -v "$1" >/dev/null 2>&1
}

persist_env() {
    # Write to CLAUDE_ENV_FILE if available (official cloud env persistence)
    if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
        echo "$1" >> "${CLAUDE_ENV_FILE}"
    fi
}

# =============================================================================
# PHASE 1: Local config (no network — always succeeds)
# =============================================================================

# ── 1. Cloud config files (settings merges; .mcp.json patched by SessionStart hook) ──
step "Cloud configuration files"

JSON_MERGE="${PROJECT_ROOT}/.claude/hooks/json_merge.py"
CLOUD_USER_SETTINGS="${HOME}/.claude/settings.json"
AUTO_MEMORY_DIR="${PROJECT_ROOT}/.claude/auto-memory"

# settings.local.json — cloud overrides, DEEP-MERGED via the shared helper so a
# pre-existing file's user keys are never clobbered (the old heredoc did clobber).
# Full enabledPlugins block emitted regardless of Claude Code's replace-vs-merge
# semantics: harmless if deep-merge, load-bearing if replace. csharp-lsp stays
# disabled (needs .NET 10); the four "Works" plugins enabled.
SETTINGS_LOCAL="${PROJECT_ROOT}/.claude/settings.local.json"
python3 "${JSON_MERGE}" "${SETTINGS_LOCAL}" << 'SETTINGS_EOF' && echo "  Merged: .claude/settings.local.json" || step_failed "settings.local.json merge"
{
  "permissions": {
    "allow": [
      "Bash(dotnet:*)",
      "Bash(xvfb-run *)",
      "Bash(xvfb-run:*)"
    ]
  },
  "enableAllProjectMcpServers": false,
  "enabledMcpjsonServers": [],
  "enabledPlugins": {
    "csharp-lsp@claude-plugins-official": false,
    "context7@claude-plugins-official": true,
    "claude-code-setup@claude-plugins-official": true,
    "code-simplifier@claude-plugins-official": true,
    "explanatory-output-style@claude-plugins-official": true
  }
}
SETTINGS_EOF

# Cloud user-level ~/.claude/settings.json — DEEP-MERGED (preserves anything the
# sandbox seeded). Heredoc is UNQUOTED so ${AUTO_MEMORY_DIR} expands. Carries:
#   - autoMemoryDirectory: the committed in-repo auto-memory dir, so the graph loads (Unit E)
#   - dreb marketplace + semantic-search@dreb (Unit C). semantic-search is a NL/intent
#     DISCOVERY substitute only — C# symbol precision is degraded on cloud (cloud_dev.md).
python3 "${JSON_MERGE}" "${CLOUD_USER_SETTINGS}" << USER_SETTINGS_EOF && echo "  Merged: ~/.claude/settings.json (autoMemoryDirectory + dreb)" || step_failed "user settings.json merge"
{
  "autoMemoryDirectory": "${AUTO_MEMORY_DIR}",
  "extraKnownMarketplaces": {
    "dreb": {
      "source": {
        "source": "github",
        "repo": "aebrer/dreb"
      }
    }
  },
  "enabledPlugins": {
    "semantic-search@dreb": true
  }
}
USER_SETTINGS_EOF

# =============================================================================
# PHASE 2: Network-dependent installs (each step isolated)
# =============================================================================

# ── 2. .NET SDK ──────────────────────────────────────────────────────────────
step ".NET ${DOTNET_VERSION_PROJECT} SDK"

export DOTNET_ROOT="${HOME}/.dotnet"
export PATH="${DOTNET_ROOT}:${PATH}"

if check_cmd dotnet && dotnet --list-sdks 2>/dev/null | grep -q "^${DOTNET_VERSION_PROJECT}"; then
    echo "  .NET ${DOTNET_VERSION_PROJECT}: already installed ($(dotnet --version))"
else
    echo "  Installing .NET ${DOTNET_VERSION_PROJECT} SDK..."
    if wget --timeout=60 -q https://dot.net/v1/dotnet-install.sh -O /tmp/dotnet-install.sh 2>/dev/null; then
        chmod +x /tmp/dotnet-install.sh
        if /tmp/dotnet-install.sh --channel "${DOTNET_VERSION_PROJECT}" --install-dir "${DOTNET_ROOT}" 2>&1; then
            echo "  .NET ${DOTNET_VERSION_PROJECT}: installed"
        else
            step_failed ".NET SDK install (dotnet-install.sh failed)"
        fi
    else
        step_failed ".NET SDK download (dot.net blocked by proxy — add builds.dotnet.microsoft.com and dotnetcli.azureedge.net to allowlist)"
    fi
fi

persist_env "DOTNET_ROOT=${DOTNET_ROOT}"
persist_env "PATH=${DOTNET_ROOT}:\$PATH"

if check_cmd dotnet; then
    echo "  Active SDK: $(dotnet --version 2>/dev/null || echo 'unknown')"
fi

# ── 3. Godot Mono (headless Linux) ───────────────────────────────────────────
step "Godot ${GODOT_VERSION} Mono (headless)"
if [ -f "${GODOT_BIN}" ]; then
    echo "  Already installed at: ${GODOT_BIN}"
else
    echo "  Downloading Godot ${GODOT_VERSION} Mono for Linux..."
    mkdir -p "${GODOT_INSTALL_DIR}"
    GODOT_ARCHIVE="Godot_v${GODOT_VERSION}-${GODOT_RELEASE}_mono_linux_x86_64.zip"
    GODOT_URL="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}-${GODOT_RELEASE}/${GODOT_ARCHIVE}"

    if wget --timeout=120 -q "${GODOT_URL}" -O "/tmp/${GODOT_ARCHIVE}" 2>/dev/null; then
        unzip -qo "/tmp/${GODOT_ARCHIVE}" -d "${GODOT_INSTALL_DIR}"
        rm -f "/tmp/${GODOT_ARCHIVE}"
        chmod +x "${GODOT_BIN}"
        echo "  Installed at: ${GODOT_BIN}"
    else
        step_failed "Godot download (github.com blocked?)"
    fi
fi

# ── 4. Node.js (for npx / dreb semantic-search plugin) ───────────────────────
step "Node.js (for npx / dreb semantic-search plugin)"
if check_cmd node; then
    echo "  Already installed: $(node --version)"
else
    echo "  Node.js not found — dreb semantic-search plugin build will not work"
    step_failed "Node.js (not pre-installed)"
fi

# ── 5. GitHub CLI ─────────────────────────────────────────────────────────────
step "GitHub CLI (gh)"
if check_cmd gh; then
    echo "  Already installed: $(gh --version | head -1)"
    if [ -n "${GH_TOKEN:-}" ] || gh auth status >/dev/null 2>&1; then
        echo "  Auth: OK"
    else
        echo "  Auth: not authenticated (set GH_TOKEN env var in Claude Code web settings)"
    fi
else
    echo "  Installing GitHub CLI from github.com releases..."
    # Use github.com/cli/cli/releases (allowed) instead of cli.github.com (blocked)
    GH_LATEST_URL=$(curl -sI https://github.com/cli/cli/releases/latest 2>/dev/null | grep -i '^location:' | tr -d '\r' | awk '{print $2}')
    if [ -n "${GH_LATEST_URL:-}" ]; then
        GH_VER=$(echo "${GH_LATEST_URL}" | grep -oP 'v\K[0-9.]+$' || echo "")
        if [ -n "${GH_VER}" ]; then
            GH_TAR="gh_${GH_VER}_linux_amd64.tar.gz"
            GH_DL="https://github.com/cli/cli/releases/download/v${GH_VER}/${GH_TAR}"
            if wget --timeout=60 -q "${GH_DL}" -O "/tmp/${GH_TAR}" 2>/dev/null; then
                tar -xzf "/tmp/${GH_TAR}" -C /tmp
                sudo cp "/tmp/gh_${GH_VER}_linux_amd64/bin/gh" /usr/local/bin/gh
                rm -rf "/tmp/${GH_TAR}" "/tmp/gh_${GH_VER}_linux_amd64"
                echo "  Installed: $(gh --version | head -1)"
            else
                step_failed "gh CLI download"
            fi
        else
            step_failed "gh CLI (could not parse version from redirect)"
        fi
    else
        step_failed "gh CLI (could not resolve latest release URL)"
    fi
fi

# ── 5b. DreB semantic-search plugin build ────────────────────────────────────
# The marketplace plugin self-bootstraps `npm install`, but its MCP server needs
# `npm run build` before it will start (else Status: failed and Unit C delivers
# nothing). The plugin cache is created when Claude Code first enables the plugin;
# on a fresh sandbox the dir may not exist yet at install time, in which case the
# first session enables it and the manual fallback is:
#   npm install --prefix <cache> && npm run build --prefix <cache>
step "DreB semantic-search plugin build"
DREB_CACHE=$(find "${HOME}/.claude/plugins/cache/dreb" -maxdepth 3 -name package.json -printf '%h\n' 2>/dev/null | head -1)
if [ -z "${DREB_CACHE:-}" ]; then
    echo "  SKIPPED: dreb plugin cache not present yet — build on first session (see summary)"
elif ! check_cmd npm; then
    echo "  WARNING: npm not available — cannot build dreb plugin"
    step_failed "dreb npm build (npm missing)"
else
    echo "  Building dreb plugin at: ${DREB_CACHE}"
    if ( cd "${DREB_CACHE}" && npm install && npm run build ) 2>&1; then
        echo "  DreB build: OK"
    else
        step_failed "dreb npm build"
    fi
fi

# =============================================================================
# PHASE 3: Project setup (depends on phase 2 results)
# =============================================================================

# ── 6. Git submodules ─────────────────────────────────────────────────────────
step "Git submodules (Jmodot)"
JMODOT_DIR="${PROJECT_ROOT}/Jmodot"
if [ -d "${JMODOT_DIR}" ] && [ "$(ls -A "${JMODOT_DIR}" 2>/dev/null)" ]; then
    echo "  Jmodot: OK (already populated)"
else
    echo "  Initializing submodules..."
    if git -C "${PROJECT_ROOT}" submodule update --init --recursive 2>&1; then
        echo "  Jmodot: initialized"
    else
        step_failed "git submodule update"
    fi
fi

# ── 7. Environment variables + xvfb-run check ───────────────────────────────
step "Environment variables"
export GODOT_BIN="${GODOT_BIN}"
echo "  GODOT_BIN=${GODOT_BIN}"
echo "  DOTNET_ROOT=${DOTNET_ROOT:-not set}"

persist_env "GODOT_BIN=${GODOT_BIN}"

# Verify xvfb-run is available (required for GdUnit4 tests)
# GdUnit4 uses .NET Named Pipes (Unix domain sockets). Godot needs a properly
# authenticated X display to start, or the pipe server never initializes.
# xvfb-run handles Xauthority + display setup correctly per invocation.
# A PreToolUse hook (cloud_test_enforcer.py) enforces this prefix automatically.
if check_cmd xvfb-run; then
    echo "  xvfb-run: available (required for GdUnit4 tests)"
else
    echo "  WARNING: xvfb-run not found — GdUnit4 tests will fail"
    step_failed "xvfb-run (not installed)"
fi

# Persist to .bashrc for any subprocess that sources it
PROFILE_FILE="${HOME}/.bashrc"
if ! grep -q "GODOT_BIN" "${PROFILE_FILE}" 2>/dev/null; then
    {
        echo ""
        echo "# {{PROJECT_NAME}} cloud environment"
        echo "export GODOT_BIN=\"${GODOT_BIN}\""
        echo "export DOTNET_ROOT=\"${HOME}/.dotnet\""
        echo "export PATH=\"\${DOTNET_ROOT}:\${PATH}\""
    } >> "${PROFILE_FILE}"
    echo "  Added to ${PROFILE_FILE}"
fi

# ── 8. Generate .runsettings ─────────────────────────────────────────────────
step "Generate .runsettings"
RUNSETTINGS="${PROJECT_ROOT}/.runsettings"
TEMPLATE="${PROJECT_ROOT}/.runsettings.template"
if [ -f "${RUNSETTINGS}" ]; then
    echo "  Already exists"
else
    if [ -f "${TEMPLATE}" ]; then
        sed "s|{{GODOT_BIN}}|${GODOT_BIN}|g" "${TEMPLATE}" > "${RUNSETTINGS}"
        echo "  Generated from template with GODOT_BIN=${GODOT_BIN}"
    else
        echo "  WARNING: .runsettings.template not found"
    fi
fi

# ── 9. Godot headless import + dotnet build ──────────────────────────────────
step "Godot import + dotnet build"

# Import cache
IMPORTED_DIR="${PROJECT_ROOT}/.godot/imported"
if [ -d "${IMPORTED_DIR}" ] && [ "$(ls -A "${IMPORTED_DIR}" 2>/dev/null)" ]; then
    echo "  Import cache: already populated"
elif [ -f "${GODOT_BIN}" ]; then
    echo "  Running headless import (may take 30-60s)..."
    "${GODOT_BIN}" --headless --path "${PROJECT_ROOT}" --import --quit 2>&1 || true
    if [ -d "${IMPORTED_DIR}" ] && [ "$(ls -A "${IMPORTED_DIR}" 2>/dev/null)" ]; then
        echo "  Import cache: OK"
    else
        echo "  WARNING: Import cache may be incomplete"
    fi
else
    echo "  SKIPPED: Godot binary not installed"
fi

# Build
if check_cmd dotnet; then
    echo "  Building..."
    if dotnet build "${PROJECT_ROOT}" --nologo -v q -consoleLoggerParameters:ErrorsOnly 2>&1; then
        echo "  Build: OK"
    else
        step_failed "dotnet build"
    fi
else
    echo "  SKIPPED: dotnet not installed"
    step_failed "dotnet build (SDK missing)"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "================================================================="
if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
    echo "  Cloud Install Complete! All steps succeeded."
else
    echo "  Cloud Install Complete (with ${#FAILED_STEPS[@]} failures):"
    for fail in "${FAILED_STEPS[@]}"; do
        echo "    - ${fail}"
    done
fi
echo ""
echo "  WORKING:"
echo "    - dotnet build / dotnet test"
echo "    - GdUnit4 tests (headless)"
echo "    - Auto-memory (file-based, in-repo)"
echo "    - Context7 (docs)"
echo "    - All hooks, skills, commands"
echo "    - Git operations"
if check_cmd gh; then
    echo "    - GitHub CLI (PRs, issues)"
fi
echo ""
echo "  NOT AVAILABLE:"
echo "    - Godot MCP (run_project, create_scene, etc.)"
echo "    - Obsidian MCP (vault access) — /worklog mutations queue to .claude/worklog-pending.md"
echo "    - Visual playtesting"
echo "    - LSP-precision C# symbol resolution (semantic-search is DISCOVERY-only on cloud)"
echo ""
echo "  FIRST SESSION TODO:"
echo "    - Run /reindex_search before semantic-search returns results (.search-index/ is gitignored)"
echo "    - If the dreb MCP server shows 'Status: failed', build it manually:"
echo "        npm install --prefix <plugin-cache> && npm run build --prefix <plugin-cache>"
echo "================================================================="
