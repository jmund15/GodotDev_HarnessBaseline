---
name: gotcha_prove_a_guard_by_making_it_fire
description: "A filter/denial/exclusion that is working and one that was never in the path emit the same evidence — nothing; verify by forcing a violation and watching it be caught, never by observing an absence"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 57e6ed14-e265-4415-94ec-b1cdebc1bfe4
  modified: 2026-08-21T16:27:09.349Z
---

# Prove a guard by making it FIRE, never by observing nothing

**A guard that works and a guard that was never in the path produce identical evidence: silence.**
So "I ran it and saw no violations" distinguishes nothing. The only evidence that separates the two
is a **violation that got caught** — a positive control.

**Why:** absence-of-signal has two causes and you cannot tell them apart from the signal. The
failure is worse than a missed check, because a clean report actively certifies the broken state,
and everything downstream inherits that certification.

**How to apply:** before trusting any allowlist, denylist, `--exclude`, matcher, filter, scope
claim, or verification script — feed it a case it MUST reject and confirm the rejection appears.
Where the guard records its own actions, prefer *counting denials that fired* over *counting
violations that did not occur*. When no positive control is possible, say the check is unverified
rather than clean.

## The six shapes it took in one session (2026-08-20)

1. **An assumed flag scope, never tested.** A whole benchmark battery was dispatched believing a
   `-t` tool allowlist excluded `mcp__ai-worker__*`. **It does not — `-t` does not gate MCP tools,
   only `-x` denies.** Every cell silently reached a second model all night; the dispatch looked
   correct and the arms looked clean. A single forced call would have exposed it in seconds.

2. **A checker that reported clean because it never looked.** The probe was handed a path pattern
   that matched zero files and printed nothing — read as "no violations". **A verifier must treat a
   zero-match input as FAILURE, never as success**, and say so in its output.

3. **A verdict that overclaimed which checks ran.** A two-source verifier's second source silently
   no-op'd on a wrong field name, while the summary line still printed "both sources agree".
   **A summary may only claim the checks that actually executed** — count them and report the
   count, including how many targets went unverified.

4. **A guard verified against a path the failure never takes.** A mid-stream degeneracy abort was
   authored, unit-tested with a synthetic *streaming* replay, measured at a 23× cut, and reported as
   verified — while every one of the eleven real degenerations happened in `read_files`, which is
   **buffered, not streamed**. Registered, passing, and unreachable by the bug. **Testing a path
   that RESEMBLES the failing one is the most convincing way to verify nothing**, because it
   produces a real green result. The missing question is *"which call sites actually reach this
   code?"* — a replay never asks it, and neither does a passing test.

5. **An instrument that over-attributes gets ignored, which costs more than it bought.** The
   two-source verifier built in response to item 1 matched ledger rows to an arm **by time window**,
   but the ledger is machine-wide: a concurrent session's `write_doc` landed inside a cell's window
   and reported it contaminated while its arm side was 0 calls. A checker that cries wolf trains you
   to skim its output — and the real signal sits on the line beside it. Attribute by an identity the
   subject actually owns (here `cwd`), never by co-occurrence. Same session, a sibling checker
   flagged a benign frame property as a leak for the same reason.

6. **A multi-leg check where ONE leg was proven and the rest inherited the credit.** The engagement
   gate has three legs: sibling-root substitution, zero-reference, and required-input-unread. The
   sibling leg was demonstrated firing — it caught two cells in a retroactive sweep — and that was
   taken as "the gate works". The required-input leg **could never fire**: it matched the filename
   and the engine's not-found string within `[^\n]{0,400}`, the same LINE, while agent transcripts
   are JSONL where a tool call and its result are separate lines. So it returned VALID *and
   affirmatively reported the input as read* for the exact original failure it was written for.
   Found only by feeding it a synthetic transcript in the real shape and asserting VOID.
   **Each leg of a check is its own guard and needs its own positive control** — a per-leg proof,
   not a per-function one. And when a check greps a RECORD-structured log (JSONL, ndjson, any
   one-object-per-line format), a same-line window is the default that silently matches nothing:
   the cause and its effect are in different records by construction.

## The generalization

**An exclusion check inherits the scope of the noun it names.** A battery that asserted forbidden
commits were unreachable *in the superproject* never asked the *submodule*, so arms that could
reach content three weeks newer than their sealed frame reported `BATTERY OK`. A nested store is a
second noun and needs its own assertion; it will never be covered by the first.

**Corroboration requires independence.** Two instruments that share an assumption agree loudly and
prove nothing. Validate a new verifier against a **known-bad** case before trusting it on unknowns —
when the two sources genuinely differ, they disagree in detail (here, the independent ledger found
one call the arm-side stream had missed), and that disagreement is what demonstrates independence.

The same epistemics apply to storage: a recorded identifier proves nothing about retrievability.
[[feedback_excluded_and_broken_look_identical]] is the sibling one step downstream: this file is
about a guard never verified to fire, that one about a check whose *deliberate* exclusion is
indistinguishable from a break. Same silence, different origin.
