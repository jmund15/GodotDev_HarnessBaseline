---
disable-model-invocation: true
---

# `/codify` — independent derivation

Read at `/codify` §Independent derivation for an owner's correction. Entrypoint: [`../codify.md`](../codify.md).

Two derivations meet because the session that made the mistake diagnoses it under the same momentum that produced it.

1. **Pointers and an evidence packet, no brief.** The session that made the mistake picks the facts that fit its own diagnosis, so the delegate gets none from it. Run `python3 .claude/tools/codify_prompt.py --session <id> --at "<YYYY-MM-DD HH:MM UTC>" --closing "<the owner's closing words>" [--part-opens "<the words that open one part of a multi-part message>"] [--base <commit before any fix>] [--turns <n>] [--out-suffix -glm]`. It writes the evidence and prompt pair (`.claude/scratch/codify_<sid8>_<n>/evidence.md` and `.claude/scratch/codify_<sid8>_<n>/prompt.md`; `<n>` one past the highest existing folder; `--out-suffix` joins the latest folder and suffixes both files), and refuses words that are not in that owner message. The script extracts the packet from the transcript: every owner message up to the correction, verbatim, then the session's replies and tool calls in the turns before the correction's last three owner messages (`--turns` widens the window; the packet names it and any rows it left out), clipped. The prompt is a fixed template of pointers that names the packet and its digest. Write no prompt or packet by hand and add nothing to either: the `dispatch_size.py` preflight flags a `codify_*/prompt*.md` that differs from its render or whose packet changed. A derivation that read a non-neutral prompt or packet is void and relaunches from a fresh render; it is never amended.
2. **Dispatch** one read-only delegate — `kind: reviewer` with `tools: "+Bash"`, since Steps 0–5 run `session_digest.py` and `verify_claims.py` — at the architect tier's scoped-architecting cell (`reference/model_ladder_evidence.md`) to run Steps 0–5 from the prompt and its packet and return its Report block without landing anything. The job writes `tier: architect`, which its read-only shape does not imply.
3. **Seal your own while it runs.** Run Steps 0–5 yourself from the same packet and your own context, and write the result to `.claude/scratch/codify_<sid8>_<n>/own.md` before the delegate returns; never edit it after.
4. **Weigh.** Compare the two Reports on the evidence each cites. Where both name the same cause and fix, land it. Where one cites evidence the other missed (a history row, a measurement, a side effect), take that one or merge them, and say which. Where both are supported and they still differ, put the fork to the owner as a question packet. The report names the source of each landed element.
