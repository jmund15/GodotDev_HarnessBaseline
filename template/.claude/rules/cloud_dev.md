---
paths:
  - "**/cloud-setup.sh"
  - "**/cloud_test_enforcer.py"
---

# Cloud Session Contract (Claude Code on the web)

`CLAUDE_CODE_REMOTE=true` marks a cloud VM: Ubuntu 24.04, 4 vCPU, 16 GB.

## Setup
- The environment's **Setup script** field holds `.claude/cloud-setup.sh`, which installs .NET 9, headless Godot mono, xvfb and Godot's X11 libs into the ~7-day environment snapshot, writing its `SETUP_FINGERPRINT` to `${PREFIX}/.cloud-setup.fingerprint`. Without it, SessionStart runs the same script on every new session and reports `cloud_setup FIXED (... took Ns ...)`. A marker that does not match the repo copy (an older paste or an edit since) reads as a missing toolchain: SessionStart runs the repo copy and reports `FIXED (snapshot script is stale; … paste .claude/cloud-setup.sh into the environment Setup script)`.
- SessionStart then initializes an empty Jmodot checkout or advances a clean one that sits behind its pin, writes `.runsettings`, rebuilds the import cache, builds, fills `.claude/cache/godot-docs/` and exports `GODOT_BIN`/`DOTNET_ROOT`. A dirty, ahead or diverged Jmodot checkout stays as it is and reads `BROKEN`, which skips the import cache and build.
- Network: Trusted does not reach `builds.dotnet.microsoft.com`, where `dot.net` redirects, so the .NET install fails there; Full passes. The Godot fallback mirror needs Custom + defaults plus `downloads.godotengine.org` and `godot-releases.nbg1.your-objectstorage.com`.

## Works
Build; GdUnit4 through `xvfb-run --auto-servernum dotnet test --settings .runsettings --filter <expr>` (`cloud_test_enforcer.py` rewrites a bare `dotnet test`); headless engine runs through `bash .claude/scripts/godot_bin.sh --headless`; git and `gh`; every hook, skill, command and agent not listed below; Workflow dispatch (the Agent tool is denied); WebFetch/WebSearch; SendUserFile. Test logs: `TestResults/godot_test.log`.

## Absent, and what to use instead
| Absent | Use |
|---|---|
| LSP (cloud never starts plugin language servers) | `Grep('class Foo\b', glob='*.cs')` to anchor, then Read; state the coverage limit |
| semantic-search, context7 (cloud skips repo `enabledPlugins`; only plugins enabled on the claude.ai account load) | Grep/Glob; the Godot docs cache, then WebFetch. Use a synced plugin tool only after one real call succeeds; semantic-search's model download needs a host the default allowlist lacks |
| ai-worker MCP | Fallback per CLAUDE.core.md §9 *Offline fallback*; routing nudges naming it are advisory |
| Sidecars | Anthropic Workflow; SessionStart prints no sidecar status line here |
| Obsidian vault (design docs, worklog) | `/worklog` mutations queue to `.claude/worklog-pending.md`. Say a design doc is unavailable; never reconstruct it |
| `/regression_gate`, `verify.ps1` (Windows-bound) | Filtered `xvfb-run` test runs over every touched domain. Report them as a verify, not a gate: the gate runs locally before merge |
| `reap.py` (Windows process table) | `pkill -f Godot_v` only for a Godot your own finished test command left behind |
| Visual playtest, live `godot.log` | Automated tests and `TestResults/godot_test.log` |

## Git and memory
- The GitHub proxy pushes only the session's own branch. Commit there and open a PR through the REST call below; never target `main` directly.
- `gh` reads `GH_TOKEN` from the environment's variables, and a session started before the variable changed keeps the old value. The cloud proxy blocks GitHub GraphQL (HTTP 403), so `gh pr create` and `gh pr list` fail with a valid token. Check the token with `gh api user`, list PRs with `gh api repos/<owner>/<repo>/pulls`, and open one with `gh api repos/<owner>/<repo>/pulls -f title=… -f head=<branch> -f base=main -F draft=true` (basis: thin; the cloud write path is not yet observed).
- The runtime's memory directory is ephemeral here. Write memories to `.claude/auto-memory/` with the `MEMORY.md` pointer and commit them on the branch. SessionStart injects the index as `<auto-memory-index>`.
