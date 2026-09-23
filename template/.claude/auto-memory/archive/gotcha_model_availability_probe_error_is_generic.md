---
name: gotcha_model_availability_probe_error_is_generic
description: "Probing whether a newly-announced model is on your plan: the backend's rejection is identical for an unknown slug and a real-but-unentitled model, so the error can never tell you which. The client-side model table is the discriminator."
metadata:
  node_type: memory
  type: project
  originSessionId: bee93ab6-839d-4f44-a335-11cfccc01bcf
  modified: 2026-09-05T00:11:37.593Z
---

> Cold-tier archive. Search-only — surfaced when checking whether a newly-released model is
> dispatchable, on any provider.

**The class.** A vendor announces a model; you try its plausible id and get a rejection. The
rejection reads as "this model does not exist", but on a plan-gated route it is emitted equally for
an id the backend has never heard of and for a real model your account cannot reach. Concluding
either one from that message is guessing with extra steps.

**Measured 2026-09-04, Codex/ChatGPT plan, GPT-6 Astra (announced 09-03).** Every candidate returned:

```
The 'X' model is not supported when using Codex with a ChatGPT account.
```

`gpt-5.3-codex` — an unquestionably real model — returned that byte-identical string, which is what
proves the message carries no existence information. Eight astra slugs (`gpt-6-astra`, `gpt-6`,
`gpt-6-codex`, `gpt-6-astra-codex`, `gpt-6-astra-preview`, `gpt-6.0-astra`, `gpt-5.7-astra`,
`astra`) were rejected; `gpt-5.6-sol` was accepted in the same sweep. `gpt-5.6-pro` is a third
state: in the client table, rejected by the backend — known-but-unentitled.

**The negative was FALSE, and rule 2 is why.** Re-probed the same day against an extracted
`0.153.4` binary: `gpt-6-astra` is in that table (`minimal_client_version: 0.153.0`) and the backend
ACCEPTED it, positive and negative controls both behaving. The 0.148.0 sweep could only ever have
returned a rejection. A stale client does not weaken an availability negative — it voids it.

**Two mechanical traps.** `codex exec` **exits 0 on a model rejection** — branch on the stdout
message, never `$?`. And the CLI prints `Model metadata for X not found` for any id outside its own
table, which is client-side and says nothing about the server.

**How to apply.**

1. Never run the probe without **both** controls in the same sweep — a known-good id that must be
   ACCEPTED and a bogus id that must be REJECTED. One arm alone cannot distinguish a working probe
   from one that rejects everything.
2. The discriminator is the **client-side model table** compiled into the CLI binary (for codex:
   the npm package's platform vendor `bin/`). Scan for the vendor's id shape. A model absent there
   is undispatchable regardless of entitlement, because the client has no slug to send — so an
   out-of-date CLI makes every availability negative uninformative. Check the installed-vs-latest
   version before recording any negative.
3. Read binary string hits **in context**. Scanning for `astra` returned four hits, all false:
   three timezone entries (`Europe/Astrakhan`) and one MIME type (`astraea-software`).

Related: [[gotcha_self_reported_model_identity_is_not_authority]] (a served-model field behind a
translating proxy echoes your own pin).
