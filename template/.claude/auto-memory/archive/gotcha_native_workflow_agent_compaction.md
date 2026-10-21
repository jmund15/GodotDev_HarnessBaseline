---
name: gotcha_native_workflow_agent_compaction
description: "A native Anthropic-model Workflow agent auto-compacts and finishes a read lane past its window; the no-boundary overflow seen on a proxied model is the proxy adapter's case, not a Workflow limit."
metadata: 
  node_type: memory
  type: project
retire_when:
  - review-by: 2027-03-14
---

**A Workflow agent on a native Anthropic pin auto-compacts mid-lane and keeps working.** **Concrete:** observed 2026-09-14 (`wf_e7c86ac3-67f`, one run):
- One `claude-haiku-4-5-20251001` Explore agent at `low` effort read `scratch/harness-ad6ec4-native-compact/page-0..19.txt`, about 23 KB each.
- The transcript holds two `compact_boundary` rows, both `trigger: auto`, with preTokens 180,916 and 154,592.
- The agent returned all 20 markers matching `expected.json`. Its peak context after compaction was 141,429 tokens.

The earlier probe of the same harness on proxied Luna overflowed at 818,392 input tokens with no boundary (`verification.json`). That is the proxy adapter's detection gap for the 2.1.270 compaction-control shape. An adapter build that detects the shape compacts.

**Verified:** transcript scan `.claude/scratch/harness-finish-3a/wave/J6/scan_transcript.py`, output in `scan.txt`.

**A `modelSettings.<model>.autoCompactWindow` passed as `--settings` JSON reaches a native Workflow agent; the settings-file route showed no effect.** The docs name no agent kind, so it was measured 2026-10-14 (session 69255593, `wf_ce756f73-dc9`, one run): a `claude -p` session given `--settings '{"modelSettings":{"claude-haiku-5-5":{"autoCompactWindow":100000}}}'` ran one Workflow Haiku 5.5 agent at `low` effort reading six 60-155 KB files. It compacted three times at 80,635, 66,107 and 91,443 preTokens (peak 77,177), then failed with "Autocompact is thrashing". Haiku seats before the setting existed ran to 218k-450k uncompacted. A window near one large read thrashes. **The settings-file route does not show the same effect.** Session 4a5da987 started 2026-10-12T22:26:03Z. That was 3 minutes after session 69255593 wrote `autoCompactWindow: 250000` into `~/.claude/settings.json` (Edit at 22:23:20Z). On 2026-10-14 that session ran six Workflow Haiku 5.5 seats (`wf_1e2e8868-738`, `wf_e028f461-430`, P17 pin A/B run 3). They peaked at 222,891 / 277,112 / 262,580 / 387,032 / 412,435 / 325,161 tokens with `compactEvents=0`. The setting is proven only for a `--settings` launch of a `claude -p` session. Through the settings file, in an interactive session, it showed no effect on Workflow agents. The cause is unmeasured: settings read at launch versus the flag, or a window cap. Until a run separates them, do not rely on the file setting to compact Workflow Haiku (the registry's `_compactAtComment` does). Scan: `.claude/scratch/pin_ab/compact_scan.py <wfdir>`.

**PreCompact and SessionStart(compact) hooks fire for a Workflow agent's compaction, and both reach it.** Measured 2026-10-14 (A2 probe, session 953e9207, `wf_3aabb85b-ad7`, one run, `.claude/scratch/a2_probe/`). A `claude -p --settings` launch registered a probe hook on both events and ran one Haiku 5.5 agent at `low` effort over 14 files of 30-37 KB, at a 130000 window.
- It compacted 4 times, at 82k-105k preTokens.
- The hook fired 4 times on each event. No payload carried `agent_id`: `session_id` and `transcript_path` were the parent's, so a hook tells a subagent apart only by `_hook_state.is_subagent_payload`'s heuristic.
- All 4 summaries began with the marker the PreCompact stdout asked for and kept the results list.
- The SessionStart output reached the agent as a `hook_success` attachment.
- The agent re-read no earlier file and finished all 14 lines from its summaries. 17 of its 18 requests stayed under 100k.

This was a lane of independent items. A results rail does not stop a review seat from redoing its checks: measured 2026-10-14 (A2b, `.claude/scratch/a2_probe/`, ledger `Claude/Meta/Benchmark/ladder-ingest/plan-review-pin-ab-ledger.md` row A2b): at 250k the run cost 65% more than uncompacted and the memory seat re-read the plan 19 times; at 130k the client's thrash detector ended 2 of 3 seats.

**The in-session route still does not compact a Workflow Haiku agent, and the cause is not in the harness's reach.** Measured 2026-10-14 (session 9c1a9920): with the user settings file holding both `modelSettings.claude-haiku-5-5.autoCompactWindow: 250000` and a top-level `autoCompactWindow: 600000`, an in-session Workflow Haiku agent (`wf_178f9fb3-c13`, 28 reads) ran to 426k with no compaction, while a `claude -p --settings` agent given the same two keys (`wf_a3e49203-86f`) compacted at 416k preTokens. So the top-level key does not block the per-model one, and the file was present at this session's launch. What differs is how an interactive session resolves per-model settings for its subagents, which only the client can show. Treat in-session Workflow Haiku as uncompacted (the registry note says so); a lane that needs compaction runs on the `--settings` route.

**Compaction cannot pre-empt one turn.** The `-p` probe agent issued all 28 Reads as parallel tool calls in one turn; its context went 26k → 409k in a single request and compaction fired only after it. A lane whose window matters is briefed to read one file per turn.

**How to apply:** keep compaction enabled on long native lanes. Before trusting a long proxied lane, check the proxy binary against `ccp_probe.py server-compaction`. Sample size: one model and one run. Do not generalize to other native models' thresholds.

