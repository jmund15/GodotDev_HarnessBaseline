---
description: Land a never-again correction in the file that owns the decision, or send a much-fixed file to a redesign
---

# /codify

Take one correction the owner wants never to recur, find the file that owns the decision that went wrong, and fix or delete the cause there in one commit. Run it when the owner asks (`/codify`, "never let this happen again"). A mid-task patch does not run `/codify`: the redesign rule in `CLAUDE.core.md` reaches it there, and Step 2 applies the same rule here.

## Arguments

- `/codify <text>`: that correction.
- Bare `/codify`: the correction just issued. Echo your reading of it before Step 1. When the context no longer holds it (after a compaction or `/clear`), read the session through `/session_digest`; when that finds nothing, say so and stop. Never reconstruct a plausible correction: an invented one passes every later step.
- A correction in another session (a named id, a pasted quote): read that session through `/session_digest` first.
- Several corrections: one pass each, in turn.

## Step 1 — Name the decision and its owner

1. Name the decision that went wrong: the one choice behind every complaint in the correction, not the symptom it names. A complaint from a different choice is a separate pass.
2. Name the earliest stage that made it (design, plan, brief or the edit itself) from the artifact that first records it. A choice a plan or brief fixed belongs to that stage, and the later stages only carried it out.
3. Name what made it costly: the limit, default, guard or missing check the harness could change. Show from the evidence (error text, a transcript line, a rerun that varies the suspect) that it caused this failure; a cause read off an error message alone is a guess.
4. Find the owner: semantic-search `.claude/` for the rule at that decision, read each hit file whole, and read its history with `git log --oneline -i --grep '<terms of the decision>' -- <hit path>`.

**Done:** the decision, its stage with the recording line, the cost with its evidence, and the owner as `path:section` are in the report.

## Step 2 — Fix, redesign or class case

From the root of the repo that tracks the owner, print the files in the top 5% by fix commits (each file counted from its last commit whose subject says `redesign`):

```bash
git log --format=@%s --name-only | awk '$0~"^@"{s=tolower($0);next} NF&&!d[$0]{if(s~"redesign")d[$0]=1;else if(s~"^@fix")c[$0]++} END{for(f in c)print c[f],f}' | sort -rn | awk '{a[NR]=$0} END{for(i=1;i<=NR*0.05;i++)print a[i]}'
```

- **Redesign.** The owner is on the list, or an earlier fix of the same decision did not hold: plan a redesign through `/plan` and `/review`, and land nothing else here. Name why each earlier fix did not hold; the redesign answers that. A fix that restores a broken floor (a guard denying every call, a red main) lands first, and its redesign plan follows. The redesign's commit subject says `redesign`, which restarts the file's count.
- **Class case.** The same decision was fixed across several files: fix the shared cause once, in the file that owns it, and delete the per-file copies.
- **Fix.** Otherwise go to Step 3.

**Done:** list membership, each earlier fix with why it did not hold, and the route are in the report.

## Step 3 — Pick the fix

| Finding at the owner | Fix |
|---|---|
| A limit, default or guard turned the choice into waste | Change or delete it; a rule teaching agents to work around it is a symptom fix |
| The rule exists but did not load at the decision | Move the whole rule to the surface that loads there (list below); leave the old home empty or a pointer |
| It loaded and was misread: ambiguous text, a contradicting sibling, a default pointing the wrong way | Fix that text, the sibling and the default in the owner |
| It loaded, read clearly and was ignored once | Land nothing; report it |
| It was ignored again after a text fix | The tool that makes the choice computes it, or, for a loss case only, a hook |
| A guard already denies it and names a usable next step | No change |
| Absent | Write the rule in the surface that loads at the decision |

A new hook is only for a loss case: lost work, a broken floor, a machine-wide failure. Any other repeat is text in the owner or a change in the tool that makes the choice. A fix that adds a file or a check names what it removes, or the loss case it answers.

Surfaces, by when they reach the agent:

- The command, skill or tool that runs the step: when the step runs.
- `rules/<name>.md` with a `paths:` glob anchored below the repo root: when a file of that class is touched. Never an editing rule, because a harness `.md` is read to be executed.
- `CLAUDE.core.md` or the project's `CLAUDE.md`: every session. Only for a decision most sessions face before any trigger could deliver it; T2's byte bounds apply.
- `auto-memory/archive/`: when a search finds it. Memory follows `CLAUDE.core.md`'s memory rules; a note whose rule an owner already states lands nothing.
- A hook, for a loss case, plus a home the agent reads before the call the hook blocks.

**Done:** the chosen surface is named, with the reason each surface above it was rejected.

## Step 4 — Land and prove

1. Write the verdict, not the incident: the rule, its trigger and the one clause that makes it non-obvious. The evidence goes in the commit message. Apply `instruction_quality` §3b's fit test.
2. Delete, in the same change, the text the fix makes redundant, stale or wrong.
3. Prove it: the case that failed passes after (a test, a rerun, or for a text fix the new sentence read against the old for the same decision). A hook proves a deny on a planted violation and an allow on its nearest clean call.
4. Commit once, naming the decision.

**Done:** the commit exists and the proof's command and result are in the report.

## Report

```
## codify — <the decision, one line>
Owner:  <path:section>   stage: <stage>   cost: <limit, default, guard or check>
Route:  fix | redesign (<plan path>) | class case   top 5%: yes | no   earlier fixes: <each, why it did not hold | none>
Fix:    <row of Step 3>   surface: <surface>; rejected above it: <surface: reason>, ...
Landed: <commit> <paths> (+<added>/-<removed> bytes)   removed: <files, checks or lines>
Proof:  <command or reading> -> <result>
```
