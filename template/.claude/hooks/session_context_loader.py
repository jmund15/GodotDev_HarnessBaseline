#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: SessionStart - Worktree setup + development context loader

On every session start:
1. Detects if running in a git worktree or cloud environment
2. On cloud: runs cloud-install.sh if dependencies are missing
3. Initializes Jmodot submodule if empty (worktree-critical)
4. Generates .runsettings from template if missing (worktree/cloud)
5. Regenerates .godot import cache if missing (headless)
6. Runs dotnet build to verify compilation health
7. Injects git context (branch, commits, submodule status)
8. On cloud: persists env vars via CLAUDE_ENV_FILE
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

def is_cloud() -> bool:
    """Return True if running in a Claude Code cloud environment."""
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true"


def persist_cloud_env(godot_bin: str):
    """Write env vars to CLAUDE_ENV_FILE so subsequent Bash tool calls inherit them."""
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return
    lines = []
    if godot_bin:
        lines.append(f"GODOT_BIN={godot_bin}")
    dotnet_root = os.environ.get("DOTNET_ROOT", "")
    if dotnet_root:
        lines.append(f"DOTNET_ROOT={dotnet_root}")
        lines.append(f"PATH={dotnet_root}:$PATH")
    if lines:
        with open(env_file, "a") as f:
            f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Update on every Godot engine upgrade — both platform fallback paths use this.
GODOT_VERSION = "4.6.2-stable"


def get_godot_bin() -> str:
    """Return the Godot binary path from GODOT_BIN or a known install."""
    env = os.environ.get("GODOT_BIN", "")
    if env and Path(env).exists():
        return env
    # Platform-specific fallback locations
    if platform.system() == "Linux":
        fallback = Path.home() / f".local/godot/Godot_v{GODOT_VERSION}_mono_linux_x86_64/Godot_v{GODOT_VERSION}_mono_linux.x86_64"
    else:
        fallback = Path.home() / f"Game_Dev/Godot_Installs/Godot_v{GODOT_VERSION}_mono_win64/Godot_v{GODOT_VERSION}_mono_win64.exe"
    if fallback.exists():
        return str(fallback)
    return ""


