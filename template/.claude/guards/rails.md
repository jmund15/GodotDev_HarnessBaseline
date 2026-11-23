# Delegate rails

These rails bind every delegate beside its brief. When a rail and your brief conflict, report it instead of picking one: the parent owns that call.

## Return
- Write the deliverable to its named path part by part as each settles: a compaction resumes from disk, not from your context. With no path, your final message is the deliverable.
- Nobody answers a question in your return. Make the choice, state it as an assumption, and finish.
- Close by naming what you could not satisfy and why: a hidden gap becomes the parent's wrong next step.
- A job over a list returns one line per item: done with its evidence, or its blocker.
- Write complete sentences; cut filler, never a finding or caveat.

## Verified versus inferred
- Quote sources verbatim, and keep your inference in its own labeled section.
- Give each claim about the code its rung (`reference/claim_confidence.md`): observed (you ran it), read (you read the deciding line; cite `file:line`), traced, pattern or matched. A claim below read says so. Code shows what it does, never why.
- Back an absence with a read-only git command and its output (`git grep -n <pattern> <ref> -- <path>`). An empty Grep or Glob proves nothing: both skip gitignored paths.
- A count only a language server can settle (every caller) is a gap you report, never an estimate.

## Write set
- Change only the files your brief's `targets:` list names; a read-only job writes only its deliverable. Return any other change you need, cleanups included, instead of making it.
- Build what your brief settles as settled; where you cannot, say why. A question or an example in it is yours to analyse: serve its reason, and say where you departed. Report a scope you think wrong; never cut it.
- Run your unit's own test in the foreground before you return. A run sized by the product (a gate, a whole suite) is the parent's.
- A red run is a result: report its first failing line. Never retry a flaky case or loosen an assertion to turn it green.
- In a worktree on your own branch, commit there. In a shared checkout, commit nothing and return your changed paths.

## The floor
- Never edit `settings*.json`, `allowed-tools` or a hook to unblock yourself. A denial ends that attempt: report the grant you need and stop.
- A destructive operation (`CLAUDE.core.md` §Shell Discipline) needs the owner's words, which no brief carries: return it as the parent's step.

## Waits
- No wake reaches a delegate. Keep working while your job runs, then wait in foreground calls of at most 270000 ms each (`until <done-test>; do sleep 30; done`): your prompt cache lives 5 minutes, and a later return re-reads your whole context. Never repeat a status check call after call.
- End your turn only once every command you started has finished: nothing resumes a returned delegate. The one exception: when waiting is all that is left and the job will end past about 30 minutes, return its log path, pid, expected end and the steps left, even if the brief says to wait: the parent spends one wake on it, not your polls.
