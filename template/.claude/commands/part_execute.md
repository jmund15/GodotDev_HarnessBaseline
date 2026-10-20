---
allowed-tools: Bash(dotnet:*), Bash(python3 .claude/tools/task_record.py:*), Glob, Grep, Read, Edit, Write, TaskCreate, TaskUpdate, SlashCommand
description: Autonomously execute an approved, converged plan as a TDD slice loop; halts on its stop valves
---

# /part_execute — Autonomous Plan-Execution Loop

**Input:** an approved, converged plan path: `/part_execute <plan-file-path>`, adopted through [`/plan_handoff`](plan_handoff.md), with enough usable context to verify the plan and diff.
**Enters at:** execute.
**Owner stop:** none, because approval was the gate.

Run the stages, halts, slices and close in [`drive_spine.md`](../skills/_brainstorm_shared/drive_spine.md). This file holds only what executing an approved plan adds.

Start a fresh session when it cuts context cost or isolates an approved handoff; an inline through-ship drive may keep its planning session and delegate the converged work. For a Part too large for one context, wrap the invocation in `/loop`; the loop body is still this command.

## Handoff

Run `/plan_handoff <plan-file-path>` and adopt its execution stance for the rest of the run. A load-bearing empirical claim that fails that stance's verification rule is spine H1. If the plan path is missing or unreadable, abort exactly as `plan_handoff` specifies and stop; that abort fires before the loop and is not a halt.

## Slice board

Read the plan's ordered slices and preserve their dependencies and readiness conditions. The task record's slice rows (`task_record.py slice`) are the execution state (`orchestration` reference `checkpointing.md`). Mirror them with `TaskCreate`/`TaskUpdate` only when the client needs that board for live, user-visible progress.

**Resume and reconcile.** On a `/loop` re-invocation, reconcile against the record's slice rows instead of re-creating the checklist: skip `completed` slices and resume the `in_progress` one. Update the board when one is in use. The loop ends when every slice is `completed` and the verification and review checks required for its chain position are green, or when a spine halt fires. On a halt, record the reason on the active slice and mirror it to the board when one is in use.
