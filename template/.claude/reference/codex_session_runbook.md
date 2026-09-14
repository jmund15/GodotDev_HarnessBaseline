# Codex-Hosted Session — runbook and roster probe

Opening a Claude Code harness session whose **session model is an OpenAI model**, orchestrating to
sibling OpenAI models from inside it, and checking whether a newly-announced model is reachable.

Sidecar dispatch — spawning a child on a transport the caller is NOT on — is a different operation
and lives in [`sidecar_dispatch.md`](sidecar_dispatch.md). This file is about being *hosted* on codex.

## Launch

From a PowerShell profile shell (`claude_profile_functions.ps1`):

| Command | Session model |
|---|---|
| `claude-gpt` | `gpt-5.6-luna` · medium |
| `claude-gpt-sol` | `gpt-5.6-sol` · medium |
| `claude-gpt-terra` | `gpt-5.6-terra` · medium |
| `claude-gpt-low` / `claude-gpt-high` | luna at that effort |
| `Invoke-ClaudeGpt -Model sol -Effort high` | any roster alias × any effort |

The launcher starts a dedicated `claude-code-proxy` on its own port and kills it at exit. Never reuse
a running proxy: `CCP_CODEX_MODEL` / `CCP_CODEX_EFFORT` are read from the **server's** environment at
startup, so a shared proxy serves whichever pin its starter set while attesting nothing
(`gotcha_codex_proxy_transport_operations`).

## Orchestrating from inside a codex session

The session resolves its own transport at SessionStart (`hooks/_session_transport.py`), and both
dispatch engines then accept that transport's model ids directly. A Workflow dispatched from a
`sol` session reaches `luna` and `terra` **in-harness — no sidecar**.

**Pin vocabulary is per transport, enforced by `hooks/workflow_provider_guard.py`.** On a codex
session, pin `luna` / `sol` / `terra` (or their full `gpt-5.6-*` ids). Anthropic role names —
`opus`, `sonnet`, `haiku`, `fable` — are Anthropic-session vocabulary and are **denied** here; the
denial names the legal roster. The rule is symmetric: vendor ids are denied on an Anthropic session.

**Effort is per session, not per job.** The proxy honours a per-request model but not a per-request
effort, so every job in a codex-hosted Workflow inherits the session's effort pin. Split by effort
across separate sessions, never across jobs in one fan-out.

## Live proof (unverified end-to-end as of 2026-09-04)

Every piece below is built and passes its structural check; no codex-hosted session has yet run a
real Workflow. Run this to close that gap:

1. `claude-gpt-sol` — confirm the SessionStart block reports transport `codex`, roster
   `luna, sol, terra`, and names sol as the session model.
2. In-session, dispatch a two-agent Workflow pinning `luna` and `terra`. Both must return findings.
3. Confirm the guard bites: pin `sonnet` in a third job. Expect a deny naming the codex roster.
4. Read the proxy traffic capture and confirm the **upstream** request models are `gpt-5.6-luna` and
   `gpt-5.6-terra`. A delegate's self-reported identity is not evidence — behind a translating proxy
   it echoes the client's own pin (`gotcha_self_reported_model_identity_is_not_authority`).
5. Delete the capture afterwards; captures are never pruned and hold full prompt bodies.

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

## Roster snapshot — 2026-09-04

Dispatchable: **`luna`, `sol`, `terra`**, plus **`gpt-6-astra` once the CLI is on ≥ 0.153.0**.

- **GPT-6 Astra: entitled on this ChatGPT plan.** Accepted against a passing `gpt-5.6-sol` positive
  control and a rejected bogus slug, probed on an extracted 0.153.4 binary (2026-09-04). Efforts
  `low|medium|high|xhigh|max|ultra`, `context_window` 272,000 / `max` 872,000, `tool_mode`
  `code_mode_only`, `minimal_client_version` `0.153.0`. The 09-04 "not reachable" reading was the
  stale-client false negative the probe method warns about — installed CLI was 0.148.0.
- **Blocked on the global install**, still `0.148.0`. `npm i -g @openai/codex@latest`, then add astra
  to `reference/external_models.json` before any dispatch pins it.
- **`gpt-5.6-pro`: known-but-unentitled** — in the CLI table, rejected by the backend. Correctly
  absent from the registry; no action.
