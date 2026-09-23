---
name: workflow-args-generation-fidelity
description: "A large, deeply-nested, escape-dense Workflow `args` payload makes the MODEL emit subtly-malformed JSON; the runtime passes the unparseable raw string to the script, whose JSON.parse(args) throws — 4ms/0-agent/0-byte/no-journal death. Not a size cap, not a char class."
metadata: 
  node_type: memory
  type: reference
  originSessionId: c85a2c2a-bc96-40bf-b8ea-6d889e7de3a0
---

# Workflow `args` generation-fidelity failure

**Symptom.** A `Workflow` call dies almost instantly — `agentCount 0`, `durationMs ~4`, **0-byte output file, no `journal.jsonl` / run dir created** — with `SyntaxError: JSON Parse error: Unable to parse JSON string`. The throw is the script's own `const A = (typeof args === 'string') ? JSON.parse(args) : ...` guard (e.g. `review_fanout.js:11`). Reads as "the Workflow tool is broken"; it is not.

**Root cause.** `args` is always delivered to the script **as a string** that the script `JSON.parse`s. When the *tool call* carries a **large, deeply-nested, escape-dense** `args` — e.g. `agents: [N objects]`, each a long prompt studded with escaped `"`, `**`, `<>`, `|`, arrows — the **model's tool-call generation** emits subtly-malformed JSON (a dropped/mismatched delimiter, a stray unescaped quote, or a silently-truncated run). The runtime can't structure it, passes the raw text through as a string, and `JSON.parse` throws. It is a **generation** failure, surfaced at the script's parse — not a harness or tool defect.

**Ruled out by controlled probes (do not re-chase these):**
- **Size alone** — 14.2 KB of *flat distinct prose* delivered complete and parsed fine. No size cap in the failure's range.
- **Any character class** — `"`→`\"`, `\`→`\\`, `&`→`&amp;`, `<`/`>`→`&lt;`/`&gt;`, newlines, all serialize correctly and parse. (The harness HTML-escapes content but escapes JSON delimiters correctly.)
- The discriminator is **structural complexity × escape density × size**, not byte count. A repetitive run also exposes the infidelity directly: an intended ~24 KB `<>` payload silently emitted as ~5 KB (model under-produced, though that one stayed valid).

**Mitigations.**
1. **Keep `args` flat and modest.** Push shared bulk through a single `contextPrefix` *string*; keep each `agents[]` prompt short; or **chunk across multiple Workflow calls**. Avoid one call carrying many long nested prompts.
2. **For heavy multi-agent review over large pushed context, prefer parallel `Agent()` calls.** Each carries ONE flat prompt string (the low-infidelity shape), which is exactly why the parallel-Agent substitute succeeds where a single nested Workflow call fails. (`review_fanout`'s dedup is only worth the nesting risk for *code* reviews keyed on file:line; prose/design critique does not need it.)
3. **Triage a 4ms/0-agent/0-byte/no-journal Workflow death as "my args were malformed,"** not "tool broken." A pre-journal failure persists nothing, so the exact bytes are unrecoverable — reproduce with an instrumented no-agent probe script that reports `typeof args` + `JSON.parse` status rather than guessing.
4. **Inline-authored scripts MUST open with the tolerant-parse guard** — `const input = typeof args === 'string' ? JSON.parse(args) : args` — before touching any field. Direct `args.agents.map(...)` dies with `undefined is not an object` even on well-formed args, because delivery-as-string is the normal path, not the failure path. **Verified:** 2026-07-10 — same call failed bare, succeeded after adding the guard.

Cross-refs: [[gotcha_workflow_fanout_search_false_absence]].
