# Model Ladder

**The model table. Load it whenever you pin a model or an effort.** §Role guidance is the only home for which model fills which tier, what each row does reliably, at which rung, and what it must never get. Dispatch *rules* are `orchestration` §5; CLAUDE.md §Model Delegation keeps the copyable-vs-derived route and the which-currency rule; how to launch a sidecar is `reference/sidecar_dispatch.md`.

**Availability is not authored here.** `reference/external_models.json` holds it; `python3 .claude/tools/model_registry.py available` enumerates. An excluded model is out of consideration — re-select from what remains.

**A selectable model with no row here is UNMEASURED.** It claims no tier and nothing has scored it, so use a measured row unless the user explicitly requests that alias.

**Evidence lives in the Obsidian board**, `Claude/Meta/Benchmark/model-effort-v1.0/RESULTS-BOARD.md`. `TASKS.md` beside it maps each work shape a cell names to its task, and `Claude/Meta/Benchmark/README.md` says how to read a figure. Cells name the work shape in words and carry no figure; a claim resting only on a sealed-era cell says so. Rows that make no benchmark claim (`qwen-local`, availability, tier metadata) trace to `external_models.json`. Read the board only when this table cannot settle a pick.

---

## Role definitions

Five tiers, named by work shape so the name survives model churn. An Anthropic row's `role` cell opens with every tier token it claims; a non-Anthropic row claims its tiers through registry `roles`. `python3 .claude/tools/model_registry.py for-role <tier>` reads both and prints the rows that serve a tier on this seat and across the hop. A row claims a tier where the board or real use shows it does that work well. A row able to do a higher tier's work usually does lower work too, so it carries no lower-tier token unless the board shows an exception; cost picks among the rows that can do the job (§Role guidance).

| tier | work shape |
|---|---|
| `orchestrator` | scoping, dispatching, reviewing across systems; long-horizon sessions; the final cross-system design verdict |
| `architect` | solution design at any scope: fresh and scoped architecting, planning from a thin, thick or wrong-premise brief, design and plan review |
| `executor` | implementation and verification under a scope: coding, root-cause debugging, fix authorship, red-team of a concrete artifact |
| `fanout` | read-heavy and mechanical work: surveys, enumeration, text comparison, rubric checks, tight-spec execution |
| `scout` | locate / enumerate / extract, verifiable without applying project doctrine |

`validation` is not a tier — it is the `fanout` row at a lower effort pin, and `for-role validation` says so rather than implying a distinct row.

## Role guidance

Roles are relative to whichever model runs the session. `±` = what the row does reliably / what never to send it. Benchmark cells and real use both set a row; neither is gospel. Judge a work shape with no cell from the row's intelligence and its results on nearby shapes, never bar it. A cell that contradicts a rung goes to the owner before the rung changes. A range names its ends: pick the rung inside it from the subject, never the top by default. A non-Anthropic row's `role` cell names its work in words; its tier claims are registry `roles`.

