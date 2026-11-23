# Q12 move set: the `ai-worker` audit tools

Q12 O1 moves these to `~/.config/ai-worker`, beside the server they measure, as user-level tools. Both repos drop them. The shared repo cannot write outside itself, so A10 copied them here for the owner to place. B16 deletes the tool copies under `template/.claude/tools/`.

| File | Needs |
|---|---|
| `commands/worker_audit.md` | `tools/worker_rate.py`, which imports the repo's `cli_usage.py` and `hooks/_hook_state.py`. `tools/ai_worker_metrics.py` is the standalone ledger report the plan lists beside it |
| `commands/aiworker_write_doc_audit.md` | nothing beyond the server; names `/eval_dashboard`, which v2 drops |
| `commands/doc_workflow_battery.md` | `tools/battery_children.py` and `tools/extract_subagent_tools.py` |

Every path in these files is written relative to a repo's `.claude/`. After the move, each one needs rewriting to the new home.
