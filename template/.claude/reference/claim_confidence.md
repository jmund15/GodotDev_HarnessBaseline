# Claim Confidence — inference distance on claims about our own code

Doctrine for any claim about behavior **inside this repo**. The sibling axis is `source_trust.md`, which
grades *source authority* on claims about behavior outside it — engine, library, spec. Different axis:
that one asks *who says so*, this one asks *how far you are from having seen it*.

Loaded by reference, not by path. `guards/any.md` and `guards/review.md` cite it; neither carries a copy.

## The ladder

Every claim sits on one rung. State the rung's phrasing, not a stronger one.

| Rung | You have | Phrase it as |
|---|---|---|
| 1 — observed | Run it, read the output, watched the behavior | "X does Y" — assert it, cite the command and its output |
| 2 — read | Read the code that decides it, start to finish, including the branch that fires | "X does Y (`file:line`)" — assert it, cite the line |
| 3 — traced | Read the entry point and the exit, inferred the middle | "X appears to do Y — traced `a:1` → `c:9`, did not read the path between" |
| 4 — pattern | A sibling does Y and this looks like the sibling | "X likely does Y, by analogy to `Sibling:line` — unverified here" |
| 5 — matched | A search returned the name; you did not open the hits | "a search shows N references to X; I have not established what any of them do" |

**The framing question:** *name the one fact this claim is safe because of.* Can't name it, or the fact is
"it looked right" → the claim is rung 4 or 5 and must say so.

**The rung grades evidence, not tool choice.** Which search is correct for a given file type is CLAUDE.md §9's
call and is unchanged by this ladder — rung 5 is where you land when you stop at the match, whatever produced
it. A ladder read as "grep is the baseline" would invert the routing rules it sits beside.

## Carry-confidence words

These assert more than the rungs below 2 can support. Each one smuggles in intent, causation, or history
that the code does not carry:

`because` · `was designed to` · `is intended to` · `fixes` · `ensures` · `guarantees` · `always` · `never` ·
`the reason is`

**Code is not evidence for its own intent.** A file shows what it does, never why someone wrote it that way.
Intent comes from a design doc, a commit message, or the person — `git log -S` is the cheap check, and it
also separates *never present* from *removed on purpose*. Absent one of those, write what the code does and
stop: "the guard rejects null" not "the guard rejects null to prevent the crash in X."

## Where this binds

- **Delegates** — `guards/any.md` (verified vs inferred) and `guards/review.md` (every finding names a
  `file:line` you actually read) fold this ladder into their rails at both model tiers.
- **Orchestrators** — a rung-4 or rung-5 claim from a delegate is not promoted by consolidation. Re-verify
  it first-party or report it at the rung it arrived on.
- **Plans and findings** — a load-bearing claim below rung 2 states its rung inline, so a reader can see
  what to check before building on it.
