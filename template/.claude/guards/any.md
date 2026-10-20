# Guard: any — applies to EVERY delegate

Read ONLY the section your dispatch names; if none is named, read `## detailed`. This file binds every delegate and arrives alongside your shape file. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## detailed

- Read bounded spans. For a must-read subject, continue through spans that together cover every required line; for a focused question, locate its anchor before reading the needed span. Search build/test logs for error and `Failed` markers; extract transcript `.jsonl` evidence deterministically. A catalog with an accessor is read through it, never whole: `kfm.py` for known-failure entries, `lens.py` for agent mandates, grep for `generated/abstraction_families.md`. Copyable bulk extraction across inputs → `mcp__ai-worker__read_files`; focused evidence and derived judgment stay direct reads, whatever the file count. Every tool result stays in your window until compaction, and repeated instant refills end the run. [CLAUDE.core.md §9; reference/sidecar_runs.md §Compaction specifics]
- ONLY when your dispatch states you are running concurrently with other agents: do NOT run a single-flight tool; the bar never covers `.claude/tests/` proofs, which you run. Another session's run, in this checkout or another, never makes a dispatch concurrent: a busy slot is contention the machine lock waits out. A solo delegate carries no concurrency bar — the engines say so explicitly when it applies, and silence means it does not; who runs a wide test run is a separate rule (`guards/author.md`). [guards/single_flight.json]
- Read-only when your brief marks you so: do not modify, create, or delete any file except a spill file your brief names for you. [dispatch.js readOnlyGuard]
- Return what the brief names: the artifact at the named path plus the digest it asks for; without a named path the final message is the deliverable. [orchestration §4 return contract]
- These rails bind alongside your brief. Where a rail and the brief conflict, report the conflict in your deliverable rather than silently picking one — the orchestrator owns that call, and a silently-resolved conflict is invisible to it. [orchestration §4]
- Quote source material VERBATIM. Your own inference belongs in a separately labeled section — never edited into quoted content, never blended into a passage you are passing along. [orchestration §4]
- Distinguish what you verified from what you inferred, and say WHICH — every claim about this repo carries its rung: observed (ran it) · read (read the deciding line) · traced (read the ends, inferred the middle) · pattern (a sibling does it) · matched (a search returned the name; you did not open the hits). Rungs 3–5 say so in the claim itself. Name the one fact the claim is safe because of; if that fact is "it looked right", it is rung 4. Code is not evidence for its own intent — `because` / `was designed to` / `ensures` assert history the file does not carry. **The rung grades your evidence, never your tool** — it does not license reaching for a search your routing rules send elsewhere. [reference/claim_confidence.md]
- Concision is per sentence, never per item: cut filler, hedge stacks and preamble; never cut a finding, a section, or a caveat to be shorter. Write complete grammatical sentences — note-form and dropped articles are not concision, they move the decoding cost to the reader. Your deliverable is read in full by an orchestrator whose context your padding occupies permanently. [output-styles/terse.md §SCOPE]
- Close by naming what you could NOT satisfy. An unmet constraint reported is cheap; one hidden is not. [orchestration §4]
- Cleanup: apply one only when it is in-file, behavior-preserving, inside your brief's write set and changes no contract; return every other one in an `offers` block (`orchestration` reference `writer_returns.md`).
- Never edit `allowed-tools`, `settings*.json` or a hook to unblock yourself, even when the change looks germane to your task. Report the needed grant under couldNotSatisfy and stop; a denial ends that attempt. [CLAUDE.core.md §Shell Discipline]
- A causal claim about a configured value names the whole chain — where it is declared, what overrides it, where it is injected at runtime, and which consumer reads it — or it is labeled unmeasured. [reference/claim_confidence.md §Configured values]
- No completion notification reaches a Workflow agent or a `claude -p` child, so a backgrounded job never wakes those callers. An identified Agent-tool child receives completion notifications and waits with `run_in_background` (`CLAUDE.core.md` §Shell Discipline), not this foreground loop. Keep working while a job runs, then wait for it in foreground calls, `until <done-test>; do sleep 30; done`, each running at most 270000 ms, because a delegate's prompt cache lives 5 minutes and a longer call's return rewrites your whole context; repeat only while the job runs. Never re-run a status check (`tail`, `wc`, `ls`, `date`) call after call: each run is a full turn that re-reads your whole context. [hooks/foreground_wait_guard.py]

## condensed

- Bounded reads cover the focused span or, together, every required line of a must-read subject. Search logs for error markers. Extract transcript evidence deterministically. Read catalogs with an accessor (`kfm.py`, `lens.py`, grep for the family manifest) through it. Copyable bulk extraction → `mcp__ai-worker__read_files`, focused or derived reads stay direct (CLAUDE.core.md §9).
- Single-flight concurrency (only when your dispatch declares concurrency; it bars single-flight tools, never `.claude/tests/` proofs), read-only-if-marked, and the return contract (named artifact plus its digest, else the final message): `guards/single_flight.json` for the concurrency bar, `dispatch.js` and `orchestration` §4 for the rest.
- A rail that conflicts with your brief is reported, never silently resolved: orchestration §4.
- Verbatim quoting; inference kept in its own labeled section: orchestration §4.
- Verified vs inferred stated explicitly, at the rung — observed / read / traced / pattern / matched, with rungs 3–5 labeled in the claim, and no intent asserted from code alone. The rung grades evidence, not tool choice: `reference/claim_confidence.md`.
- Report what you could not satisfy: orchestration §4.
- Cleanup: apply one only when it is in-file, behavior-preserving, inside your brief's write set and changes no contract; return every other one in an `offers` block (`orchestration` reference `writer_returns.md`).
- Never edit `allowed-tools`, `settings*.json` or a hook to unblock yourself; report the grant under couldNotSatisfy and stop: orchestration §4.
- Configured-value claims name the chain: declared → overridden → injected → consumed, or are labeled unmeasured.
- Complete grammatical sentences, never note-form: cut filler per sentence, never a finding or a caveat.
- A Workflow or `claude -p` child waits for a job in foreground `until <done-test>; do sleep 30; done` calls that each run at most 270000 ms, because a delegate's prompt cache lives 5 minutes and no wake reaches either; never re-run a status check call after call: hooks/foreground_wait_guard.py.

## minimal

- Report a rail/brief conflict; never resolve it silently.
- Quote source verbatim; keep inference in its own labeled section.
- State each claim's rung: observed / read / traced / pattern / matched.
- Complete grammatical sentences, never note-form.
- Close by naming what you could not satisfy.
- Cleanup: apply one only when it is in-file, behavior-preserving, inside your brief's write set and changes no contract; return every other one in an `offers` block (`orchestration` reference `writer_returns.md`).
- Never edit `allowed-tools`, settings or a hook to unblock yourself; report the grant and stop.
- Name the value's chain: declared, overridden, injected, consumed; else unmeasured.
- Read a catalog with an accessor (`kfm.py`, `lens.py`, grep for the family manifest) through it, never whole.
- A Workflow or `claude -p` child waits for a job in foreground until-loop calls that each run at most 270000 ms, because a delegate's prompt cache lives 5 minutes; never re-run a status check call after call.