def get_project_root() -> Path:
    """Get the git toplevel (works in worktrees too)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def is_worktree() -> bool:
    """Return True if the current directory is inside a git worktree (not the main repo)."""
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5
        )
        if toplevel.returncode == 0 and common.returncode == 0:
            tl = Path(toplevel.stdout.strip()).resolve()
            cd = Path(common.stdout.strip()).resolve()
            # In a worktree, the common dir points OUTSIDE the toplevel
            return not str(cd).startswith(str(tl))
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------

def setup_submodule(root: Path) -> str:
    """Initialize submodule if the Jmodot directory is empty."""
    jmodot_path = root / "Jmodot"
    # Check if directory exists but is empty (or only has . and ..)
    if jmodot_path.exists() and any(jmodot_path.iterdir()):
        return "OK"

    try:
        result = subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            capture_output=True, text=True, timeout=120, cwd=str(root)
        )
        if result.returncode == 0:
            return "FIXED (initialized)"
        return f"FAILED: {result.stderr.strip()[:80]}"
    except subprocess.TimeoutExpired:
        return "FAILED: timeout"
    except Exception as e:
        return f"FAILED: {e}"


def setup_runsettings(root: Path) -> str:
    """Generate .runsettings from template if missing (worktree-only).

    The .runsettings file is gitignored (machine-specific GODOT_BIN path).
    Worktrees get a fresh checkout without it. This generates it from the
    tracked .runsettings.template, substituting the resolved GODOT_BIN path.
    """
    runsettings_path = root / ".runsettings"
    if runsettings_path.exists():
        return "OK"

    template_path = root / ".runsettings.template"
    if not template_path.exists():
        return "SKIPPED (.runsettings.template not found)"

    godot_bin = get_godot_bin()
    if not godot_bin:
        return "SKIPPED (GODOT_BIN not resolved)"

    try:
        template_content = template_path.read_text(encoding="utf-8")
        resolved = template_content.replace("{{GODOT_BIN}}", godot_bin)
        runsettings_path.write_text(resolved, encoding="utf-8")
        return "FIXED (generated from template)"
    except Exception as e:
        return f"FAILED: {e}"


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


def setup_import_cache(root: Path) -> str:
    """Regenerate .godot import cache if missing."""
    godot_dir = root / ".godot"
    imported_dir = godot_dir / "imported"
    # If imported/ already exists, cache is populated
    if imported_dir.exists() and any(imported_dir.iterdir()):
        return "OK"

    godot_bin = get_godot_bin()
    if not godot_bin:
        return "SKIPPED (godot binary not found)"

    try:
        result = subprocess.run(
            [godot_bin, "--headless", "--path", str(root), "--import", "--quit"],
            capture_output=True, text=True, timeout=120, cwd=str(root)
        )
        # Godot import may return non-zero but still succeed
        if (imported_dir.exists() and any(imported_dir.iterdir())):
            return "FIXED (regenerated)"
        if result.returncode == 0:
            return "FIXED (regenerated)"
        return f"FAILED: exit {result.returncode}"
    except subprocess.TimeoutExpired:
        return "FAILED: timeout (>120s)"
    except Exception as e:
        return f"FAILED: {e}"


# Build-verify freshness gate: a full `dotnet build` on EVERY session start
# costs up to 120s and can collide with a concurrent session's build/test
# (shared obj/ locks, gdunit4 pipe). Skip when a successful verify for the
# same HEAD is recent; failures are never cached (always re-verify).
BUILD_VERIFY_TTL_SECONDS = 30 * 60


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(root)
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _build_cache_path(root: Path) -> Path:
    return root / ".claude" / ".cache" / "build_verify.json"


def cached_build_result(root: Path) -> str | None:
    """Return the cached OK result if fresh and HEAD-matched, else None."""
    try:
        import time
        cache = json.loads(_build_cache_path(root).read_text(encoding="utf-8"))
        if not cache.get("result", "").startswith("OK"):
            return None
        if time.time() - float(cache.get("ts", 0)) > BUILD_VERIFY_TTL_SECONDS:
            return None
        head = _git_head(root)
        if not head or cache.get("head") != head:
            return None
        return cache["result"]
    except Exception:
        return None


def store_build_result(root: Path, result: str) -> None:
    try:
        import time
        path = _build_cache_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"ts": time.time(), "head": _git_head(root), "result": result}),
            encoding="utf-8",
        )
    except Exception:
        pass


def verify_build(root: Path) -> str:
    """Run dotnet build and report success/failure."""
    try:
        result = subprocess.run(
            ["dotnet", "build", "--nologo", "-v", "q"],
            capture_output=True, text=True, timeout=120, cwd=str(root)
        )
        # Parse error/warning counts from last lines
        output = result.stdout + result.stderr
        errors = 0
        warnings = 0
        for line in output.splitlines():
            line_stripped = line.strip()
            if "Error(s)" in line_stripped:
                try:
                    errors = int(line_stripped.split()[0])
                except (ValueError, IndexError):
                    pass
            if "Warning(s)" in line_stripped:
                try:
                    warnings = int(line_stripped.split()[0])
                except (ValueError, IndexError):
                    pass

        if result.returncode == 0 and errors == 0:
            return f"OK ({warnings} warnings)" if warnings else "OK"
        return f"FAILED ({errors} errors, {warnings} warnings)"
    except subprocess.TimeoutExpired:
        return "FAILED: timeout (>120s)"
    except Exception as e:
        return f"FAILED: {e}"


# ---------------------------------------------------------------------------
# Git context (existing functionality)
# ---------------------------------------------------------------------------

def get_git_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def get_uncommitted_count() -> int:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l for l in result.stdout.strip().split("\n") if l]
        return len(lines)
    except Exception:
        return 0


def get_recent_commits(count: int = 3, cwd: str | None = None) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "log", f"-{count}", "--format=%h %s"],
            capture_output=True, text=True, timeout=5, cwd=cwd
        )
        return [line.strip()[:70] for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception:
        return []


def get_jmodot_commits(root: Path, count: int = 3) -> list[str]:
    """Get recent commits from Jmodot submodule using the resolved project root."""
    jmodot_path = root / "Jmodot"
    if not jmodot_path.exists() or not any(jmodot_path.iterdir()):
        return []
    return get_recent_commits(count, str(jmodot_path))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_cloud_install(root: Path) -> str:
    """Run cloud-install.sh if on cloud and dependencies are missing."""
    if not is_cloud():
        return ""

    # Check if deps are already installed
    try:
        dotnet_ok = _cmd_exists("dotnet") and subprocess.run(
            ["dotnet", "--version"], capture_output=True, timeout=5
        ).returncode == 0
    except Exception:
        dotnet_ok = False

    godot_bin = get_godot_bin()

    if dotnet_ok and godot_bin:
        return "OK (deps already installed)"

    install_script = root / ".claude" / "cloud-install.sh"
    if not install_script.exists():
        return "SKIPPED (cloud-install.sh not found)"

    try:
        result = subprocess.run(
            ["bash", str(install_script)],
            capture_output=True, text=True, timeout=540, cwd=str(root)
        )
        if result.returncode == 0:
            # Re-source env vars that the install script set
            _load_cloud_env_from_output(result.stdout)
            return "FIXED (installed dependencies)"
        return f"FAILED (exit {result.returncode}): {result.stderr.strip()[-200:]}"
    except subprocess.TimeoutExpired:
        return "FAILED: timeout (>540s)"
    except Exception as e:
        return f"FAILED: {e}"


def _cmd_exists(cmd: str) -> bool:
    """Check if a command exists on PATH."""
    try:
        result = subprocess.run(
            ["which", cmd] if platform.system() != "Windows" else ["where", cmd],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _load_cloud_env_from_output(output: str):
    """Parse cloud-install.sh output for env var exports and apply them."""
    for line in output.splitlines():
        line = line.strip()
        # Look for lines like GODOT_BIN=/path or DOTNET_ROOT=/path
        if line.startswith("GODOT_BIN=") or line.startswith("DOTNET_ROOT="):
            key, _, value = line.partition("=")
            os.environ[key] = value
            # Propagate DOTNET_ROOT to PATH so dotnet is discoverable
            if key == "DOTNET_ROOT" and value and value not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{value}:{os.environ.get('PATH', '')}"
        # Also look for export statements
        if line.startswith("export ") and "=" in line:
            assignment = line[len("export "):]
            key, _, value = assignment.partition("=")
            value = value.strip('"').strip("'")
            os.environ[key] = value


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        input_data = {}

    root = get_project_root()
    worktree = is_worktree()
    cloud = is_cloud()

    # --- Cloud auto-install (before any other setup) ---
    setup_results = {}
    if cloud:
        setup_results["cloud_install"] = run_cloud_install(root)

    # --- Worktree/cloud setup (runs for ALL sessions, idempotent) ---
    setup_results["submodule"] = setup_submodule(root)
    if worktree or cloud:
        setup_results["runsettings"] = setup_runsettings(root)
    setup_results["import_cache"] = setup_import_cache(root)

    # Only build if submodule is ready; skip when a fresh same-HEAD OK verify
    # is cached (see BUILD_VERIFY_TTL_SECONDS rationale above).
    if "FAILED" not in setup_results["submodule"]:
        cached = cached_build_result(root)
        if cached:
            setup_results["build"] = f"{cached} [cached <{BUILD_VERIFY_TTL_SECONDS // 60}m]"
        else:
            setup_results["build"] = verify_build(root)
            store_build_result(root, setup_results["build"])
    else:
        setup_results["build"] = "SKIPPED (submodule not ready)"

    # --- LSP plugin health check (local only) ---
    if not cloud:
        setup_results["lsp_plugin"] = verify_lsp_plugin()

    # --- Git context ---
    branch = get_git_branch()
    uncommitted = get_uncommitted_count()
    uncommitted_str = f"{uncommitted} uncommitted" if uncommitted > 0 else "clean"

    main_commits = get_recent_commits(3)
    jmodot_commits = get_jmodot_commits(root, 3)

    # --- Persist env for cloud ---
    godot_bin = get_godot_bin()
    if cloud:
        persist_cloud_env(godot_bin)

    # --- Build output ---
    output_lines = ["<session-context>"]

    # Worktree/cloud report
    if worktree:
        output_lines.append(f"Worktree: YES (root: {root})")
    else:
        output_lines.append("Worktree: no (main repo)")

    if cloud:
        output_lines.append("Environment: CLOUD (CLAUDE_CODE_REMOTE=true)")

    output_lines.append("")
    output_lines.append("Environment Setup:")
    any_fixed = False
    for step, status in setup_results.items():
        if status.startswith("OK"):
            marker = "OK"
        elif "FIXED" in status:
            marker = "FIXED"
        elif "BROKEN" in status:
            marker = "FAIL"
        else:
            marker = "WARN"
        if "FIXED" in status:
            any_fixed = True
        output_lines.append(f"  [{marker}] {step}: {status}")

    # GODOT_BIN for test commands (Bash sessions don't inherit setx env vars)
    if godot_bin:
        output_lines.append(f"  GODOT_BIN: {godot_bin}")
    else:
        output_lines.append("  GODOT_BIN: NOT FOUND (tests requiring Godot runtime will fail)")

    if any_fixed:
        output_lines.append("  ** Auto-fixed issues above. Ready to develop. **")

    # Git context
    output_lines.append("")
    output_lines.append(f"Git: {branch} | {uncommitted_str}")
    output_lines.append("")
    output_lines.append("Recent {{PROJECT_NAME}} commits:")
    if main_commits:
        for commit in main_commits:
            output_lines.append(f"  {commit}")
    else:
        output_lines.append("  (none)")

    output_lines.append("")
    output_lines.append("Recent Jmodot commits:")
    if jmodot_commits:
        for commit in jmodot_commits:
            output_lines.append(f"  {commit}")
    else:
        output_lines.append("  (none)")

    output_lines.append("</session-context>")

    # Worklog titles cache (lightweight always-loaded TODO awareness).
    # Source of truth is Obsidian; this is the local mirror maintained by the
    # `worklog` skill. Empty / missing file is fine — silent no-op.
    worklog_titles_path = root / ".claude" / "worklog-titles.md"
    if worklog_titles_path.exists():
        try:
            worklog_content = worklog_titles_path.read_text(encoding="utf-8").strip()
            if worklog_content:
                output_lines.append("")
                output_lines.append("<worklog-titles>")
                output_lines.append(worklog_content)
                output_lines.append("</worklog-titles>")
        except Exception:
            pass

    # LSP early-load nudge: whenever LSP is available locally.
    # LSP tool schema is deferred by the harness; this nudge tells the model to load
    # it up front via ToolSearch so C# symbol queries don't default to Grep.
    # Gated only on plugin health — unconditional otherwise, because this is a C#
    # project and even read-only sessions benefit from semantic navigation.
    # Mirrors .claude/rules/csharp_lsp.md behavioral gotchas — sync on changes there.
    lsp_ok = not cloud and setup_results.get("lsp_plugin", "").startswith("OK")
    if lsp_ok:
        output_lines.append("")
        output_lines.append("<lsp-early-load>")
        output_lines.append(
            "csharp-lsp is healthy. Load the LSP tool schema as one of your first actions: "
            "ToolSearch(query=\"select:LSP\", max_results=1). Then default to LSP for C# "
            "symbol/caller/type questions (findReferences, hover, documentSymbol, "
            "incomingCalls). Keep Grep for .tscn/.tres, StringName keys, and as the "
            "anchor-finding step before LSP (see workflow below).\n\n"
            "Schema quirks (avoid the C2-failure shape):\n"
            "  1. filePath is REQUIRED on every operation, even workspace-wide ones. It is "
            "a server-routing hint (extension picks the language server) — pass any real "
            ".cs file, NOT \".\" or \"\". Passing \".\" returns \"Path is not a file\".\n"
            "  2. workspaceSymbol does NOT take a query parameter — there is none in the "
            "schema. It returns 100 unfiltered alphabetical-by-path symbols. Use it for "
            "browsing, NOT for finding a symbol by name.\n\n"
            "Anchor-then-navigate workflow (find symbol FooBar → enumerate callers):\n"
            "  Step 1: semantic-search('FooBar') OR Grep('class FooBar\\b' -g '*.cs') → file path\n"
            "  Step 2: LSP(operation='documentSymbol', filePath=<file>, line=1, character=1) → line numbers\n"
            "  Step 3: LSP(operation='findReferences', filePath=<file>, line=<decl>, character=<col>) → callers"
        )
        output_lines.append("</lsp-early-load>")

    # Semantic-search early-load nudge (cloud only). LSP is unavailable on cloud,
    # so semantic-search is the primary code-discovery tool — but a DISCOVERY one
    # (NL/intent/concept), NOT an LSP-precision substitute: C# symbol resolution
    # degrades to Grep-anchored navigation (see .claude/rules/cloud_dev.md).
    if cloud:
        output_lines.append("")
        output_lines.append("<semantic-search-early-load>")
        output_lines.append(
            "Cloud session: csharp-lsp is disabled. Load semantic-search as one of your "
            "first actions: ToolSearch(query=\"select:mcp__plugin_semantic-search_semantic-search__search\", "
            "max_results=1). Use it for \"where is X\" / prior-art / concept queries when you don't "
            "know symbol names yet. It is DISCOVERY-only on cloud (no C# symbol-tree precision) — for "
            "exact callers/definitions, anchor with Grep('class FooBar\\b' -g '*.cs') then navigate. "
            "If results come back empty, run /reindex_search first (.search-index/ is gitignored)."
        )
        output_lines.append("</semantic-search-early-load>")

    # Cloud-worklog replay surface (local only). When a cloud session has queued
    # worklog mutations to .claude/worklog-pending.md (Unit D), flag the live count
    # so the user knows to run /worklog and replay them into Obsidian. Struck-through
    # (already-skipped) entries are excluded from the count.
    if not cloud:
        pending_path = root / ".claude" / "worklog-pending.md"
        if pending_path.exists():
            try:
                raw = pending_path.read_text(encoding="utf-8")
                # Count only the queue body, AFTER the DO-NOT-HAND-EDIT comment
                # close — the header documents the entry format with example
                # "- ADD ..." lines that must not be miscounted as pending.
                body = raw.split("-->", 1)[1] if "-->" in raw else raw
                pending_lines = [
                    l for l in body.splitlines()
                    if l.strip().startswith("- ") and not l.strip().startswith("- ~~")
                ]
                if pending_lines:
                    output_lines.append("")
                    output_lines.append(
                        f"Cloud worklog: {len(pending_lines)} pending — run /worklog to replay into Obsidian."
                    )
            except Exception:
                pass

    # Continuation reminder
    output_lines.append("")
    output_lines.append("<context-reload-reminder>")
    output_lines.append("If resuming from compaction: search auto-memory (semantic-search) for task-relevant gotchas.")
    output_lines.append("</context-reload-reminder>")

    print("\n".join(output_lines))
    sys.exit(0)


if __name__ == "__main__":
    main()
