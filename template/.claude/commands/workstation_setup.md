---
description: Provision or audit this harness on a workstation
stamp-inputs:
  - ".mcp.json"
  - ".claude/reference/project_stack.md"
  - ".claude/commands/workstation_setup.md"
  - ".claude/commands/reference/workstation_setup_land_runner.md"
  - ".claude/tools/user_scope.py"
  - ".claude/user_scope/manifest.json"
  - ".claude/user_scope/statusline.py"
  - ".claude/user_scope/hooks/ollama_healthcheck.py"
  - ".claude/user_scope/hooks/large_read_reminder.py"
---

# /workstation_setup

Run after cloning the repo onto a fresh machine, or re-run to audit one. The agent executes every
step it can, verifies each with a concrete check, and ends with the report table below. Secrets are
never created by the agent — only verified present and reported missing. `environment_bootstrap`
holds this machine's ground truth and troubleshooting; this command owns provisioning.

**Idempotency:** every phase starts with its verification check; a passing check skips the phase.
A provisioned machine produces an all-green table and no writes.

**`stamp-inputs:` above is the drift list, and this file is in it.** SessionStart hashes those paths,
plus the inputs each `commands/reference/workstation_setup_*.md` declares the same way, and compares
them with this machine's last run of this command; a changed one is reported as
`workstation STALE (changed: …)` until this command runs again. Add a path here when a new file
joins the setup surface — the list is read from this frontmatter and is not duplicated anywhere.

**Start by recording this machine:** `python3 .claude/tools/workstation_profile.py record` probes
`nvidia-smi` itself and stores `{gpu, local_tier}` in `~/.claude/workstation_profile.json`. Those
thresholds live in that tool alone, in MiB — the unit the card actually reports, where the marketing
size is not; `user_scope/hooks/ollama_healthcheck.py` reads the recorded tier. Phase 3 takes its tiers from that
tool, and no caller here passes a VRAM count.

**Engine toolchain.** Phase 8 and Phase 9 step 3 come from the `workstation_setup_toolchain` reference
in `.claude/commands/reference/`, a file only the godot layer ships. A checkout without that file
reports one NOT-ADOPTED row for them, runs every other phase, and counts the gap as no failure.

## Phase 0 — Repo integrity

1. `git -C <repo> submodule update --init --recursive`; `git -C <repo> submodule status` shows no
   `-` (uninitialized) prefix.
2. `git -C <repo> status` clean; `main` present.
3. Harness baseline: `python3 .claude/tools/baseline_sync.py check --strict`. Exit 0 → PROVISIONED.
   A non-zero exit is reported with its states; `/sync_baseline` owns the follow-up. Do not pull
   unprompted.
4. `git -C <repo> config core.hooksPath .claude/githooks`; `git -C <repo> config --get core.hooksPath`
   prints `.claude/githooks`. Its commit-msg hook re-checks at commit time that the commit reverts no
   peer's commit.

## Phase 1 — Prerequisites (verify, report versions)

| Check | Command | Requirement |
|---|---|---|
| claude CLI | `claude --version` | current; the sidecars and the MCP idle-timeout knob (Phase 3) depend on it |
| Python | `python3 --version` from the Bash tool, not a WSL shell | 3.10+; UTF-8 mode comes from `settings.json` `env.PYTHONUTF8` |
| git + gh | `git --version`, `gh auth status` | gh authenticated (baseline publish and PRs) |
| Node | `node --version` | any LTS (workflow scripts, Node MCP servers) |
| pwsh | `pwsh --version` | answers (PowerShell Core, not `powershell`): the `claude-*` shell integration and the land runner task launch through it |
| Git bash | `python3 -c "import sys; sys.path.insert(0,'.claude/tools'); import sidecar_launch; print(sidecar_launch.git_bash())"` | prints a Git for Windows `bash.exe` path; a bare `bash` can be WSL's (`environment_bootstrap` §This-machine ground truth) |
| WSL | `wsl -l -q` lists a distro with `python3` | optional: only `baseline_linux_preview.py` needs it, and a baseline publication that owes a preview is red without it → DEGRADED when absent |

A missing required item → BLOCKED-ON-USER row with its install link; continue with the other phases.
Engine prerequisites are the toolchain reference's (Phase 8).

### User scope — home-folder files the harness depends on

