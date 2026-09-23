---
name: gotcha_native_workflow_agent_compaction
description: "A native Anthropic-model Workflow agent auto-compacts and finishes a read lane past its window; the no-boundary overflow seen on proxied Luna is the proxy adapter's case, not a Workflow limit. Observed 2026-09-14."
metadata: 
  node_type: memory
  type: project
  originSessionId: 3259384b-d4a7-47b7-8d52-9cded4b98af2
  modified: 2026-09-15T00:48:21.426Z
retire_when:
  - review-by: 2027-03-14
---

**A Workflow agent on a native Anthropic pin auto-compacts mid-lane and keeps working.** Observed 2026-09-14 (`wf_e7c86ac3-67f`, one run):
- One `claude-haiku-4-5-20251001` Explore agent at `low` effort read `scratch/harness-ad6ec4-native-compact/page-0..19.txt`, about 23 KB each.
- The transcript holds two `compact_boundary` rows, both `trigger: auto`, with preTokens 180,916 and 154,592.
- The agent returned all 20 markers matching `expected.json`. Its peak context after compaction was 141,429 tokens.

The earlier probe of the same harness on proxied Luna overflowed at 818,392 input tokens with no boundary (`verification.json`). That is the proxy adapter's detection gap for the 2.1.270 compaction-control shape. An adapter build that detects the shape compacts.

**Verified:** transcript scan `.claude/scratch/harness-finish-3a/wave/J6/scan_transcript.py`, output in `scan.txt`.

**How to apply:** keep compaction enabled on long native lanes. Before trusting a long proxied lane, check the proxy binary against `ccp_probe.py server-compaction`. Sample size: one model and one run. Do not generalize to other native models' thresholds.

