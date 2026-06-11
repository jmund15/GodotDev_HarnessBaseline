#!/usr/bin/env bash
# setup-csharp-ls.sh — Automate csharp-ls adapter setup for new workstations
#
# Prerequisites:
#   - .NET SDK installed (9.0+)
#   - Node.js installed
#   - Claude Code installed
#   - ENABLE_LSP_TOOL=1 set in environment
#
# What this script does:
#   1. Restores dotnet tools (installs csharp-ls per .config/dotnet-tools.json — currently v0.24.0)
#   2. Renames the real csharp-ls binary → csharp-ls-original.exe (re-run-safe: overwrites old binary)
#   3. Deploys the adapter script to ~/.dotnet/tools/
#   4. Generates plugin.json with correct absolute paths
#
# Usage:
#   bash .claude/tools/setup-csharp-ls.sh [--dry-run]
#
# Run from the {{PROJECT_NAME}} project root.

set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "[DRY RUN] No files will be modified."
fi

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DOTNET_TOOLS_DIR="$HOME/.dotnet/tools"
ADAPTER_SOURCE="$SCRIPT_DIR/csharp-ls-adapter.js"
ADAPTER_DEST="$DOTNET_TOOLS_DIR/csharp-ls-adapter.js"
SOLUTION_PATH="$PROJECT_ROOT/{{PROJECT_NAME}}.sln"

# Plugin.json location (Claude Code plugin cache)
PLUGIN_DIR="$HOME/.claude/plugins/cache/claude-plugins-official/csharp-lsp/1.0.0/.claude-plugin"
PLUGIN_JSON="$PLUGIN_DIR/plugin.json"

echo "=== csharp-ls Adapter Setup ==="
echo "Project root:  $PROJECT_ROOT"
echo "Solution:      $SOLUTION_PATH"
echo "Adapter src:   $ADAPTER_SOURCE"
echo "Adapter dest:  $ADAPTER_DEST"
echo "Plugin.json:   $PLUGIN_JSON"
echo ""

# Step 1: Restore dotnet tools
echo "[1/4] Restoring dotnet tools..."
if [[ "$DRY_RUN" == false ]]; then
  dotnet tool restore --tool-manifest "$PROJECT_ROOT/.config/dotnet-tools.json"
else
  echo "  Would run: dotnet tool restore"
fi

# Step 2: Rename the real csharp-ls binary
# Re-run-safe: both fresh-install and post-`dotnet tool update` cases produce
# csharp-ls.exe. In the update case, csharp-ls-original.exe also exists (the old
# version) — `mv -f` overwrites it with the fresh binary, which is the desired
# behavior. Prior version had a "skip if exists" guard that silently kept the old
# binary in place after `dotnet tool update`, leaving the wrapper invoking the
# stale binary; replaced 2026-05-03 during csharp-ls 0.22→0.24 upgrade.
ORIGINAL_EXE="$DOTNET_TOOLS_DIR/csharp-ls.exe"
RENAMED_EXE="$DOTNET_TOOLS_DIR/csharp-ls-original.exe"

if [[ -f "$ORIGINAL_EXE" ]]; then
  echo "[2/4] Renaming csharp-ls.exe → csharp-ls-original.exe (overwrites old version if present)..."
  if [[ "$DRY_RUN" == false ]]; then
    mv -f "$ORIGINAL_EXE" "$RENAMED_EXE"
  else
    echo "  Would rename: $ORIGINAL_EXE → $RENAMED_EXE (with -f overwrite)"
  fi
elif [[ -f "$RENAMED_EXE" ]]; then
  echo "[2/4] csharp-ls-original.exe already in place; no fresh csharp-ls.exe to rename."
else
  echo "[2/4] ERROR: neither csharp-ls.exe nor csharp-ls-original.exe found."
  echo "  dotnet tool restore may have installed it elsewhere."
  echo "  Check: dotnet tool list --global"
  exit 1
fi

# Step 3: Deploy adapter script
echo "[3/4] Deploying adapter script..."
if [[ ! -f "$ADAPTER_SOURCE" ]]; then
  echo "  ERROR: Adapter source not found at $ADAPTER_SOURCE"
  exit 1
fi
if [[ "$DRY_RUN" == false ]]; then
  cp "$ADAPTER_SOURCE" "$ADAPTER_DEST"
  echo "  Copied to $ADAPTER_DEST"
else
  echo "  Would copy: $ADAPTER_SOURCE → $ADAPTER_DEST"
fi

# Step 4: Generate plugin.json
echo "[4/4] Generating plugin.json..."

# Convert Git Bash paths (/c/Users/...) to Windows paths (C:/Users/...) for JSON
# cygpath -m gives Windows paths with forward slashes — exactly what we need
to_win_path() {
  if command -v cygpath &>/dev/null; then
    cygpath -m "$1"
  else
    echo "$1"  # Not Git Bash — assume paths are already correct
  fi
}
ADAPTER_DEST_JSON="$(to_win_path "$ADAPTER_DEST")"
SOLUTION_JSON="$(to_win_path "$SOLUTION_PATH")"
PROJECT_JSON="$(to_win_path "$PROJECT_ROOT")"

PLUGIN_CONTENT=$(cat <<ENDJSON
{
  "name": "csharp-lsp",
  "description": "C# language server for code intelligence",
  "version": "1.0.0",
  "author": {
    "name": "Anthropic",
    "email": "support@anthropic.com"
  },
  "lspServers": {
    "csharp-ls": {
      "command": "node",
      "args": ["$ADAPTER_DEST_JSON", "--solution", "$SOLUTION_JSON"],
      "extensionToLanguage": {
        ".cs": "csharp"
      },
      "workspaceFolder": "$PROJECT_JSON"
    }
  }
}
ENDJSON
)

if [[ "$DRY_RUN" == false ]]; then
  mkdir -p "$PLUGIN_DIR"
  echo "$PLUGIN_CONTENT" > "$PLUGIN_JSON"
  echo "  Written to $PLUGIN_JSON"
else
  echo "  Would write to $PLUGIN_JSON:"
  echo "$PLUGIN_CONTENT"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Remaining manual steps:"
echo "  1. Set ENABLE_LSP_TOOL=1 in your environment"
echo "  2. Restart Claude Code to pick up the new adapter"
echo "  3. (Optional) Set LSP_ADAPTER_DEBUG=1 for debug logging"
