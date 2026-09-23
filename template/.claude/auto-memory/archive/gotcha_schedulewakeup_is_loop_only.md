---
name: gotcha-schedulewakeup-is-loop-only
description: "ScheduleWakeup only applies inside /loop dynamic mode — it errors outside one, and is never needed to wait on a background Workflow/task"
metadata: 
  node_type: memory
  retire_when: 
    - review-by: 2027-03-18
  type: reference
  originSessionId: dfe017b3-b84e-41bf-8517-359f6b15b971
  modified: 2026-09-18T01:01:27.021Z
---

`ScheduleWakeup` schedules the next `/loop` iteration; called outside an active `/loop` it just
errors (`prompt is required when stop is not true`). A background Workflow or task already delivers
its own `<task-notification>` when it completes — nothing needs to be scheduled to wait for it.

**Why:** called it to "wait" on an in-flight background fan-out in an ordinary (non-`/loop`) session.

**How to apply:** never reach for `ScheduleWakeup` to wait on a background task; the completion
notification arrives on its own. It only has a role inside an active `/loop`.
