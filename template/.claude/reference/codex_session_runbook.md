# Codex-Hosted Session — runbook and roster probe

Opening a Claude Code harness session whose **session model is an OpenAI model**, orchestrating to
sibling OpenAI models from inside it, and checking whether a newly-announced model is reachable.

Sidecar dispatch — spawning a child on a transport the caller is NOT on — is a different operation
and lives in [`sidecar_dispatch.md`](sidecar_dispatch.md). This file is about being *hosted* on codex.

## Launch

From a PowerShell profile shell (`claude_profile_functions.ps1`), use `claude-gpt` or
`Invoke-ClaudeGpt -Model <registry alias> -Effort <legal effort> [-LongContext]`. Run
`python3 .claude/tools/model_registry.py available` for the current Codex roster and
`model_registry.py effort-values <alias>` for accepted effort values; profile function names and
its defaults are launch mechanics, not a second roster. A `-1m` suffix or `-LongContext`
requests the registry's `maxContextTokens` window using `[1m]`, not another model row.

Use long context only for a named turn that needs more than the default window printed by
`model_registry.py context-window <model>`. The threshold and multipliers live in
`reference/external_models.json` (`longContextTier`); compact after that turn. The Codex band
in `[budget-posture]` governs concurrency. A Codex sidecar's `-M` (fan-out job
`"longContext": true`) holds the max window for the whole run, so the child never compacts at the
default window: use it only to resume (`-r`) a run already past the default window, never on a
fresh job.

The launcher starts a dedicated `claude-code-proxy` on its own port and kills it at exit. It clears
`CCP_CODEX_MODEL` and `CCP_CODEX_EFFORT` before proxy startup, so model and effort stay request-scoped.
The sidecar launcher sets those server-wide pins on its own proxy for strict dispatch attestation.

## Orchestrating from inside a codex session

Pin a **canonical Codex model ID** on each native Workflow job. The transport guard injects the
legal vocabulary from the registry, so sibling models run in-harness without a sidecar. Resolve
the work shape with `model_registry.py for-role <tier>` and choose effort from
`reference/model_ladder_evidence.md`; that command reports rows, not a decision.

A sidecar's `-m` takes a registry alias; a Codex-hosted Workflow's `model` takes the canonical ID.
The provider guard denies Anthropic role names on a Codex session and Codex IDs on an Anthropic
session. Per-job effort must be legal for the specific model row; the guard checks that before
spawn. To verify that a proxy honored a request, inspect its traffic for the served model and
`reasoning.effort` rather than taking a delegate's self-report as attestation.

## Live proof for a hosted Workflow

A Codex **sidecar** record does not prove that a Codex-hosted native Workflow forwards each
job's model and effort. When this distinction matters, verify the hosted path:

1. Launch a Codex session on a current registry row and confirm SessionStart names its transport.
2. Select two distinct in-transport native IDs with `for-role`, then dispatch a two-agent Workflow
   with explicit model and effort for each.
3. A third job naming an Anthropic role must be denied before spawn.
4. Check the proxy traffic's upstream `model` and `reasoning.effort` for both jobs. A child claiming
   its identity is not attestation (`gotcha_self_reported_model_identity_is_not_authority`).
5. Remove the capture once checked; it contains full prompt bodies.

`python3 .claude/tools/verify_transport_status.py --transport codex` checks the transport
surfaces, not that live per-job traffic proof.

## Is model X on the plan? — the probe

Roster SSOT is `reference/external_models.json`. To test a model that is not in it:

```bash
codex exec -m <candidate-id> -s read-only --skip-git-repo-check "Reply with exactly: ok"
```

**Run a positive and a negative control in the same sweep.** Select a known-good current ID from
`model_registry.py available`; it must be accepted, and a bogus ID must be rejected, or the
sweep proves nothing.

Two traps make a naive read wrong, and one of them silently inverts the conclusion:

- **The rejection is generic** — byte-identical for an unknown slug and for a real-but-unentitled
  model, so it can never distinguish them.
- **`codex exec` exits 0 on rejection.** Parse stdout; never branch on `$?`.

The discriminator is the **client-side** model table compiled into `codex.exe` (npm package →
platform vendor `bin/`). A model absent there is undispatchable regardless of entitlement, so an
out-of-date CLI makes every negative uninformative — check installed-vs-latest before recording one.
Method, controls and the false-positive shapes: `gotcha_model_availability_probe_error_is_generic`.

**Probe the current table without upgrading the install.** The vendor package is an npm alias, so
resolve its real name from `npm view @openai/codex@<ver> optionalDependencies` first, then pack,
extract, and run the probe against the extracted `codex.exe` — it reads `~/.codex` auth directly:

```bash
npm pack @openai/codex@<ver>-win32-x64 && tar xzf openai-codex-<ver>-win32-x64.tgz
B=package/vendor/x86_64-pc-windows-msvc/bin/codex.exe
grep -a -A85 '"slug": "<candidate>"' "$B"   # the entry, pretty-printed JSON
"$B" exec -m <candidate> -s read-only --skip-git-repo-check "Reply with exactly: ok"
```

Each entry carries `minimal_client_version`; that field, not the announcement date, is why a slug is
missing from an older CLI. Delete the extraction afterwards.

## Current roster

`model_registry.py available` names current and excluded rows; `for-role <tier> --from codex`
shows the rows that claim a work shape. `external_models.json` owns version, context limits,
availability and any exclusion reason. This runbook records mechanics, not a roster snapshot.