| model | role | intel | deleg | speed | cost | billing | taste | effort | ± |
|---|---|---|---|---|---|---|---|---|---|
| fable | `orchestrator` — ideal design/architecture, elegant creative | 9 | 9 | 3 | 2 | Anthropic · plan | 9 | `high` default / `medium` guiding a long-horizon session / `low` converged spec, anchored lens, enumerable survey — but never `low` on a turn that must verify the current state of a named thing / `xhigh` review and short capability-sensitive one-offs — on review `high` finds far fewer defects | + long-horizon sessions, cross-system seams, the final design verdict, spec-writing for delegates, campaign-class problems; − the most expensive row; as a delegate it sits below `opus·high` on wrong-premise architecting, chained research and review, so it buys nothing over opus there; at `low` on fresh architecting it cites work that does not exist, so `low` on any judgment lens is a challenger and never the default |
| opus | `architect` `executor` — design & execution | 9 | 5 | 4 | 4 | Anthropic · plan | 7 | execution: `medium` ambiguous, `low` clearly defined / review rung — review, red-team, critic and plan-check seats: `low`–`xhigh` by lens and depth; `low` a line-level audit lens, `medium` an ordinary review, `xhigh` a subject deep or broad enough for defects to hide / architecting: `medium`–`xhigh` by complexity and breadth / chained research: `high` — `medium` loses much of the chain | + top of the board on wrong-premise architecting, scoped architecting, thin- and thick-spec planning and chained research, and at `xhigh` on review; at the max on parity refactor and test authoring; − on fresh architecting well under sol and level with luna and muse at `max`, and a cite or two per architecting deliverable does not resolve; needs intent specified — unscoped it circles; loses the thread and makes trivial mistakes on long-horizon ambiguous tasks |
| sonnet | `fanout` — fan-out, validation, & tight exec | 5 | 6 | 6 | 7 | Anthropic · plan | 5 | `medium` audit lenses, and tight-spec execution, test authoring and debugging in sealed-era cells / `low` parity refactor and thick-spec planning, the cheapest Anthropic row near the top of each / `high` more depth; effort never rescues a plan-review lens — every rung stays far under opus there | + read-heavy surveys, authoring/execution under tight spec, validation verdicts; − lower quality on open-ended design, chained research and review; mid-board on debugging; never above `high` — `xhigh` falls below `high` on chained research and gains nothing on review; `low` effort does not think AT ALL, only for strict no-effort execution — it scores nothing on test authoring under an oracle |
| luna | test authoring, open review and fresh architecting | 6 | — | 4 | 5 | OpenAI · plan | — | `low` converged work under an oracle; `high` thick-spec planning (registry `thickPlanning`); `max` every investigative or open lens and plan drafting — below `max` it answers from the brief instead of opening the repo | + test authoring near the max at `low`; at `max` level with opus on fresh architecting, with few fabrications, and level with `opus·high` on review; − below opus and fable on wrong-premise architecting, where it cites work that does not exist; well under opus on chained research; well short on parity refactor at `low`; weak at root-cause debugging; thick-spec planning at `low` trails `sonnet·low`; unattended it stops and asks — tell it to dig and not return until done; slow for its tier, so never the arm on a wall-clock-bound lens set |
| sol | fresh architecting and thin-spec planning | 7 | — | 3 | 5 | OpenAI · plan | — | `high` — the only measured rung | + strong fresh architecting and thin-spec planning, clean cites on both; − scoped architecting less strong, under `opus·high`; chained research under `opus·medium`; slow for its tier (long turn counts on every shape), and its small per-request output makes session cost scale with context size, so drive sessions at the standard window (`gotcha_long_context_gpt_sessions_cost_by_context_size`) |
| muse | execution under an oracle and debugging | 5 | — | 4 | 10 | OpenCode · free | — | `low` convergent work under an oracle — it reaches the max on parity refactor and test authoring / `max` open judgment and debugging | + at the max on parity refactor and test authoring; near the top on loose-spec execution and debugging; level with opus on fresh architecting at `max`, with clean cites; − below opus on scoped, wrong-premise and thin-spec architecting; weak on chained research, review and thick-spec planning; its review findings include false positives, so triage every finding before acting on one |
| flash | execution and debugging under an oracle | 5 | — | 4 | 9 | DeepSeek · pay-as-you-go | — | `low` on a converged surface, `max` on an open or investigative one; `high` fabricates and is avoided | + top of the board on loose-spec execution and debugging at `max`; at or near the max on tight-spec execution, parity refactor, test authoring and thick-spec planning at `low` in sealed-era cells; above `opus·medium` on chained research; the cheapest paid row, so it routes in any band; − below `opus·high` on chained research; mid-board on wrong-premise architecting and on review, with false positives there; separates from opus wherever the surface is open judgment rather than convergence; slow for its tier, with long turn counts on loose-spec execution; one run per cell |
| qwen-local (`ai-worker`) | I/O worker | 3 | — | 6 | 10 | local · free | — | low | + every fact COPYABLE from the supplied material: digest reads, doc prose, extraction; zero cost, so it routes in every band; − anything DERIVED, ordered or chained — route that to sonnet or above |
| haiku | `scout` — read-only locate/enumerate/extract | 2 | — | 9 | 9 | Anthropic · plan | 1 | low | + read-only locate / enumerate / extract / read-and-report; − hallucinates paths; near the bottom even on thick-spec planning; scores nothing on test authoring; never for authoring a doc, patch, spec, or anything that must preserve values verbatim; never where ordering or supersession decides the answer |

