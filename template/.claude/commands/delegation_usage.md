---
description: Report all-time delegated usage; /quota_dashboard shows balances
argument-hint: (none)
---

# Delegation Usage

Scope: every model/effort pin dispatched **out** of a session, across this project's whole history. It leaves out the parent sessions' own tokens. State this boundary first.

## Report

```bash
python3 .claude/tools/orchestration_metrics.py --all-time
```
One call, two tables: Workflow agents (swept from every session's `workflows/*.json` in this project) and sidecar runs (the global ledger, one table per `servedModel`). Currencies and cost bases stay separate per [`orchestration_metrics.md`](orchestration_metrics.md).
