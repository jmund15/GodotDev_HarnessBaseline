# Positive controls and sub-floor rungs — the mechanics behind SKILL.md §5

Detail layer for `benchmark_design` §5: the control cell an all-fail tier needs before it can be read, and why a rung prepended beneath a floor is an ordering claim.

### A tier where NOTHING passes needs a positive control before it can be read

Zero passes across a whole tier cannot distinguish *the tier lacks the capability* from *the instrument
does not reach this tier*, and extra rungs never separate them. Prompt size, a strict output contract
and an unusual material format each fail before any reasoning is attempted, and all read as incapacity.
The fix is a cell the arm MUST pass if delivery works:

- **Reuse the parent's corpus byte-for-byte and swap only the task header.** Material, size, format and
  output contract stay identical; only the reasoning demand is removed. That is what makes the result
  attributable.
- **A control is not a rung and must not be gate-legal for the parent axis.** It asserts a different
  proposition — *the material arrived and the contract was honoured* — so the objection that sinks a
  too-shallow rung is irrelevant: a shortcut landing on the answer IS a pass by the control's standard.
  Give it its own instrument name and never pool it with the ladder it guards.
- **Grade it in stages, not as a bit**: never-emitted-the-shape, emitted-and-wrong, and correct are
  three different diagnoses (§9).
- **A control with a guessable answer measures nothing.** Reject a scalar answer in the range a blind
  guess lands in.
- **A control is an EXISTENCE claim, so it is scored asymmetrically and never at one seed.** The ladder
  asks *how far can this arm go*; the control asks *can this arm receive the instrument at all*. One
  pass refutes "cannot deliver"; N failures at ONE seed prove nothing. Run several seeds, pass if ANY
  passes, report the rate alongside — an arm that delivers once in eight is deliverable but not
  deployable.

### A SUB-FLOOR rung is a claim about ordering, and it must be checked per arm

Adding a rung beneath an existing floor asserts it is easier than everything above — as unreliable as
any other ladder-ordering claim (§2 *A monotone SCALAR*), and invisible when it fails: a chain-bound
walker hits the new bottom rung, breaks, and reports NONE, reading as *cleared nothing* rather than
*the chain has a hole at the bottom*. Two consequences:

- **Prepending a rung retroactively invalidates the bound of every arm collected before it existed** —
  run the new rung for those arms or name their gap.
- **Never report a bound across a gap** — skip the unasked rung and name it, never break the chain on it.
