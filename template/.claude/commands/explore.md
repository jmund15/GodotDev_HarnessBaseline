---
allowed-tools: Bash(git ls-files:*), Bash(git ls-tree:*), Bash(git branch:*), Bash(git grep:*), Bash(git log:*), Bash(git check-ignore:*), Glob, Grep, Read, Write, Workflow
description: Ground a design: map what exists, its owners, constraints and consumers, with explicit coverage
---

# /explore

Establish what exists before a design is proposed: read-only lenses survey one topic in parallel and return evidence-backed claims, never recommendations. `/plan` consumes the result. A breadth map with no design question is a `/delegate` scout, not an explore.

## Arguments

- `/explore <topic>`: the lenses the roster below selects.
- `/explore --make-this-easy <one named change>`: adds `exp-change-ease`; a vague topic is not enough for it.
- No argument, or a design question too vague to set coverage: ask one clarifying question and stop.

## Step 1 — Scope

1. State the topic in the owner's words, verbatim, with the capability or files it concerns, the intended change, and each owner-stated constraint or exclusion. A fact inherited from another session is a claim to check, never ground truth.
2. Pick the lenses from the roster: every lens whose trigger holds runs, and each runs alone (one lens per seat, never merged). Skip a lens only when existing evidence answers the topic's questions from sources still unchanged; cite that evidence. Record why each lens ran or was skipped.
3. Map each question to the lens that owns it. A question one command can enumerate (every call site, every file holding a field, every test naming a symbol) is answered by that command now: write its output to a file in the run directory, sort the hits a fact decides (path class, declaration or use, test or production), and hand the file to the owning lens to judge the rest. A question no lens owns and no command answers gets a bespoke lens (roster, last paragraph).

**Done:** the run directory `.claude/scratch/explore_<slug>/` holds `context.md`, and the selection with its reasons is recorded there.

### Roster

| Lens | Runs when |
|---|---|
| `exp-memory` | relevant memory constraints and gotchas are unverified |
| `exp-prior-art` | relevant existing owners or families are unverified |
| `exp-integration-surface` | the topic names an existing type, file, scene, global singleton or blackboard key |
| `exp-design-source` | the topic is design-loaded, or names a system, roadmap Part or formula; never for harness or process topics |
| `exp-harness-governance` | the topic is process- or tooling-shaped, edits `.claude/`, or comes from a drive |
| `exp-external-truth` | the topic's correctness rests on engine, library, framework or runtime behaviour |
| `exp-empirical-state` | the topic touches tested logic code or authored data files |
| `exp-change-ease` | `--make-this-easy` was given; it adds to the others, since its friction claims read against theirs |

Each lens's mandate is `.claude/commands/agents/explore/<lens>.md`; every lens also reads [`agents/explore_agents.md`](agents/explore_agents.md). A bespoke lens is a new file there, written for this topic's risk with a named failure mode it hunts; a lens a selected mandate already contains extends that mandate instead.

## Step 2 — Brief and dispatch

`context.md` is an open brief: the owner's words verbatim, the raw source paths, the owner-tagged constraints, the question map and the enumeration files, and the deliverable. It carries no conclusion of yours and no condensed or pre-filtered input; the lenses read the sources.

Invoking `/explore` authorizes the fan-out. Issue one `Workflow` call to `.claude/workflows/explore_fanout.js` with:

```json
{
  "lenses": [{"key": "<lens>", "promptPath": ".claude/commands/agents/explore/<lens>.md", "kind": "lens", "model": "<pin>", "effort": "<effort>"}],
  "contextPrefixPath": ".claude/scratch/explore_<slug>/context.md",
  "spillDir": ".claude/scratch/explore_<slug>/",
  "checker": {"model": "<pin>", "effort": "<effort>"}
}
```

Each pin comes from `orchestration`'s table for a read-heavy survey. The engine runs the lenses, stamps each claim with its lens, groups claims by subject, flags contradictions and corroboration, checks evidence, and returns once for the whole set. Builds, tests and language-server calls stay with the parent and run serially; lenses never run them.

**Done:** the engine returned, and its result and each lens's spill file are in the run directory.

## Step 3 — Consolidate and report

- Account for every selected lens: one that did not run or returned nothing leaves its dimension UNCOVERED, never clean. Recover a missing lens from its spill file or with `/session_digest --workflow-dir <transcript dir>` before anything else.
- Account for every question: name the claims that answer it; a question with none is UNCOVERED.
- Read the cited source of every claim the engine did not mark verified before it enters the report. Re-run the `{command, output}` evidence of an `absent` claim a design decision will rest on.
- Verify a premise contradiction first-party. Settle conflicting claims on their evidence, never on order or count.

Report the topic, lenses run and skipped, counts and the run directory briefly, then the material claims:

1. Premise contradictions that change the task.
2. Existing owners and families, their real coverage and consumers, and the real integration target with its type contract.
3. Constraints and blast radius.
4. Resolved contradictions, then the remaining gaps and UNCOVERED dimensions.

With `--make-this-easy`, add the verified friction; derive from it the smallest restructuring and name what it must not touch (the invariants and settled decisions the claims found). That restructuring becomes the plan's first slice, or goes to the owner as a preparatory Part when it outgrows one. Never expand a narrow change into speculative infrastructure.

Carry constraints into the plan, owner claims into its inventory of existing families, and gaps into its open scope. A plan consumes this result; it does not regenerate it.
