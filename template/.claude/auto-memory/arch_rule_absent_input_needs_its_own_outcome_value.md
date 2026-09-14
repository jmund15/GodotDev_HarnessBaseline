---
name: arch-rule-absent-input-needs-its-own-outcome-value
description: A reducer over rows destroys the never-measured/failed distinction for free — carry a third outcome value in the type and assert the buckets sum to the source row count
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-19T04:49:15.556Z
---

Any code that collapses a set of rows to a scalar — a pass/fail walker, a percentage, a re-derive
loop, a status roll-up, a console summary — receives rows in three states and has only two values
to put them in. **Absent, unparseable and not-attempted all land on `False`**, and the output is a
confident number rather than an error.

**Why:** the distinction is normally preserved correctly upstream — the row is on disk, the field is
present-and-empty, the item exists but was never asked — and is destroyed at aggregation, by the
language's own defaults: `.get(k, False)`, `except: continue`, a falsy empty string, a key checked
in one of two legal locations. A rule stated as a property of the output ("never-asked and
answered-badly must never look alike") binds only the one site its author is auditing; every later
reducer is a fresh unchecked chance to violate it. Worse, the violating edit is the one that looks
like good hygiene — the tolerance that keeps a long batch alive is exactly the operation that maps
absent onto failed.

**How to apply:** three rules, all mechanical.

1. **The third state lives in the type, not in a convention.** Outcomes cross a module boundary as a
   tagged value — `NOT_ASKED` / `NOT_DELIVERED` / `DELIVERED(pass|fail)` — never a bare bool. A
   consumer that wants a bool must ask for one explicitly and thereby name what it does with the
   other two.
2. **Assert totality at every reducer.** `sum(buckets) == rows_read_from_source`, checked in code,
   raising on mismatch. This is the one check that catches the drop you did not anticipate, because
   it needs no theory of how the row went missing.
3. **Print the reconciliation line unconditionally**, beside every rate: `n/N asked, K unparseable,
   M not attempted`. A rate whose denominator is not the source count is unreadable, and a reader
   cannot tell a clean run from a lossy one without it. Never `continue` past a row you could not
   parse — bucket it.

Measured (2026-08, model-benchmark battery): the same defect landed six ways in one session under a
written rule against it — an unasked rung broke a chain walker and reported "cleared nothing"; an
absent response re-scored as a wrong answer and zeroed a whole reference column; a verdict stored at
top level instead of nested dropped its rows and read as a coverage gap; a repetition loop scored as
a wrong answer; a detector that tested low entropy without testing length flagged a CORRECT
three-character reply as degenerate; and an audit script silently dropped 473 rows it could not
parse and printed a confident percentage over the 21 it could — inside the audit written to catch
this exact class. Related: [[feedback_excluded_and_broken_look_identical]],
[[feedback_delegate_output_trust]].
