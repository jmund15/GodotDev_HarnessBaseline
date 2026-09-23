---
name: gotcha_survey_absence_feature_lives_on_unmerged_branch
description: "A code survey can return a confident, well-evidenced NOT FOUND simply because the feature lives on an unmerged branch — plain Grep/Glob/Read only ever see the checked-out worktree."
metadata: 
  node_type: memory
  type: project
  originSessionId: 11491863-83a1-49e2-9147-f13b079e8f51
  modified: 2026-08-18T22:16:47.268Z
retire_when:
  - review-by: 2027-01-05
---

A survey agent (or you) can report a feature as **nonexistent**, citing every search tried, when the
feature is fully shipped on an **unmerged branch**. `Grep` / `Glob` / `Read` see only the checked-out
worktree, so absence evidence is silently scoped to the current branch and reads as repo-wide.

**Litmus before accepting any NOT FOUND:** does a design doc, roadmap Part, or commit message say this
shipped? If yes, the absence is a branch artifact until proven otherwise. Check
`git branch -a --list "*<topic>*"` and `git branch --merged main` first.

**Read an unmerged branch without checking out** (safe, no worktree mutation):
- `git -C <repo> show <branch>:<repo-relative/path>` — file contents
- `git -C <repo> grep -n "<pattern>" <branch> -- "*.cs"` — branch-scoped grep
- `git -C <repo> ls-tree -r --name-only <branch>` — enumerate paths
- `git -C <repo> diff --name-status main...<branch>` — the branch's true surface

**Submodule caveat:** a submodule is a gitlink, so `git show <branch>:<Submodule>/...` FAILS. Read those
files normally from disk — a submodule's contents are not in the superproject's tree at any branch.

**When dispatching survey agents, put the branch and these commands in the prompt.** Agents default to
plain Grep, hit false-empties, and report confident absence. Require every absence claim to quote the
branch-scoped command that produced it. Related: [[gotcha_workflow_fanout_search_false_absence]].

**This bites auto-memory itself, and the cost is a duplicate rather than a gap.** The memory-save rule says to
check for an existing file before writing a new memory — but that check is a search of the CURRENT
branch, so a memory added on an unmerged branch is invisible and the honest search returns nothing.
Measured 2026-08-11: a session wrote `gotcha_godot_mcp_engine_pin_diverges_from_godot_bin` while
an equivalent memory (same gotcha, added in `af51d3312` on another branch) already
existed; both would have landed on main as separate files. **Before writing a NEW memory file, search
across branches** — `git log --all --oneline --diff-filter=A -- '*<topic-token>*'` finds the adds, and
the main checkout's own working tree is a second place to look (a sibling worktree may hold an
uncommitted one). On a hit, merge into the EXISTING name and repoint inbound references, so the two
branches converge on one path instead of silently coexisting.

**The INVERSE bites harder: a feature can be present on TWO branches and absent from their union.**
Checking each half on the branch that happens to carry it produces two true findings and one false
conclusion. Measured 2026-08-18: `AddConsumedRange`/`AddAbsorbed` were verified on
`origin/npc-trait-system` and `ApplyTraitGrant`/`ICategoryWeightProvider` on `origin/spell-effectiveness`
— both correct — and the session concluded an eat→trait-grant→weighted-stamp chain was "already
shipped". Neither branch carries the chain end-to-end: `git grep "ICategoryWeightProvider\|ApplyTraitGrant"
origin/npc-trait-system -- "*.cs"` is EMPTY, and `git grep "AddConsumedRange\|AddAbsorbed"
origin/spell-effectiveness -- "*.cs"` is EMPTY. **Verify the CROSS-PRODUCT, not each half on its own
branch** — and when the two branches both modify the same type at that seam, the merge is a conflict
surface, not a formality. Litmus: "which branch would I demo this whole flow on?" No single answer →
the flow does not exist yet.

**Verified:** 2026-07-19 — two of three parallel survey agents reported the entire turret/summon feature
as nonexistent; a third found it on `feat/turret-spawner` (17 commits ahead, PR #94) via `git show`. The
roadmap had already marked its Parts `complete`, which is what exposed the contradiction.
