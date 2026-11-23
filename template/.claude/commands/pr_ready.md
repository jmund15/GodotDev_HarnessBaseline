---
description: Audit a branch's release readiness before a PR or merge through /review diff mode
argument-hint: "[<base>..<head>]"
---

# PR Ready

Checks the invariants that pass every test and still ship broken: a dropped branch, a feature no authored data reaches, an unmigrated call site, stale worklog or roadmap state, a leftover diagnostic. It runs the session-side checks, then one `/review` diff mode pass with this file's §Lenses as the checklist. It runs no test suite.

## When

- After the drive's `close` check passes (`change_control` §Gate cadence) and before `/create_pr`, `/merge_pr` or `/commit_push`. `/drive` runs it when a branch is prepared for a PR.
- A change only to `.claude/` or to docs skips it: the reachability lenses have no authored data or call sites to judge.

## Arguments

- `/pr_ready`: the range `<merge-base>..HEAD`, where `<merge-base>` is `git merge-base main HEAD`, plus the uncommitted changes to tracked files.
- `/pr_ready <base>..<head>`: that range alone.

A range `git rev-parse` cannot resolve is named above this usage, and nothing runs. When the range is empty and `git status --porcelain` lists nothing, print `clean, nothing to gate` and stop.

## 1. Session-side checks

Run each check from the checkout root over the changed files, and keep its output for the brief:

- **Leftover diagnostics:** `git grep -n "\[DIAG-" -- <changed files>`. Each hit blocks the PR.
- **Framework leak:** for each `framework_paths` root in the registry (`skills/project_subsystems/SKILL.md`), `git grep -n "<project namespace>" -- <root>`. Each hit blocks the PR.
- **The project's checks:** read each file `python3 .claude/tools/adaptation.py get pr_ready.checks` prints and run its steps, such as a boot-log probe or a playtest-route check. Each file says which of its results block the PR. A check that cannot run is a coverage gap, never a pass.

A check that boots the app or runs a single-flight tool runs here in the session, never in the seat.

## 2. Review

Run `/review` diff mode §1 to §4 over the range. When the default range has uncommitted changes, append `git diff HEAD` to `subject.diff` and add those files to the touched paths. The brief adds:

- **This command's checklist:** `.claude/commands/pr_ready.md` §Lenses. `/review` diff mode already carries `.claude/commands/checklists/parity.md`.
- **The step 1 output**, raw.
- **For the state lens:** the paths `.claude/worklog-titles.md` and the touched topic folder's roadmap.md.

## 3. Report

Print `/review` §5's report with the blocking findings first: a parity finding, a step 1 hit or project result marked blocking, and an UNREACHED finding whose probe the claim check re-ran. Each is fixed before the PR, or the owner accepts it in words. Then list the coverage gaps: a project check that could not run, and an UNREACHED finding whose probe could not be re-run.

## Lenses

The seat applies each lens to the diff. Every finding takes `/review`'s findings format.

- **data-reach:** for each behaviour-selecting default and each threshold the diff adds or touches, name the authored data file that selects the non-default branch or crosses the threshold. Rubric below.
- **wire-reach:** for each new parameter on an existing signature and each new type the feature needs at runtime, name the production call site, scene or config that supplies it. Rubric below.
- **api-consume:** for each new public type or method, check that the call sites that motivated it use it. Name each old path that still bypasses it, with `file:line`.
- **state:** against the worklog titles and the roadmap, flag a shipped item still open and a completed Part not marked done.
- **doc-coverage:** flag a changed authored-data field (an engine export, a config key) without its doc comment, a changed subsystem whose skill or roadmap was not touched, and a new public API without docs.

### Reachability rubric

The failure it hunts is the inert feature: code that compiles, passes the gate and never runs, because no authored data and no production wiring selects the branch that enables it. Ask what authored value reaches the branch, never whether the code is correct.

The production corpus is the tracked files outside the test tree and outside any prototype tree the project excludes from production. A reacher inside either is not a reacher: a test that passes the new parameter, or a fixture that instantiates the new type, has let inert features past a green gate before.

| Lens | Candidate from the diff | The reacher to name |
|---|---|---|
| data-reach | a new or changed authored field whose default selects behaviour (enum, bool, nullable reference, strategy slot) | an authored data file that sets a non-default value |
| data-reach | a new threshold, band or comparison against a tunable field | authored values on both sides, and the share of the authored range that reaches each branch |
| wire-reach | a new parameter on an existing method | a production call site that passes it explicitly; a defaulted parameter compiles at every unmigrated site |
| wire-reach | a new type, component or node needed at runtime | a production scene, resource or config that references it |

One verdict per candidate:

- **UNREACHED:** the probe found no reacher. The finding's evidence is a `{command, output}` item holding the exact read-only `git grep` and its empty output, so the claim check re-runs it. Word it "no authored reacher found in <the corpus the probe covered>", never "dead code": a probe scoped to the diff cannot prove the code unreachable everywhere. Name the data or wiring that would reach it.
- **NARROW:** the threshold is crossed, but only by a minority of the authored range. Quote both values with their files and give the share.
- **INDETERMINATE:** the selecting value is computed at runtime or generated, so no literal probe can find it. Name the unknown and who can settle it. Never upgrade it to UNREACHED.
- **REACHED:** no finding. List it under the closing coverage heading below.

Each reachability lens ends with one `## <lens> coverage` heading. It lists every candidate examined, and quotes each REACHED candidate's reacher as evidence. A lens that found no candidates says so, so "nothing found" stays distinct from "nothing examined".
