---
paths:
  - "**/.runsettings"
  - "**/cloud-install.sh"
  - "**/cloud_test_enforcer.py"
---

# Cloud Development (Claude Code Web)

Active when `CLAUDE_CODE_REMOTE=true` (detected by SessionStart hook). This rule auto-loads when Claude touches cloud-specific artifacts; on cloud sessions, the entire ruleset applies.

**Auto-setup:** First launch runs `.claude/cloud-install.sh` (~5 min) — installs .NET 9.0 SDK, Godot 4.6.2 headless, generates `.runsettings`.

**Works:** dotnet build/test, GdUnit4 (all suites via `xvfb-run`), Git/GitHub, auto-memory (file-based in-repo store; semantic-search recall), Context7, semantic-search (DreB, discovery-only — see below), all hooks/skills/commands, WebSearch/WebFetch.

**Does NOT work (inherently local):**
- Godot MCP (run_project, get_debug_output, create_scene, get_uid) — skip these tools on cloud
- Obsidian MCP (vault access) — defer design doc work to local sessions; `/worklog` mutations queue to `.claude/worklog-pending.md` for local replay
- Visual playtesting — focus on automated tests
- csharp-ls plugin (disabled via `settings.local.json` — needs .NET 10)
- **LSP-precision C# symbol resolution** — LSP is unavailable on cloud. Semantic-search (DreB) is a *discovery* substitute (NL/intent/concept queries), **NOT** symbol-precise: the cloud marketplace build heuristic-splits C# (no symbol-tree extraction — that lives only in the local fork). For exact callers/definitions, anchor with `Grep('class FooBar\b' -g '*.cs')` then navigate.

**Adapted workflows:**
- Tests: **Must prefix `dotnet test` with `xvfb-run --auto-servernum`** — PreToolUse hook (`cloud_test_enforcer.py`) enforces automatically. GdUnit4 uses .NET Named Pipes; Godot needs an authenticated X display or the pipe server never starts (10+ min timeout).
- Code discovery: lead with semantic-search for "where is X" / prior-art (load it early per the SessionStart `<semantic-search-early-load>` nudge); fall to Grep anchors for symbol precision. `.search-index/` is gitignored → run `/reindex_search` first on a fresh sandbox.
- UIDs: read from `.cs.uid` companion files (not get_uid MCP)
- Orphan kill: `pkill -f "Godot_v"` (not PowerShell)
- Godot logs: `~/.local/share/godot/app_userdata/{{PROJECT_NAME}}/logs/godot.log`

**Proxy allowlist (required domains):** `builds.dotnet.microsoft.com`, `dotnetcli.azureedge.net`, `github.com`, `registry.npmjs.org`.

**gh authentication on cloud:** Set `GH_TOKEN` env var in Claude Code web settings (Settings → Custom Environment). `gh` CLI auto-detects `GH_TOKEN` — no `gh auth login` needed. Use `-R owner/repo` flag with `gh` commands (cloud sandbox proxy requires it).