**Delegate selection: Intelligence > Taste > Cost > Speed.** Read the order as a requirement, not a sort: intelligence and taste count up to the level the task needs, and above that level they buy nothing. The tier claim, the `±` cell and the effort rung set that level. Among the rows that meet it, the cheapest wins, then the fastest; cheapest is the highest `cost` value, read in the currency the budget band makes tight (`orchestration` §5b). Read as a plain sort, the order would send the most intelligent row to every task, and a board lead inside the judge band never justifies a pricier pin. On a review seat the architect tier is the floor: no cheaper row replaces it.

**A selectable registry row with `roles: []` and `effort.evidence: unmeasured` gets no row here until a battery cell scores it.** It is dispatchable by explicit alias only, never a substitute chosen by rule. deepseek `pro` is that case.

**Each row is its model family's current version.** A new version takes over its family's row and every claim in it, until a benchmark cell or a ruling shows it differs; the registry records which version each measurement came from, and the vault keeps lifetime results per version. A version gets its own row only when two versions are worth running for different work.

**Session-model selection inverts on one axis:** an orchestrator scopes, dispatches and reviews rather than executing, so at equal intelligence the *lower-`deleg`* row is the better **delegate**. Canonical pairing: orchestrator-row session, architect- and executor-row delegates.

**`agentType: 'Explore'`** composes with any pin and halves per-dispatch input cost by loading no project doctrine. Read-only locate/enumerate/extract lenses only — never a lens that must APPLY project rules. It selects an agent type, not a model; pin one beside it.

**A native Workflow agent compacts; a proxied one compacts only on an adapter build that detects the client's compaction control.** A long lane on a native pin keeps compaction enabled and needs no split. Evidence: `gotcha_native_workflow_agent_compaction`.

## Axis definitions

Higher = better on every axis.

- **intel** — hardest problem handleable unsupervised.
- **deleg** — orchestration ability: scoping, spec-writing, dispatching, reviewing returns, holding a long-horizon plan. Independent of intel; governs who RUNS a session. `—` = never a session model.
- **taste** — UI/UX, code cleanliness, API design, prose, relative to the owner's preference.
- **speed** — time to completion.
- **cost** — tokens spent per unit of result: output, uncached input and cache reads read APART (a cache read is roughly a tenth of an input token). Never dollars imputed to a plan-quota transport, never turns × minutes — those measure the loop, not the model.
- **billing** — vendor · how a call is paid: `plan` spends a subscription quota, `pay-as-you-go` spends money per token, `free` spends neither. The registry transport is the source.
- **effort** — peak quality-per-token rung per work shape; doubles as the default pin for judgment-stage `agent()` dispatches and the session `/effort` under that model. `max` is banned on Anthropic pins (cost far exceeds gain). `—` = no effort knob.
- **A tied total across effort rungs can hide opposite failure CLASSES — check which axis moved, not just the sum.** More effort can trade integrity for completeness (forcing an out-of-scope fix through by editing a pre-existing test — an automatic-fail oracle violation) rather than buying safety.

**A third pin sits beside model and effort: how much harness the child loads.** It is a dispatch-mechanism knob, not a model property; its doctrine limit lives in `orchestration` §5 *Per-dispatch harness cost*, and current costs are measured per run rather than cited from a figure.

## Candidates from external sources

Opinion, not measurement, and never a pick: `reference/model_ladder_candidates.md` holds unverified claims.
