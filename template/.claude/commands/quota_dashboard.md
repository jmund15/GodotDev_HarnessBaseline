---
description: Show live quota, balances, models and tier routes
argument-hint: "[--cache-only]"
---

# /quota_dashboard

Run `python3 .claude/tools/quota_dashboard.py $ARGUMENTS` and paste its full stdout into your reply, verbatim: the owner does not see tool output. Any other argument than `--cache-only` prints the tool's usage; show that and stop.

The tool reads every value from its owner on each run and stores nothing, so a number that looks wrong is fixed at its owner: the capacity reading (`tools/provider_capacity.py`), the registry (`reference/external_models.json`) or the plan facts (`tools/provider_bands.py`). After the table, add one line only when a plan is in the Surplus band or a band is `Exhausted`: name the plan and what `orchestration` §5b says to do about it.

Dispatch spend by session is `/delegation_usage`, not this command.
