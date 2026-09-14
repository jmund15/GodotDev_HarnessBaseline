---
description: Spot-check ai-worker digests against their sources and record anchored fidelity ratings.
disable-model-invocation: true
allowed-tools: Bash(python3 *worker_rate.py*:*), Read, Grep, Glob
---

Sample recent `mcp__ai-worker__*` calls, verify their claims against the actual files, and record a verdict with an anchor. This is the only source of ground-truth data on the worker; every other signal it produces is mechanical.

**What the mechanical signals already cover — do not re-derive them here.** `degenerate`, `truncated` and `error` detect a call that COLLAPSED; `paths_covered` vs `paths_requested` detects a coherent answer that silently discussed fewer files than it was handed. All are on the ledger row and need no judgment. This command exists for the one failure none of them can see: **a fluent, well-formed digest that is wrong about the code.**

## Arguments

- `/worker_audit` — sample 3 unrated calls that have artifacts.
- `/worker_audit N` — sample N.
- `/worker_audit <ts-prefix>` — audit one specific call.

## Procedure

1. **List candidates.**
   ```bash
   python3 .claude/tools/worker_rate.py unrated --since <date>
   ```
   Skip rows the tool reports as having no artifact — they predate capture and cannot be audited (`--with-artifact` filters them). Prefer calls whose output was actually acted on; a digest nobody used teaches you nothing about the risk you carry.

   **Sample toward the empty cells `report` names, not toward whatever traffic happened to occur.** `--breadth wide` finds the harder half. Coverage that climbs inside one already-measured cell buys nothing.

2. **Open the call.**
   ```bash
   python3 .claude/tools/worker_rate.py show <ts>
   ```
   Note the `git_sha`. **Verify against the sources at THAT commit, not at HEAD.** Files move; a digest that was faithful when written scores as invention once the code changes, which measures drift and reports it as fabrication. If HEAD has moved on the relevant paths, `git show <sha>:<path>` is the file the worker actually read.

3. **Pick 2–3 checkable claims per call.** A checkable claim names a type, member, file, value, or relationship — something a `Read` or `Grep` settles outright. Skip summary judgments ("the architecture is clean"); they are unfalsifiable and rating them is what produces impression data.

4. **Check each claim against the source.** Read the file. Do not accept the digest's own quotation of it — reproducing a wrong quote confidently is the failure mode under test.

5. **Rate, with an anchor and a derivation level.**
   ```bash
   python3 .claude/tools/worker_rate.py rate <outcome> --ts <ts> \
     --anchor "<claim checked> | <file:line that settles it>" \
     --derivation copyable|linked|derived
   ```
   | outcome | when |
   |---|---|
   | `faithful` | every claim you checked held |
   | `incomplete` | what it said was right, but it omitted material it was asked for |
   | `fabricated` | it asserted something the source does not support |
   | `unusable` | it collapsed — nothing was checkable (needs no anchor or derivation) |

   One rating per call, set by the worst outcome among the claims checked. A single fabrication outranks two faithful claims: the digest is unsafe to act on either way.

   | derivation | when |
   |---|---|
   | `copyable` | every fact was in the supplied files; the work was extraction or compression |
   | `linked` | facts were joined across files or against a spec, each join stated |
   | `derived` | inference, ordering or chaining; nothing in the inputs stated the answer |

   Enter `--derivation` only. `breadth` is computed from the ledger row — never type it.

   **Rate the question you asked, not the answer you got.** A `derived` question answered correctly is still `derived`; grading the coordinate by the reply makes every clean cell look easy in hindsight and the frontier stops moving.

6. **Report the run.** Calls audited, claims checked, outcomes, and every `fabricated` verbatim with its anchor. Then:
   ```bash
   python3 .claude/tools/worker_rate.py report --since <date>
   ```

## Reading the result

**Rated coverage is stated before any verdict, and unrated is UNKNOWN rather than clean.** A fabrication rate computed over a mostly-unrated set is a denominator artifact — the same error as reading a shrinking denominator as a quality gain (`harness-bounds-v1.2` FULL-BREAKDOWN, trap instrument).

**Thirty anchored ratings beat three hundred unanchored ones.** Resist widening the sample at the cost of actually opening files; an unverified `faithful` is worse than no rating, because it counts as coverage.

**Read the difficulty grid as a frontier, never as a score.** A cell is `faithful/rated`; `UNKNOWN` means never measured, which is not a pass. What routes work is the LINE between clean cells and unmeasured ones — "clean through wide×copyable, nothing known at `derived`" is a routing rule; an averaged number is not, and would hide the one cell that matters. Stratifying a small sample raises the UNKNOWN count on purpose: cells you have not measured stop hiding inside a pooled rate.

**A config change resets the baseline.** `ollama_num_ctx`, `max_completion_tokens`, the model alias and `OLLAMA_KV_CACHE_TYPE` all change what is being measured. Ratings taken across such a change describe two different workers.

`report` enforces this for the KV quant: it is part of the grouping key, so q4_0 and q8_0 rows never pool into one rate. Read the `kv` column as a coordinate of the measurement, not a footnote — comparing a q4_0 fabrication rate against a q8_0 one is a comparison of two instruments. The other three knobs are not yet on the row; state them yourself when reporting a run that spans a change.

**On a disagreement between declared and measured, the footprint wins.** `show` prints both: `kv=` is what `OLLAMA_KV_CACHE_TYPE` said at call time, `resident=` is the actual GPU footprint. The variable is read *only* when the Ollama server starts and cannot be read back from its API, so flipping it without restarting Ollama makes every subsequent row declare a quant that is not serving. Discriminator for Ridge: **~13.3 GB at ctx 65536 is q4_0**; q8_0 runs ~2.5 GB heavier and usually spills off the GPU. A row with `kv = -` predates runtime capture — its quant is unrecoverable, so it groups separately rather than being assumed.
