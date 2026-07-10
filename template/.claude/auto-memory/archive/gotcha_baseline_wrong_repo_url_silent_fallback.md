---
name: gotcha-baseline-wrong-repo-url-silent-fallback
description: "A wrong/unreachable baseline_repo URL makes /sync_baseline fall back to --baseline-dir; a \"clean\" check there does NOT prove sync with the real published remote."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 01c8633d-c4e3-4b41-88df-21aa59609efb
---

If `baseline.lock.json`'s `baseline_repo` names a repo that doesn't resolve, `baseline_sync.py`'s clone 404s and the workflow falls back to `--baseline-dir <local-staged-copy>`. A `check` that reports **clean against the staged copy does NOT prove the project is in sync with the actual published baseline** — the staged in-repo copy can silently accumulate changes that never reach the shared remote.

**Why it's dangerous:** "clean" + "upstreamed" feels done, but you may have only reconciled against a local mirror. The real shared baseline stays stale, and other projects pulling from it get nothing.

**How to apply:**
- Treat *"the sync only works when I pass `--baseline-dir`"* as a RED FLAG that `baseline_repo` is misconfigured — don't just keep passing the flag. Verify the URL resolves: `gh repo view <owner>/<repo>` or `git ls-remote <url>`.
- The authoritative sync state is `check` run **without** `--baseline-dir` (it clones `baseline_repo`). If that 404s, fix the URL first; a `--baseline-dir` result is only as current as that local copy.
- To measure staged-vs-remote drift, clone the real remote and compare *normalized* (CRLF→LF) — raw `diff -rq` inflates the count massively because a Windows working tree is CRLF and the git-stored remote is LF (see the normalize() step the engine uses for hashing).

**Sibling false-clean mode — `watch` status:** a tracked file with `status: watch` (e.g. `sync_baseline.md`, CLAUDE.md, seed skills) is *never hash-compared* — `check` only notes if it changed, never as actionable drift. So a *universal* command/skill placed under `watch` can silently rot in a consumer (this session, the consumer's `sync_baseline.md` was 10 lines / a whole `audit` section behind the baseline, yet `check` read clean twice). When auditing whether a universal artifact is current, byte-compare it against the baseline directly — don't trust a clean `check`, which is blind to watch files by design.

**Verified:** 2026-06-14 — lock named `jmund15/harness-baseline` (404); real repo is `jmund15/GodotDev_HarnessBaseline`. The staged `harness-baseline/` dir was ahead of the remote by 22 files (18 mod + 4 new) of un-published improvements. Resolved: pushed staged→remote (remote `81d2c38`), corrected lock URL + synced_commit (`6aec8864`). A second consumer (DraconicWars) had `baseline_repo` pointed at its own game repo — same class, corrected to the shared baseline. Related: [[gotcha-baseline-forked-file-drifts-behind]].