`.claude/user_scope/manifest.json` lists the files outside the repo that sessions rely on (the statusline
that `hooks/budget_posture.py` reads, the user hooks). The optional `manifest.local.json` holds the owner's
own items, including the settings keys the shared project file cannot carry, such as `syncClaudeAiSkills`,
and the Chrome default in `~/.claude.json`, which the tool only reads. The claude.ai connectors,
bundled-skill hiding and plugin choices ride in `settings.project.json`. Phase 2 owns
`autoMemoryDirectory`, so leave a manifest item for that key to it. `.claude/tools/user_scope.py` checks
and installs the items from the templates beside the manifests. Run this before `sync_plan_config.py`
runs: it carries the registered settings into the plan logins.

1. `python3 .claude/tools/user_scope.py check`. A `drift` or `register-differs` row is PROVISIONED, with the
   copy commands `check` prints in Evidence (the owner edits these files live; `adopt <id>` carries a home
   edit into the template). A `missing`, `unregistered`, `unset` variable or `unset` key row is the next step's; a `differs` key keeps the
   home value; a `manual` row is DEGRADED until the owner makes the change it prints in the app; an `unreadable`
   row is DEGRADED until `~/.claude.json` parses, so run `check` again.
2. Ask the owner, naming each missing file, each key it adds to `settings.json` or this checkout's
   `settings.local.json`, each hook command it registers and each user environment variable it sets; on a yes,
   `python3 .claude/tools/user_scope.py install`, then `check` again. `install` writes only what was missing and
   keeps a `.pre-user-scope` copy of the first edit to each settings file.
3. `python --version` from the Bash tool, the shell the registered hook commands run in (the table above proves only
   `python3`). A failure → DEGRADED for the statusline and hook consumers.
