---
name: archive_hook_gotchas
description: Hook_Gotchas — 6 archived observations (TechnicalKnowledge). Cold-tier reference.
metadata: 
  node_type: memory
  type: reference
  tier: cold
  source: archive
  entity_type: TechnicalKnowledge
  originSessionId: 34f04e8e-de64-46f0-bcc5-2758e6139a6f
---

# Hook_Gotchas

> Cold-tier archive (TechnicalKnowledge). Search-only — not auto-loaded.

- Hooks: name by purpose not type. Hot-reload automatically. print('{}') before sys.exit(0) prevents false error.
- Hook output channels (**Verified:** 2026-06-09 vs code.claude.com/docs/en/hooks): stdout reaches the model ONLY for UserPromptSubmit + SessionStart (exit 0). stderr reaches the model ONLY on exit 2 (any event). For model-visible advisory output on PreToolUse/PostToolUse, the ONLY channel is `hookSpecificOutput.additionalContext` JSON on stdout + exit 0 — stderr+exit-0 there goes to the debug log, not the model (dead channel for nudges). `systemMessage` → user only. Docs state all matching hooks run in parallel; same-matcher-block writer-first ordering is the empirically-derived project rule.
- Claude Code permission wildcards (*) CANNOT match across shell operators (&&, ||, ;, |). This is a security feature. `Bash(cd *)` does NOT match `cd /path && git show ...`. Solution: (1) CLAUDE.md rule banning compound cd commands, (2) PreToolUse hook (compound_cd_approver.py) auto-approves `cd <path> && ...` patterns, (3) Use `git -C <path>` or absolute paths instead.
- CRITICAL hook ordering: When adding a new PreToolUse hook, create the hook FILE first, then add the settings.json reference. Python returns exit code 2 for 'file not found', which Claude Code interprets as 'BLOCK this tool call' — effectively bricking ALL tool usage until the reference is removed.
- $CLAUDE_PROJECT_DIR available in hook subprocesses but does NOT expand in Bash tool on Windows. Hooks: use it directly. Bash: use relative paths.
- A battery run in an isolated checkout (a sparse or `git worktree` copy) inherits the session's `CLAUDE_PROJECT_DIR`, which still names the main checkout. About 50 hooks, tools and proofs root themselves on that variable, so the run silently exercises the main checkout's files. Run it as `CLAUDE_PROJECT_DIR=<that checkout> python3 .claude/scripts/harness_tests.py` from inside the checkout. **Verified 2026-09-15:** a sparse landing tree's battery passed 188/189. One proof failed there on a delete under the main checkout's path and passed 40/40 in the main checkout.
- pattern_enforcer.py rm-delete rule: only block genuinely recursive delete. BLOCK: rm -r / -R / -rf / -fr / -Rf / -f -r / --recursive / any flag cluster containing r or R. ALLOW: rm -f <file> (force = suppress confirm prompt on write-protected files, single-file delete — standard temp-file cleanup idiom), rm <file>, rm -i <file>, git rm -* (preserved via `(?<!git\s)` lookbehind). Regex `rm\s+.*-[rf]` is wrong: treats -f as equivalent to -r and produces chronic false positives (blocked `rm -f .tmp_commit_msg.txt` after `git commit -F`). Splitting into two commands defeats the hook's purpose. Correct patterns: `(?<!git\s)\brm\s+(?:[^\s]+\s+)*-[a-zA-Z]*[rR]` + `(?<!git\s)\brm\s+--recursive\b`. -f alone is safe (single-file), -r/-R/--recursive is the actual risk. Meta-gotcha when testing the hook itself: the hook scans the entire bash command string INCLUDING string-literal labels you pass to python3 -c, so 'rm -r/-R variant' as a test-case label retriggers the block — obfuscate the literal rm-flag pairs in test input.

**Related (from MCP graph relations):**

