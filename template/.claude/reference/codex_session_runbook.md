# Codex-Hosted Session — runbook and roster probe

Opening a Claude Code harness session whose **session model is an OpenAI model**, orchestrating to
sibling OpenAI models from inside it, and checking whether a newly-announced model is reachable.

Sidecar dispatch — spawning a child on a transport the caller is NOT on — is a different operation
and lives in [`sidecar_dispatch.md`](sidecar_dispatch.md). This file is about being *hosted* on codex.

## Launch

From a PowerShell profile shell (`claude_profile_functions.ps1`):

| Command | Session model |
|---|---|
| `claude-gpt` | `gpt-5.6-luna` · medium · default context |
| `claude-gpt-sol` / `claude-gpt-terra` / `claude-gpt-astra` | named model · medium · default context |
| `claude-gpt-low` / `claude-gpt-high` | luna at that effort · default context |
| `<any command above>-1m` | same model and effort · max context |
| `Invoke-ClaudeGpt -Model sol -Effort high -LongContext` | any Codex roster alias × effort × context tier |

The `-1m` launchers pass the proxy's `[1m]` request modifier and set Claude Code's declared window
from `maxContextTokens`. The name is conventional; the exact effective limit comes from
`external_models.json`, printed by `model_registry.py context-window <model> --max`. `[1m]` stays a
request modifier, not a duplicate registry row.

**Launch standard; use `-1m` only for a named turn that needs more than ~258K of live context.** Each
request whose prompt exceeds the codex `longContextTier` threshold in `external_models.json` bills at
its multipliers, and a long session re-sends its whole context every turn, so most `-1m` turns land in
the tier (cost: `gotcha_long_context_gpt_sessions_cost_by_context_size`). `/compact` once that turn is
done. Concurrency has no fixed count; the codex band in `[budget-posture]` gates a new session.

The launcher starts a dedicated `claude-code-proxy` on its own port and kills it at exit. It clears
`CCP_CODEX_MODEL` and `CCP_CODEX_EFFORT` before proxy startup, so model and effort stay request-scoped.
The sidecar launcher sets those server-wide pins on its own proxy for strict dispatch attestation
(`gotcha_codex_proxy_transport_operations`).

## Orchestrating from inside a codex session

Pin a Codex model id on each Workflow job. The SessionStart transport marker lets both dispatch
engines accept sibling ids directly, so a `sol` session reaches `luna` and `terra` in-harness with no
sidecar.

Pin `luna` / `sol` / `terra` or their full `gpt-5.6-*` ids. `hooks/workflow_provider_guard.py`
denies Anthropic names (`opus`, `sonnet`, `haiku`, `fable`) and names the legal Codex roster. The
same guard denies vendor ids on an Anthropic session.

Per-job Workflow effort passthrough is not yet proven end to end. Treat the request as unverified
until proxy traffic attests `reasoning.effort`. `/model` and `/effort` do change the live parent
session; the launcher sets their starting values.

## Live proof (unverified end-to-end as of 2026-09-12)

No successful codex-hosted Workflow has yet proved both model and effort routing. Run this to close
that gap:

1. `claude-gpt-sol` — confirm SessionStart reports transport `codex`, roster `luna, sol, terra`,
   and sol as the session model.
2. Dispatch a two-agent Workflow: `luna` at one effort and `terra` at another. Both must return.
3. Pin `sonnet` in a third job. Expect a deny naming the Codex roster.
4. Read proxy traffic and confirm upstream `model` values are `gpt-5.6-luna` and
   `gpt-5.6-terra`, and each `reasoning.effort` matches its job's request. A delegate's identity claim
   is not evidence behind a translating proxy (`gotcha_self_reported_model_identity_is_not_authority`).
5. Delete the capture; it has full prompt bodies and no pruning.

Verify the surfaces agree at any time with `python3 .claude/tools/verify_transport_status.py
--transport codex`.

## Is model X on the plan? — the probe

Roster SSOT is `reference/external_models.json`. To test a model that is not in it:

```bash
codex exec -m <candidate-id> -s read-only --skip-git-repo-check "Reply with exactly: ok"
```

**Run a positive and a negative control in the same sweep.** A known-good id (`gpt-5.6-sol`) must be
ACCEPTED and a bogus id must be REJECTED, or the sweep proves nothing.

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
missing from an older CLI. Delete the extraction afterwards — the binary is ~295 MB.

## Roster snapshot — 2026-09-12

Session drivers: **`luna`, `sol`, `terra`, `astra`**. Delegate targets: **`luna`, `sol`,
`terra`**. Luna can also be selected by role; Sol and Terra require explicit pins. Context limits live
in `reference/external_models.json`.

- **GPT-6 Astra is entitled and session-launchable but owner-excluded from all delegation.** It needs
  Codex CLI ≥ 0.153.0; `claude-gpt-astra` and `claude-gpt-astra-1m` remain valid.
- **`gpt-5.6-pro` is known but not entitled.** It remains absent from the registry.
