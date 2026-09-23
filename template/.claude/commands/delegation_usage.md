---
description: Report all delegated-dispatch usage — Workflow + sidecar, every session, never the global account usage
argument-hint: (none)
---

# Delegation Usage

Scope: every model/effort pin dispatched **out** of a session, across this project's whole history. This is not the account's global usage page — no local source covers native interactive-session tokens across every session. State this boundary first. See `auto-memory/feedback_delegation_log_is_not_global_usage.md`.

## Report

```bash
python3 .claude/tools/orchestration_metrics.py --all-time
```
One call, two tables, never blended: Workflow agents (swept from every session's `workflows/*.json` in this project) and sidecar runs (the global ledger, one table per `servedModel`). Sidecar cost is real USD; Workflow cost is normalized/plan-quota — different currencies, never summed into one number.
