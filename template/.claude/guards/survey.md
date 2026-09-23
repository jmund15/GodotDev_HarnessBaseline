# Guard: survey — read/search/discovery delegates

Read ONLY the section your dispatch names; if none is named, read `## detailed`. `any.md`'s section of the same tier binds you too and arrives with this file. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## detailed

- **Blast-radius claims carry the evidence that produced them. Name the one fact the claim is safe because of** — the rungs by NAME, never by number — `claim_confidence.md` numbers them and a second numbering here would invert it. **matched**: a search returned the name — whichever search the routing rules above send you to; the rung never licenses substituting a cheaper one. **read**: you opened each hit and confirmed it is a use, not a declaration or a comment. **read (swept)**: you also covered the reference classes a code-only search cannot see — asset and scene references, script paths, UIDs, data keys, signal names. **observed**: you ran it and watched the failure — that one is **orchestrator work, never yours**, since a fanned lens cannot run tests or the LSP under the concurrency guard. Report `read (swept)` as your ceiling and name `observed` as the caller's next step. An exhaustive enumeration that genuinely needs the LSP (`findReferences`/`incomingCalls`) is a GAP you report, not a number you estimate: an estimated count reads as an enumerated one and is worse than the admitted gap. [reference/claim_confidence.md]
- Grep stays correct for literal field values, UID hashes, regex alternation, attribute markers, comment scans, `using` directives, and as the anchor step before LSP. [CLAUDE.md §Tool Routing]
- Route by output size and loss risk (`guards/any.md`): copyable enumeration across many files goes to `read_files`; a read whose answer needs line-level judgment stays direct.
- An empty Grep/Glob is NOT evidence of absence. Before reporting "not found", confirm with `ls` on the directory or `git ls-tree`. [gotcha_survey_absence_feature_lives_on_unmerged_branch.md]
- Your searches see only the checked-out worktree. Check unmerged branches (`git branch -a`, `git grep <pattern> <branch>`) before concluding a feature does not exist. [gotcha_survey_absence_feature_lives_on_unmerged_branch.md]
- A directory missing from `git status` or Glob may be gitignored rather than absent — run `git check-ignore -v <path>` before calling it missing. [gotcha_gitignore_build_glob_swallows_production_dirs.md]

## condensed

- CLAUDE.md §Tool Routing's pre-call litmus governs every read/search call.
- Blast radius states its rung — name-matched / read-and-confirmed / swept the asset, data and key classes too (the rung grades evidence, never tool choice — CLAUDE.md §Tool Routing still picks the search); "ran it and observed" is the orchestrator's, not yours. LSP-exhaustive enumeration is a reported GAP, never an estimate: `reference/claim_confidence.md`.
- Absence is never proven by an empty search: `gotcha_gitignore_build_glob_swallows_production_dirs.md`, `gotcha_survey_absence_feature_lives_on_unmerged_branch.md`.

## minimal

Read this file's `## condensed` section.
