---
disable-model-invocation: true
---

# `/codify` — independent derivation

Read at `/codify` §Independent derivation when the owner asked for the codification. Entrypoint: [`../codify.md`](../codify.md).

1. **Brief, facts only.** Write `.claude/scratch/codify_<sid8>_<n>/brief.md`, with `<n>` one past the highest existing folder for the session because an earlier pass's folder holds its evidence. The brief holds the owner's words verbatim, the session id and the transcript span (`session_digest.py --select` reads it), the evidence paths, and this command's path. It states no cause, fix or judgment of yours.
2. **Seal your own.** Run Steps 0–5 yourself and write the result to `.claude/scratch/codify_<sid8>_<n>/own.md` beside the brief before the delegate returns; never edit it after.
3. **Dispatch** one read-only delegate at the architect tier's scoped-architecting cell (`reference/model_ladder_evidence.md`) to run Steps 0–5 from the brief and return its Report block without landing anything.
4. **Weigh.** Compare the two Reports on the evidence each cites. Where both name the same cause and fix, land it. Where one cites evidence the other missed (a history row, a measurement, a side effect), take that one or merge them, and say which. Where both are supported and they still differ, put the fork to the owner as a question packet. The report names the source of each landed element.
