---
name: gotcha-auto-mode-classifier-fail-closed
description: "Diagnose auto-mode denials from the actual result; the shell-shape workaround is retired, not evidence of a current failure"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1575807a-659b-4f41-9af8-9997b17e978c
  modified: 2026-09-11T14:25:58.997Z
retire_when:
  - review-by: 2027-02-14
---

# Auto-mode denial diagnosis and retired shell-shape workarounds

> **SUPERSEDED for the five guarded shapes (measured 2026-09-01).** With `bash_shape_guard.py`'s
> settings.json matcher neutered, all five ran silently, zero prompts: `cat <<EOF`; `python3 - <<EOF`
> reading a file; `python3 - <<EOF` **writing** a file; `$(git rev-parse)`; backticks; `echo x &`;
> `cd .claude/cache && mkdir … && touch …`. The 2.1.233 revert below is the reason. The guard's five
> denials are now commented out in the hook (uncomment to restore).
>
> Still live, still unmeasured — do NOT read the above as clearing them: `"$GODOT_BIN" <args>`
> (allow rules match EXPANDED text, so an env-var form matches no rule), backslash-escaped
> whitespace in paths, and `< file` input redirection.
>
> Classifier is not dead — it judges intent over prefix: `sed -i` against the guard file itself was
> DENIED while `Bash(sed *)` sat in `permissions.allow`.
>
> **Confound:** `permissions.allow` still carries broad `Bash(cat *)`, `Bash(python3 *)`,
> `Bash(bash *)`, `Bash(sed *)`. The clean runs may owe to those, not to classifier tolerance.
> `/auto-mode-setup` proposes removing the interpreter entries — if they land, re-run the test.

**The rule (as of 2.1.232, since reverted):** in auto permission mode (`permissions.defaultMode: "auto"`), a Bash command auto-approves only when it matches a static allow rule (narrow rules carry over into auto mode; `classifyAllShell` defaults false). Non-matching commands go to the auto-approval classifier — but the platform refuses delegation for shapes it cannot statically verify: observed verbatim refusal — "this request cannot be delegated to the auto-approval classifier". Changelog-confirmed origin (2026-08-14): the 2.1.232 auto-update (landed 2026-08-13 22:10 per the session-store version timeline) made Bash input redirections (`< file`, heredocs) permission-checked and required approval for writes through Cygwin-style symlinks; 2.1.233 (2026-08-14) reverted both as a regression ("fixed auto mode repeatedly stopping for manual approval on ordinary `cd <dir> && <command> > file` Bash commands"), promising a narrower version later. Those shapes fall through to a MANUAL prompt — the autonomous-session stall.

**Historical explanation (2.1.232, since reverted):** the client refused classifier delegation for these shapes. This does not establish why a current call is denied.

**Historical workarounds (not current instructions):**
- `git -C <abs>` for repo-scoped commands; never `cd <path> && git …` (CLAUDE.md §Shell Discipline).
- Cross-branch content checks: `git -C <wt> log --all -G'<pat>' --oneline | head -20`.
- Ref enumeration: `git -C <wt> grep -l -E '<pat>' refs/heads/* refs/remotes/*/* -- '*.cs'` (globs must expand or git errors on the literal; nested local branches add `refs/heads/*/*`). Never `for b in $(git for-each-ref …)`.
- Probe/script files: Write tool, never heredoc `cat > … <<'EOF'`.
- Engine invocations in auto mode: a repo launcher script run as `bash <launcher> <args>` (statically allowlisted via `Bash(bash *)`; resolves the engine internally) or the literal install path — the raw `"$GODOT_BIN"` form prompts (expansion flag blocks rule matching), never emit it. `$GODOT_BIN` remains the machine-level env pin.
- Multi-step probes: committed wrapper under `.claude/scripts/`, invoked `bash <script> <worktree>`.
- **The guard scans the WHOLE command string, so a shell metacharacter denies even inside quoted DATA.** A backtick in a `sed` *replacement* — `sed -i 's/x/see \`orchestration\` §5b/'` — reads as command substitution and is denied, though nothing would expand. Markdown citations are backtick-dense, so any `sed` rewriting doc citations trips this: route those to the `Edit` tool, which needs no shell.

**Verify current behavior:** a renewed prompt is not proof the retired shell-shape regression returned. Record the actual tool result and distinguish a hook denial from a classifier verdict.

**Current hook status:** `.claude/hooks/bash_shape_guard.py` leaves its shell-shape denial blocks commented out. For current denials, use `/sync_permissions` §Identify the denial layer before changing permissions; a renewed prompt alone does not justify adding or reactivating a guard.

**Timeline facts (verified 2026-08-14):** `Bash("$GODOT_BIN":*)` has been in settings.json since 2026-03-02 and provably never matches (the matcher compares expanded command text — the refusal text proves no rule matched). `rules/godot_files.md:14` mandated the raw `"$GODOT_BIN" --editor --path` form only from 2026-08-14 (queued-edit batch) — now switched to the wrapper form. The dead rule is a remove-on-triage candidate — removed since: no `$GODOT_BIN` allow entry remains in `settings.json` (2026-09-04 memory-claim audit).

**Class lesson (2026-09-01):** a guard that works around a PLATFORM defect outlives the defect
silently — the vendor ships a fix, the workaround keeps costing, and nothing signals the change
because the guard's own denial reads exactly like the bug it was written for. Any hook whose
justification is "the platform does X" states the version it was measured against and gets re-tested
when the symptom stops being observed independently. A guard is not evidence of the thing it guards.

Related: [[gotcha_workflow_args_permission_control_chars]] (same fail-closed permission layer), [[archive_claude_code_permissions_location]].

**Gate-loosening harness edits are denied by content, not by shape (2026-09-01):** an `Edit`, `Write` or python rewrite that REMOVES or LOOSENS a halt valve, approval gate, "wait for user input" line or numeric attempt cap in a `.claude/` command file is denied by the auto-mode classifier for delegates AND for the driving session alike; writing a script that merely CONTAINS such an edit is denied too. Additions, pointer rewrites and duplicate-row deletions in the same files pass. Do not retry through another mechanism: list the exact intended text under `couldNotSatisfy` and hand it to an interactive session for approval.

