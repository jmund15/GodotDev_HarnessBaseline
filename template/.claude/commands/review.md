---
description: Review a plan or a code diff with one holistic seat and check its quotes
argument-hint: <plan.md> | <base>..<head> | --files <path>...
---

# Review

One holistic reviewer reads a plan or a diff against the system it lands in. Its findings quote their sources, `.claude/tools/quote_check.py` confirms the quotes, and review ends after at most two rounds. The session decides every finding; open taste forks go to the owner.

## When

- **Plan mode** runs on a plan before its first slice lands, because landed work reads to a reviewer as a premise. `/plan` sends its plan here before the owner's approval.
- **Small, local, reversible work skips plan review.** A fix to a file in the top 5% by fix commits (the redesign rule in `CLAUDE.core.md`) or to a shared contract (a floor module, a shared command, tool or skill) is not small. It goes through `/plan` and `/review`, because its reach is wide.
- **Diff mode** runs when the owner or a drive asks for a second look. It is also the review step of `/review_pr`, `/pr_ready` and `/pr_pipeline`.

## Arguments

- `/review <plan.md>`: plan mode over that plan file.
- `/review <base>..<head>`: diff mode over the commit range.
- `/review --files <path>...`: diff mode over each file whole, with its uncommitted diff.

With no argument, print these three lines and stop. A path that does not exist, or a range `git rev-parse` cannot resolve, is named above the same usage, and nothing is dispatched. A range or file list with no change prints `nothing to review` and stops.

## 1. Write the brief

Make the run directory `.claude/scratch/review/<subject-slug>/` and write `<run-dir>/brief.md` as an open brief (`skills/orchestration/reference/brief_checks.md`): raw inputs, and none of the session's conclusions. It holds:

- **Read first:** the owner's note and the core principles in `.claude/CLAUDE.core.md`. The brief points at them and never copies them, so the owner never re-adds a principle to a brief.
- **The owner's words** behind the change, verbatim, each with its source: the owner-decisions block of the state file (`.claude/work/<slug>.md`) or the session transcript `.jsonl` (`/session_digest` finds it).
- **The subject.** Plan mode: the plan path. Diff mode: the range or file list, and `<run-dir>/subject.diff` holding `git diff <range>` (for `--files`, `git diff HEAD -- <files>`). When the range moves a submodule pointer, append `git -C <submodule> diff <old>..<new>`: a caller reviewed without its callee's change hides a broken contract.
- **The system it lands in:** the paths of the files the change touches and of the directories holding them. The reviewer reads their callers itself.
- **Fix history:** the output of `git log --oneline --grep='^fix' -- <touched files>`.
- **Diff mode, the size change:** the output of `git diff --numstat <range>`. A rise in code, test or prose lines that names no new job is a root-cause-or-patch finding.
- **Diff mode, the checklists:** `.claude/commands/checklists/parity.md`, and each path `python3 .claude/tools/adaptation.py get review.checklists` prints.
- **The questions** below, as written.
- **The return:** `<run-dir>/findings.md` in the shape below. The seat writes nothing else.

### The questions

These are prompts for a whole-system read, not a checklist to answer and stop.

1. Root cause or patch?
2. The smallest system after the change, and what it does for later efficiency and flexibility.
3. Impact beyond the happy path.
4. Do the cited files and contracts match the code?
5. Is an agent assumption dressed as an owner requirement?
6. Does a project-local rule lower a shared floor?

### The findings file

One markdown file. Each finding is a `##` heading stating its claim, a line naming the change it asks for, and one fenced `json` block holding a list of evidence items, one source per item:

- `{"file": "<path>", "quote": "<exact text>"}`: the quote is an exact substring of the file under the reviewed checkout. An owner quote cites the state file or the transcript `.jsonl`.
- `{"command": "<read-only git command>", "output": "<its stdout>"}`: every count a finding rests on carries the command that prints it, because a reviewer's unbacked aggregate is the claim most often fabricated.

A heading with no evidence block is a question for the session, not a finding.

## 2. Dispatch

Pick the reviewer: the seat the owner named for this work, else the holistic-review row of `orchestration`'s model table. An architecture-scale plan (one that sets or changes a contract between subsystems) also gets one cross-system seat: a reviewer of another model family, with the same brief and its own findings file. Two families each catch defects the other misses.

Launch every seat in one `/delegate` call, so the session wakes once. When the subject and the files it touches overflow one seat's context, split them into coherent batches (a subsystem with its callers), one job per batch in the same launch. Each touched file sits in exactly one batch, and each findings file names its batch's files.

A command that reviews through this route (the PR commands, the red-team) puts its own lens file in the brief as the checklist and keeps steps 1 to 4 otherwise.

## 3. Check the claims

Run on each findings file:

```bash
python3 .claude/tools/quote_check.py <findings.md> --root <reviewed checkout>
```

The root is the checkout the change lives in, such as a drive's lane, never the launch directory: a quote checked against the wrong tree reads as fabricated. For each printed miss, read the cited source. Keep the finding only when the source supports it; otherwise drop it and list the miss in the report. A miss never earns a re-dispatch.

## 4. Fold and decide

Decide each surviving finding: apply it, or reject it with the evidence that refutes it. In diff mode, a parity finding is fixed before the change lands, or the owner accepts the dropped behaviour in words. A finding that turns on the owner's taste or intent goes into one packet for the owner, not into the plan.

**At most two rounds.** A second round runs only when the first round's fold changed the plan's principles, solution or reach; its brief adds the round-one findings with their decisions. A fold that changed none of them ends review. After round two, open forks go to the owner. Any converging set of dispatches, such as a drive's rework round, takes the same test.

## 5. Report

Print the mode, the seats, each finding with its decision, the claim-check misses, the rounds run and the owner packet. The run directory keeps the brief and the findings files.
