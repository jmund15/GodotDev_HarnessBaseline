@CLAUDE.core.md

# CLAUDE.md — MaddManagement

**MaddManagement** is a two-person project manager for the MaddMund game studio: React and Vite on the front end, Convex on the back end, vitest and Playwright for tests. `reference/project_stack.md` holds the version pins.

## Build and Test
- `npm run typecheck` (`tsc -b` and the Convex project), `npm run test` (vitest), `npm run staging:deploy` then `npm run staging:e2e` (Playwright, staging only). `npm run verify` runs all three; `change_control` §Gate cadence sets which rows run when.
- A harness change runs its unit's test file; the main check runs the MaddManager-local harness tests after the land.

## Development Philosophy: Hybrid TDD
- **Test-first:** server and logic code gets a failing test before the code, and the red run is kept as evidence: Convex functions and model rules (`convex/**/*.test.ts`, vitest `convex` project) and pure logic in `shared/`, `src/**/*.ts` and `convex/**/*.node.test.ts` (vitest `domain` project, node, no DOM). A component's logic moves into a pure `.ts` module so it can be tested this way.
- **Integration-verified:** a screen's behaviour (`src/**/*.tsx`) is proved by Playwright rows run only against staging (`npm run staging:deploy`, then `npm run staging:e2e`), plus `tsc -b` and the axe check. Staging runs from a build worktree: the main checkout's `.convex/local` belongs to the owner's dev servers, and an e2e run resets the data of the backend it targets. `change_control` §Gate cadence sets which rows run when.
- **Owner-approved:** the agent makes each screen as good as it can: usable, true to the written visual system and the kit, reading at a glance, and free of every problem the owners named. It judges its own frames for all of that and fixes what falls short before an owner sees it; a problem it can see is never handed back as taste. The owners have the final say: they accept or reject each result, their reaction overrides the agent's judgment, and only they start a change to the written visual system (`product_ui`). Before a Part closes, `/agent_playtest` walks each person task on the rendered build, and each failure is fixed.
- **Refuse:** "too small for a test", "the e2e row covers it" for server or pure logic, and a retry, sleep or loosened assertion that turns a flaky row green: find its cause.

## Design Source (Obsidian)
Read and search `DevProjects/MaddManagement`, plus any vault path the owner, a skill or a command names; it is also the semantic-search design root. Search before choosing a path. Project-specific writes go to `MaddManagement/Claude/`. Load `obsidian_conventions`; `environment_bootstrap` owns machine paths. Never invent a formula absent from the design source. The worklog is `DevProjects/MaddManagement/Claude/TODO/Worklog.md`; `/worklog` owns its operations.

## Domain Index
Rules in `rules/` load on their paths: screens and the visual system (`product_ui`), Playwright rows (`e2e_authoring`). Memory search seeds per domain: `reference/memory_domains.md`. Subsystems register in `skills/project_subsystems/SKILL.md`, the one registry subsystem-aware commands read. Skills load by their descriptions.
