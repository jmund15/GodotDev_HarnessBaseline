# Model Ladder

**The role → model ladder. Load it whenever you pin a model or an effort.** §Role guidance is the only home for which model fills which role and what each is for / never for. Dispatch *rules* are `orchestration` §5; CLAUDE.md §Model Delegation keeps the copyable-vs-derived route and the which-currency rule; how to launch a sidecar is `reference/sidecar_dispatch.md`.

**Authoring an entry:** the reader is an orchestrator picking a model and an effort at a glance. Lead with the routing verdict. State capability in the instrument's own terms (its score, its fabrication count), never relative to another row — rows retire. **Numbers, dates and campaign narrative do not live here**; they live in the Obsidian benchmark results below, and a `±` cell summarizes them in one clause.

**Availability is not authored here.** `reference/external_models.json` holds it; `python3 .claude/tools/model_registry.py available` enumerates. An excluded model is out of consideration — re-select from what remains.

Benchmark results (Obsidian, `Claude/Meta/Benchmark/`): `model-effort-v1.0/REPORT.md` (7-task cross-domain battery), `results/SCORES-*.md` (sidecar campaigns), `Model Effort Calibration Baseline.md`. Read them only when a `±` cell cannot settle the pick.

---

## Role guidance

Roles are relative to whichever model runs the session. `±` = what to pin the row for / what never to.

| model | role | intel | deleg | speed | cost | taste | effort | ± |
|---|---|---|---|---|---|---|---|---|
| fable | orchestrator, ideal design/architecture, elegant creative | 9 | 9 | 3 | 2 | 9 | high complex, medium for guiding a long-horizon task | + long-horizon sessions, cross-system seams, ideal-design verdict, spec-writing for delegates; − most expensive model, less thorough when diving into one specific task/topic |
| opus | architect & executor | 8 | 5 | 4 | 4 | 7 | xhigh hardest design + buried-fork verification / high while ambiguous / medium exec / low tight specs | + scoped work that fits one context window: coding/execution, scoped architecture, red-team, debugging, low fabrication tendency; − needs intent specified — unscoped it circles; loses the thread and makes trivial mistakes on long-horizon ambiguous tasks |
| sonnet | fan-out, validation, & tight exec | 5 | 6 | 6 | 7 | 5 | high for more depth / medium tight spec | + read-heavy surveys, authoring/execution under tight spec, validation verdicts; − lower quality on open-ended design; never above `high` (effort INVERTS); `low` effort does not think AT ALL, only for strict no-effort execution |
| deepseek flash (sidecar) | I/O worker `paid` + budget-gated middle tier | 6 | — | 6 | 5 | 6 | `low` converged / `max` open; `high` is middle-ground | + converged surfaces at `flash·low`; decent red-team/reviewer on `max`; − opus separates on open judgment; never the reserved floor; spends dollars, not quota |
| deepseek pro `gated` (sidecar) | orchestrator/architect | 7 | 7 | 4 | 4 | 6 | `max` open | + stays on task better than opus; decent fable substitute; − not as smart or creative; no vision |
| gpt-5.6-luna (sidecar) | scoped planning/architecture/review + spec-tight execution | 6 | — | 4 | 5 | — | `low` converged / `max` open architecting / `high` thick planning | + scoped architecting, thick planning, tight-spec implementation, test authoring; low fabrications; − weak at root-cause debugging and loose-spec execution; unattended it stops and asks rather than guessing — tell it explicitly to dig on its own and not return until done |
| qwen-local (`ai-worker`) | I/O worker — free, local | 3 | — | 6 | 10 | — | low | + every fact COPYABLE from the supplied material: digest reads, doc prose, extraction; zero cost, so it routes in every band; − anything DERIVED, ordered or chained — route that to sonnet or above |
| haiku | scout | 2 | — | 9 | 9 | 1 | low | + read-only locate / enumerate / extract / read-and-report; − hallucinates paths; never for authoring a doc, patch, spec, or anything that must preserve values verbatim; never where ordering or supersession decides the answer |

**Delegate selection: Intelligence > Taste > Cost > Speed.** Cost breaks a tie once intelligence and taste are satisfied, or outranks them when the budget band is tight.

**Session-model selection inverts on one axis:** an orchestrator scopes, dispatches and reviews rather than executing, so at equal intelligence the *lower-`deleg`* row is the better **delegate**. Canonical pairing: orchestrator-row session, executor-row delegates.

**`agentType: 'Explore'`** composes with any pin and halves per-dispatch input cost by loading no project doctrine. Read-only locate/enumerate/extract lenses only — never a lens that must APPLY project rules. It selects an agent type, not a model; pin one beside it.

## Axis definitions

Higher = better on every axis.

- **intel** — hardest problem handleable unsupervised.
- **deleg** — orchestration ability: scoping, spec-writing, dispatching, reviewing returns, holding a long-horizon plan. Independent of intel; governs who RUNS a session. `—` = never a session model.
- **taste** — UI/UX, code cleanliness, API design, prose, relative to Jacob's preference.
- **speed** — time to completion.
- **effort** — peak quality-per-token rung per work shape; doubles as the default pin for judgment-stage `agent()` dispatches and the session `/effort` under that model. `max` is banned on Anthropic pins (cost far exceeds gain). `—` = no effort knob.

## Per-dispatch harness cost — context disclosure per mechanism

Every dispatch mechanism has one knob deciding how much harness the child loads, charged PER AGENT — a 10-lens fan-out pays it ten times:

| mechanism | knob | where |
|---|---|---|
| Workflow `agent()` | `opts.agentType` (`dispatch.js` requires it per job) | table below |
| `Agent` tool | `subagent_type` (same agent types; no effort pin — fan-outs don't go here) | table below |
| Sidecar | `-D bare\|pointer\|full` × `-G` | `reference/sidecar_dispatch.md` agent-type table |

First-turn input tokens, identical trivial prompt (Anthropic transports):

| agentType | Sonnet | Haiku |
|---|---|---|
| `Explore` / `Plan` | 27.8K | 15.1K |
| `general-purpose` / default workflow subagent | 53.4K | 33.6K |

`Explore`/`Plan` receive no project CLAUDE.md and no memory index — read-only locate/enumerate/extract only, never a lens that must APPLY project rules (`bare` is the sidecar analog; `full` ≈ `general-purpose`). An unpinned agent inherits the SESSION model regardless of agentType.
