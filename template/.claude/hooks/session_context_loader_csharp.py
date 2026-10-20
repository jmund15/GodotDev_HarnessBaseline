#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C# language-server steps of the SessionStart loader (`session_context_loader.py` loads this module
through `_optional_hooks`; a consumer without the coding layer has no file and the loader skips it).

Both hooks do nothing unless the checkout root holds a `.sln`, `.slnx` or `.csproj`. Advisory class:
a failing hook is reported by the loader as a `step_modules` status and never blocks session start.

Hooks the loader calls:
  local_checks(root)                 `lsp_plugin` status; the loader calls it on a local machine only
  nudge_lines(setup_results, cloud)  the LSP early-load nudge, when `lsp_plugin` is OK
"""

import json
import os
from pathlib import Path


def csharp_project(root: Path) -> bool:
    return any(next(root.glob(pattern), None) for pattern in ("*.sln", "*.slnx", "*.csproj"))


def verify_lsp_plugin() -> str:
    """Verify the C# LSP plugin is correctly configured and not corrupted.

    Local-only check — caller must gate on not is_cloud().
    Checks: plugin.json content, adapter file, binary, registration,
    orphaned marker, and ENABLE_LSP_TOOL env var.
    """
    home = Path.home()
    # Version-agnostic: resolve the newest installed plugin version rather than
    # hardcoding one (a hardcoded "1.0.0" silently reports BROKEN after a bump).
    plugin_cache_root = home / ".claude/plugins/cache/claude-plugins-official/csharp-lsp"
    version_candidates = sorted(plugin_cache_root.glob("*/.claude-plugin/plugin.json"))
    plugin_json_path = (
        version_candidates[-1] if version_candidates
        else plugin_cache_root / "0.0.0/.claude-plugin/plugin.json"
    )
    adapter_path = home / ".dotnet/tools/csharp-ls-adapter.js"
    binary_path = home / ".dotnet/tools/csharp-ls-original.exe"
    installed_path = home / ".claude/plugins/installed_plugins.json"
    orphaned_path = plugin_json_path.parent.parent / ".orphaned_at"

    issues = []

    if not binary_path.exists():
        issues.append("csharp-ls-original.exe missing from ~/.dotnet/tools/")

    if not adapter_path.exists():
        issues.append("csharp-ls-adapter.js missing from ~/.dotnet/tools/")

    if not plugin_json_path.exists():
        issues.append("plugin.json missing from plugin cache")
    else:
        try:
            with open(plugin_json_path, "r", encoding="utf-8") as f:
                pj = json.load(f)
            lsp_cmd = pj.get("lspServers", {}).get("csharp-ls", {}).get("command", "")
            if lsp_cmd != "node":
                issues.append(f"plugin.json command='{lsp_cmd}' (expected 'node') — marketplace overwrote it?")
        except Exception as e:
            issues.append(f"plugin.json unreadable: {e}")

    if orphaned_path.exists():
        issues.append(".orphaned_at marker present (plugin uninstalled?)")

    if installed_path.exists():
        try:
            with open(installed_path, "r", encoding="utf-8") as f:
                ip = json.load(f)
            if "csharp-lsp@claude-plugins-official" not in ip.get("plugins", {}):
                issues.append("not registered in installed_plugins.json")
        except Exception:
            issues.append("installed_plugins.json unreadable")

    if os.environ.get("ENABLE_LSP_TOOL") != "1":
        issues.append("ENABLE_LSP_TOOL env var not set to '1'")

    if not issues:
        return "OK"
    return "BROKEN: " + "; ".join(issues)


def local_checks(root: Path) -> dict[str, str]:
    return {"lsp_plugin": verify_lsp_plugin()} if csharp_project(root) else {}


def nudge_lines(setup_results: dict[str, str], cloud: bool) -> list[str]:
    """LSP early-load nudge. The LSP tool schema is deferred by the harness; this tells the model to
    load it up front via ToolSearch so C# symbol queries don't default to Grep. The quirks and
    workflow live in .claude/rules/csharp_lsp.md (auto-loads on the first .cs read); this only
    preserves the EARLY ToolSearch timing."""
    if cloud or not setup_results.get("lsp_plugin", "").startswith("OK"):
        return []
    return [
        "",
        "<lsp-early-load>",
        "csharp-lsp healthy. Load the LSP tool schema as one of your first actions: "
        "ToolSearch(query=\"select:LSP\", max_results=1); then default to LSP for C# "
        "symbol/caller/type questions. Anchor-then-navigate (semantic-search or "
        "Grep(\"class X\") -> LSP documentSymbol -> findReferences); schema quirks "
        "(filePath required on every op; workspaceSymbol has no query param) live in "
        "rules/csharp_lsp.md, which auto-loads on the first .cs read.",
        "</lsp-early-load>",
    ]