4. Credential files and per-machine values (the manifest's `local` entries) are reported by their own phases
   and never copied.

### Tool updates — report only

After the table above, list each tool below with a newer release than this machine runs, as one yes/no
row per tool. This command never applies an update to a pinned tool, whose bump can break what
this audit just verified. The exception is the plugins `sync_plan_config.py` owns, which it brings
to their marketplace's latest on every login (Phase 4 step 2). An unreachable registry is DEGRADED.
The toolchain reference adds its own rows.

| Tool | Installed | Latest | Upgrade owner |
|---|---|---|---|
| claude CLI | `claude --version` | `npm view @anthropic-ai/claude-code version` | `claude update` |
| semantic-search plugin | version in each config dir's `installed_plugins.json` | `npm view @dreb/semantic-search version` | applied by `sync_plan_config.py` (Phase 4 step 2) |
| gh, opencode, ollama, node | `--version` | their release pages | the user's package manager |

A gap appears as `UPDATE-AVAILABLE <installed> → <latest>` in the row's Evidence column; the
row keeps its own status.

## Phase 2 — Auto-memory directory

`autoMemoryDirectory` makes a session load `.claude/auto-memory/` instead of the runtime's per-user
store. The setting takes only an absolute or `~/` path, so no tracked settings file can carry it: it
lives in this checkout's gitignored `.claude/settings.local.json`. A session already running keeps
its old value; start a new one after a change.

1. Expected value: `<main checkout root>/.claude/auto-memory`, with forward slashes. The main root is
   the parent of `git -C <repo> rev-parse --path-format=absolute --git-common-dir`, so a linked
   worktree names the one shared store.
2. `git -C <repo> check-ignore -q .claude/settings.local.json` exits 0. Otherwise DEGRADED, and no
   write: the file would carry a machine path into a commit.
3. Read `.claude/settings.local.json`. `autoMemoryDirectory` equal to the expected value (slash style
   and drive-letter case ignored) → PROVISIONED, no write. A missing file, a missing key or a
   different value → set the key to the expected value, keep every other key, write the whole file as
   UTF-8 JSON with Edit or Write, and read it back. Evidence names the old value beside the new one.
   An unparseable file → BLOCKED-ON-USER naming it; never overwrite it blind.
4. The expected directory holds `MEMORY.md`. Absent → DEGRADED: the index a session loads is missing.

## Phase 3 — ai-worker MCP

1. `ToolSearch("select:mcp__ai-worker__list_models")`, then call it. A response → go to step 3.
2. Absent: config lives at `~/.config/ai-worker/models.yaml` (hot-reloads); routing and model
   facts live in `~/.claude/ai_worker_model_guide.md`. No repo file installs the server: report it
   DEGRADED with the missing path.

   **Every Claude account.** A server registers in that account's user config
   (`<CLAUDE_CONFIG_DIR>/.claude.json`); a `settings.json` `mcpServers` key is ignored. For each
   `~/.claude*` config dir, `CLAUDE_CONFIG_DIR=<dir> claude mcp list` shows `ai-worker` and
   `plugin:semantic-search` connected. Add a missing one with `claude mcp add-json -s user` from a
   JSON file, since inline backslashes collapse in Bash. The Obsidian MCP is retired: remove it
   where found.
3. **Classify the box by the recorded `local_tier`** — `full` installs the local models `models.yaml`
   assigns to roles; `small` installs only the small arms, leaving the 14B- and 30B-backed roles
   unset; `none` installs no local arm, and leaves ai-worker uninstalled unless `models.yaml` has a
   funded cloud alias for its roles.
4. **An armless ai-worker is a valid end state** → DEGRADED, never BLOCKED-ON-USER. Bulk reads use
   the CLAUDE.md §Tool Routing offline fallback.
5. **Single-GPU concurrency env — report, don't set.** Each session runs its own ai-worker, and
   nothing queues across them. Report a value other than `1` for `OLLAMA_NUM_PARALLEL` (requests per
   loaded model) or `OLLAMA_MAX_LOADED_MODELS` (default 3× the GPU count).
6. **`OLLAMA_KV_CACHE_TYPE` must be `q4_0`** — BLOCKED-ON-USER otherwise. The pinned `qwen-local`
   build degenerates under `q8_0`; higher precision is not safer here (evidence: the `qwen-local`
   block in `models.yaml`). The value takes effect only after an Ollama restart, so confirm it
   against the ledger's `resident_bytes`, not the declared value.
7. **MCP idle timeout.** ai-worker sends a progress notification every 20 s, so Claude Code's
   30-minute stdio idle abort fires only on a hung server. No setting is required.
8. Credentials: `~/.env.ai-worker.cmd` supplies cloud keys. Only providers
   `reference/external_models.json` marks available need one.
9. **Record the registration this phase observed:** `python3 .claude/tools/workstation_profile.py
   record --ai-worker present|absent`. The value is kept for the report; routing reads the live
   registration, never this field.

## Phase 4 — Model registry and sidecars

1. `python3 .claude/tools/model_registry.py --check` exits 0. It is a hard dependency of every
   sidecar. A `price.asOf is N days old` warning means re-probing the vendor page it names.
2. For each `.claude/scripts/*_sidecar.sh` with a `--check` mode, run `bash <script> --check`:
   `OK (…)` → PROVISIONED. `UNAVAILABLE` for a provider the user does not use → DEGRADED with the
   reason printed; for one they do use → BLOCKED-ON-USER naming the missing key or install. On a
   `go` row, `--check` makes one small live call that spends a little Go plan quota: run it once
   per setup, never in a loop.
   Before those checks, run `python3 .claude/scripts/sync_plan_config.py` once. It syncs each further
   Anthropic plan's `configDir` with `~/.claude`: the config files, then on every login each enabled
   plugin at its marketplace's latest version with its post-install steps (for semantic-search, the
   Windows build and that login's idle shim). Exit 0 → PROVISIONED. A missing plan dir → BLOCKED-ON-USER:
   log in to it once with `claude` under that `CLAUDE_CONFIG_DIR`. A `BLOCKED-ON-USER` line (a
   marketplace command awaiting `--accept-command`) → that row, quoting the line. A `plugins: deferred`
   line → DEFERRED-PEER-CONTENTION with the same command, run after the named benchmark ends. A
   `plugins: ... failed` line → FAILED, quoting the line.
3. **OpenCode credentials and CLI.** `~/.env.ai-worker.cmd` carries `set OPENCODE_API_KEY=<key>`
   with a real key: Go refuses the `public` fallback. `opencode --version` answers and
   `~/.local/share/opencode/auth.json` exists, since the CLI reads its key there. A missing key while
   the user uses Go → BLOCKED-ON-USER; a missing CLI → DEGRADED, naming the install.
4. **Shell integration.** `.claude/scripts/claude_profile_functions.ps1` defines the `claude-*`
   launch functions. The user's `$PROFILE` must dot-source it, never carry copies of the bodies.
   Verify: `pwsh -NoLogo -NonInteractive -Command "Get-Command claude-primary, claude-deepseek, claude-gpt"`.
   If it fails, wire the dot-source and re-verify. Inline function bodies in `$PROFILE` are drift:
   replace them with the dot-source.
   When `environment_bootstrap` names a shared `$PROFILE` with a repo-root list, add this machine's
   repo root to it and keep other machines' entries. A launch banner with blank rates means step 1
   failed.

## Phase 5 — Semantic-search plugin

1. Run `python3 .claude/scripts/sync_plan_config.py`; a missing plugin installs through it. It needs
   the DreB marketplace known to `~/.claude` (`claude plugin marketplace add aebrer/dreb` once) and
   `semantic-search@dreb` enabled in `~/.claude/settings.json` `enabledPlugins`. It also pre-builds
   each new version, because a plugin that self-installs through `execFileSync('npm', ['install'])`
   fails on Windows with `spawnSync npm ENOENT` (a `Status: failed` MCP card). Its build check is
   `timeout 3 node <plugin-cache-dir>/bin/server.js` exiting 0 with empty stderr, stdin at EOF.
   Its output lines read as in Phase 4 step 2. Then
   `ToolSearch("select:mcp__plugin_semantic-search_semantic-search__search")` answers; absent →
   restart the session.
2. `.search-index/search.db` is gitignored, so a fresh clone lacks it: run `/reindex_search`, then
   one query naming a known subsystem returns results.

## Phase 6 — Obsidian vault

1. The vault paths CLAUDE.md §3 names resolve under `{{VAULT_ROOT}}\DevProjects\`.
2. A different vault location on this machine → report the delta. The harness hard-codes the root,
   so the user changes it, or approves the edit.
3. Spot-read `Worklog.md`: content, not OneDrive placeholder stubs.

## Phase 7 — Land runner

Applies when `land_prefix` in `.claude/skills/project_subsystems/adaptation.json` is non-empty: the
project's land runner proves and lands every push to a branch under that prefix, and with none online
those pushes queue. An empty or absent `land_prefix` → one NOT-ADOPTED row, skip the steps. A machine
the user does not land from → DEGRADED, skip the steps.

Run the three checks of [`workstation_setup_land_runner.md`](reference/workstation_setup_land_runner.md).

## Phase 8 — Engine toolchain

Read the `workstation_setup_toolchain` reference and run its phases T1–T6 in order. Its T7 runs inside
Phase 9. With no such file in `.claude/commands/reference/`, report `Engine toolchain | NOT-ADOPTED |
godot layer not installed`, skip Phase 8, and skip Phase 9 step 3.

## Phase 9 — Smoke gate

1. `python3 .claude/tests/test_workstation_profile.py` ends with `all ok`: Python, git and a writable
   temp dir work together on this machine.
2. The project's build command (the Build line of `.claude/reference/project_stack.md`) succeeds, and
   one small test subset through the project's test command reports real pass counts, not zero-match.
   A project with no build or test command → NOT-ADOPTED for that row. A machine-wide single-flight
   tool that a peer run holds → mark the row DEFERRED-PEER-CONTENTION with the exact command.
3. The toolchain reference's T7 checks.

## Report format

End with one table: `| Component | Status | Evidence / next action |`. Status is one of
PROVISIONED, DEGRADED, FAILED, BLOCKED-ON-USER, DEFERRED-PEER-CONTENTION or NOT-ADOPTED (a layer or
feature this checkout does not use; no action); one row per phase item.
A BLOCKED-ON-USER row names the exact artifact the user supplies (key file, install, path
decision). A DEFERRED row carries the exact command to run later.

Then, last: `python3 .claude/tools/workstation_profile.py stamp`. It re-runs the cheap checks it can
observe for itself — those each installed layer module supplies, such as an engine layer's toolchain
and feed folders — and **refuses on any failure**, so a stamp never certifies a machine this audit
would not have passed. It takes no count from you; report its exit and its message, and pass each
DEGRADED row's name with `--degraded NAME` (repeatable) so the record keeps what this machine is
weak at beside what it has. That stamp is what later sessions compare against.
