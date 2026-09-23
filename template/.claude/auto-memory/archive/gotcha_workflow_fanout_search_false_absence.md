---
name: workflow-fanout-search-false-absence
description: "In workflow fan-out, agents' Grep/Glob/semantic-search return empty INTERMITTENTLY under concurrency (and deterministically for the OneDrive vault) — an empty search reads as false 'absence' and silently produces clean-but-fabricated audit verdicts. Push content via args; agents must not discover."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c85a2c2a-bc96-40bf-b8ea-6d889e7de3a0
---

Observed 2026-05-31 running a 21-agent discovery workflow on a 24-core Windows box (workflow concurrency cap = min(16, cores-2) = 16 agents each spawning ripgrep/glob at once).

**Symptoms (three distinct, do not conflate):**
1. `Grep` / `Glob` / `semantic-search` return EMPTY *intermittently* on targets that exist — reproduced same-session (failed, then succeeded, same target kind). `Bash`/`git` (`git grep`, `git ls-files`) and `Read` stayed reliable. Likely concurrent-file-tool contention on Windows; worse under wide fan-out.
2. Many fanned agents hit it simultaneously → multiple lane agents reported "could not read files, reasoned from briefing only." Audit findings from those agents are unverified.
3. The Obsidian vault (`{{VAULT_ROOT}}\...`) ALSO returned empty intermittently — one `Bash ls` reported "No such file or directory / 0 files", a later identical call found 405 `.md`. So vault access flakes under the SAME fault; it is NOT a permanent OneDrive-cloud-only block. Do not conclude "vault unreachable" from one empty result — retry via Bash. The local game repo under `Game_Dev\` stayed reliable throughout.

**Why it's dangerous:** an empty search is indistinguishable from genuine absence, so a verification/audit agent silently emits a clean bill. Worst failure mode for the whole audit-workflow class.

**How to apply (mandatory for any audit/verification workflow):**
- **Push-don't-pull:** Claude does discovery + content-fetch via `Bash`/`git`/`Read` (reliable) and passes file LIST + CONTENT into agents via `args`/CONTEXT. Agents judge pushed content; they do NOT discover. (Reinforces [[workflow-integration-mechanics]] push-don't-pull from a correctness angle, not just token cost.)
- If an agent MUST discover, instruct it to use `git grep`/`git ls-files`, not `Grep`/`Glob`, and to **treat an empty result as INCONCLUSIVE (retry via git), never as absence.**
- Do discovery Claude-side (serial, reliable); fan out only the JUDGMENT over pre-fetched content. Don't fan out raw file-discovery at 16-wide.
- Surface lens-completion in the script (the `doc_architecture_audit.js` `lensStatus`/`failedLenses` pattern) so a silently-failed lens doesn't read as a clean pass.
- Vault reads can transiently fail under the same fault — retry via Bash before concluding the vault is unreachable, and have Claude read + push content rather than relying on agent-side vault reads.
- **Large/escape-dense CONTEXT → scratch file, not `args`:** when the pushed CONTEXT is big or is escape-dense code (braces/quotes/newlines), embedding it in `args` risks malformed JSON (see [[workflow-args-generation-fidelity]]). Instead write CONTEXT to a scratch `.md` and have each fanned agent `Read` it by ABSOLUTE path — direct Read-by-path is reliable (unlike Grep) and sidesteps BOTH the false-absence and the args-fidelity gotchas at once.

Related: [[grep-glob-miss-tracked-files]] (general "empty Grep/Glob ≠ absence; verify via git"), [[workflow-single-flight-concurrency]] (the other concurrency wall), [[workflow-integration-mechanics]].
