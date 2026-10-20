---
description: Analyze Godot logs for health, errors or runtime behavior
disable-model-invocation: false
---

Analyze Godot log files with mode-based presets and token-efficient JSON output.

Runs `python3 .claude/hooks/analyze_godot_logs.py`. `--help` is the one home for every mode, `--log` value, filter and token flag.

## Quick Reference

| User asks | Invoke | Typical JSON size |
|---|---|---:|
| "Health check" / no specifics | `--json --mode summary` (default) | ~200 tok |
| "Just the errors" | `--json --mode errors` | <500 tok |
| "What just happened?" | `--json --mode tail --last 20` | ~3K tok |
| "[Tag] frequency" | `--json --mode tags` | ~150 tok |
| "Everything ExampleAgent did" | `--json --mode entity --node ExampleAgent` | varies |
| "Targeted timeline" | `--json --target HSM --target Transition` | varies |
| "Previous game session" | add `--log previous` to any | varies |
| "Check the test run" | add `--log test` to any (reads the newest `godot_test.log`) | varies |

**Always use `--json` for agent consumption.**

---

## Natural-Language Inference

Users rarely pass flags. Infer the invocation, state what you inferred in one line, and ask one user-language question — "is this about a crash, recent activity, or a specific system?" — only when the ask is ambiguous and a wrong default would mislead. Never ask which mode they want; modes are an implementation detail. Assume the recent topic and preserve the prior filter.

### Inference decision flow

Apply every step that matches, in order; the `--mode summary` steps apply only when no earlier step did:

1. **Crash, freeze or quit mentioned?** → add `--log previous` (see the log-selection trap).
2. **Specific system, entity or domain named?** → map it with the Domain Mapping table.
3. **Temporal or recency cue?** → `--mode tail --last 20`.
4. **Error or break framing?** → `--mode errors`, which returns the first errors in the log; when step 3 matched, keep `--mode tail --last 20` and add `--level error,exception` for the most recent errors.
5. **Generic "check the logs"?** → `--mode summary`.
6. **Bare invocation with no signal?** → `--mode summary`.

Start with `--mode summary`; drill in when the summary shows a repeated or high-severity group the request bears on. Go straight to a targeted `--mode timeline` only when the user named a target — that timeline is then the cheapest useful answer.

---

## Log file selection

`--log` takes `latest` (omitted), `test`, `previous`, an integer, or an explicit path; `--help` carries their meanings, and `rules/godot_files.md` is the canonical home for the log paths.

**Why this matters:** After a crash and quit the active log is empty — use `--log previous`. A test run overwrites one file, so `--log test` reads the newest `godot_test.log`.

---

## Domain Mapping

Use these rows when the user's word is not the tag or node name.

| User says | Script flags |
|---|---|
| "example agent" / "example agent entity" | `--node ExampleAgent` |

When the user's topic is not here, pass their literal terms to `--target`; the script searches every block field (class name, node path, owner, message text, source file, method, tags, backtraces).

---

## Report contract

Report the severity counts and total blocks parsed, cite a line number for every claim, say plainly when there are no errors, and close with a concrete next drill-in or a diagnosis.
