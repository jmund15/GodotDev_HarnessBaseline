# Model Ladder

**The role → model ladder. Load it whenever you pin a model or an effort.** §Role guidance is the only home for which model fills which role and what each is for / never for. Dispatch *rules* are `orchestration` §5; CLAUDE.md §Model Delegation keeps the copyable-vs-derived route and the which-currency rule; how to launch a sidecar is `reference/sidecar_dispatch.md`.

**Authoring an entry:** the reader is an orchestrator picking a model and an effort at a glance. Lead with the routing verdict. State capability in the instrument's own terms (its score, its fabrication count), never relative to another row — rows retire.

**The `±` and `effort` cells state what the model TENDS to do — never what a run of it did.** A comparison earns an edit only once it converts into a tendency: *"tops the board on scoped architecting, cites cleanly there"*, not *"53/77 with 0 contradicted cites"*. So neither cell carries a score, a task id, a date, an arm count, a wall-clock figure, a multiplier, a run description or a link to a per-run write-up. One incident that recurs is a tendency and goes in as one; one that does not recur belongs nowhere. Every ingest rewrites an existing clause or adds none. `tools/ladder_prose_check.py` fails on all of it in **both** columns — bind one and the figures move one cell left.

**Availability is not authored here.** `reference/external_models.json` holds it; `python3 .claude/tools/model_registry.py available` enumerates. An excluded model is out of consideration — re-select from what remains.

**A selectable model with no row here is UNMEASURED, not forgotten.** It claims no role and nothing has scored it, so pinning it is a bet: run `/pin_ab` and give it a row, or take a row that has one.

Detailed measurement reports stay outside the published baseline. Record only stable routing tendencies here; do not publish private report roots, run ids, task ids, dates, or campaign names.

---

## Pick by work shape

These rows record stable routing tendencies. A pin carrying a transport label such as `[codex]` or `[opencode]` needs that transport or a sidecar hop. An untagged pin is Anthropic. `model_registry.py for-role <tier>` prints the current route.

| work shape | pin | also | never |
|---|---|---|---|
| deep review / red-team / adversarial plan review | `opus·xhigh`; the gain tracks turns spent rather than tier | `luna·max` [codex] when Codex quota is the intended currency | opus at `high` or below; sonnet at any rung; fable, which stops early |
| scoped architecting against wrong premises | `opus·high` is the executor pick; `fable·high` when the orchestrator tier is open | `muse·max` [opencode] — lower cost with clean citations | sonnet, which stays a tier below; luna, which can contradict its own citations here |
| expansive fresh architecting | `opus·xhigh` is the on-transport pick | `muse·max` [opencode] as a lower-cost challenger | luna at any rung; `fable·low` |
| chained research / cross-doc synthesis | `opus·high`; lower effort loses much of the chain | `fable·high` | opus below `high`; rows that fail to preserve the source chain |
| root-cause debugging with fix + test | `opus·xhigh`, `sonnet·medium`, or `muse·max` [opencode] meet the capability floor | choose by currency once the floor is met | luna, which misses much of the chain |
| converged execution, parity refactor, test authoring | the cheapest competent row: `sonnet·medium` or `muse·max` [opencode] | — | buying extra effort without residual ambiguity |
| loose-spec execution | `opus·xhigh`; the task is not ceilinged | — | treating a loose spec as mechanical execution |
| thick-spec planning and scoped thin-spec planning | `opus·medium` or `sonnet·low`; luna needs `high` for thick specs | `muse·max` [opencode] as a lower-cost challenger | a luna draft at `low`; haiku; calling the task ceilinged |
| multi-arm plan-check on an architectural plan | `opus·low` is the canonical arm | `muse·max` [opencode] as an independent second arm | `sonnet·medium` as the only arm |
| post-execution line-level audit after executor slices | `opus·low` for robustness and design lenses | `sonnet·medium` for a testability lens | skipping it because each slice reported green |

## Role definitions

Four tiers, named by WORK SHAPE so the name survives model churn. The `role` cell of every Anthropic
row below opens with its tier token; `python3 .claude/tools/model_registry.py for-role <tier>` reads
those tokens and prints the rows that serve the tier on this seat and across the hop. What each tier
is FOR and when to trade between them: `orchestration` §5.

| tier | work shape |
|---|---|
| `orchestrator` | scoping, dispatching, reviewing across systems; long-horizon sessions; the ideal-design verdict |
| `executor` | anything a wrong answer only out-reasoning would catch: SCOPED architecting, red-team, root-cause, fix authorship. Cross-domain architecting is not scopable and stays at `orchestrator` |
| `fanout` | read-heavy and mechanical work: surveys, enumeration, text comparison, rubric checks, tight-spec execution |
| `scout` | locate / enumerate / extract, verifiable without applying project doctrine |

`validation` is not a fifth tier — it is the `fanout` row at a lower effort pin, and `for-role
validation` says so rather than implying a distinct row.

## Role guidance

Roles are relative to whichever model runs the session. `±` = what to pin the row for / what never to.
Each Anthropic row's `role` cell opens with its tier token (§Role definitions); a non-Anthropic row
carries no token — its tier claims live in `external_models.json` `roles`, earned by battery evidence.

| model | role | intel | deleg | speed | cost | taste | effort | ± |
|---|---|---|---|---|---|---|---|---|
| fable | `orchestrator` — ideal design/architecture, elegant creative | 9 | 9 | 3 | 2 | 9 | `high` default / `medium` guiding a long-horizon session / `low` converged spec, anchored lens, enumerable survey — but never `low` on a turn that must verify the current state of a named thing / `xhigh` only for a SHORT capability-sensitive one-off, never a long prose deliverable, where `high` beats it | + long-horizon sessions, cross-system seams, the ideal-design verdict, spec-writing for delegates, large cross-cutting problems; strong on scoped architecting against wrong premises; − the most expensive row, and less thorough drilling into one narrow topic; at `low` on fresh architecting it may cite work that does not exist, so `low` on any judgment lens is a challenger and never the default |
| opus | `executor` — architect & executor | 8 | 5 | 4 | 4 | 7 | `xhigh` hardest design, buried-fork verification, and every deep review / red-team / adversarial plan-review lens — below it review coverage falls sharply, and `max` buys nothing further; NOT for scoped architecting, where `xhigh` finds no more than `high` and costs far more / `high` chained research — `medium` loses much of the chain / `high` while ambiguous / `medium` exec / `low` tight specs | + scoped work that fits one context window: coding/execution, scoped architecture, red-team, debugging; − a cite or two per architecting deliverable does not resolve — run the citation verifier; needs intent specified — unscoped it circles; loses the thread and makes trivial mistakes on long-horizon ambiguous tasks |
| sonnet | `fanout` — fan-out, validation, & tight exec | 5 | 6 | 6 | 7 | 5 | `high` more depth / `medium` tight spec; effort never rescues a plan-review lens — every rung stays far under the executor tier there | + read-heavy surveys, authoring/execution under tight spec, validation verdicts, thick-spec planning at `low` — the cheapest competent row there; − lower quality on open-ended design; never above `high` (effort INVERTS); `low` effort does not think AT ALL, only for strict no-effort execution |
| gpt-5.6-luna (sidecar) | scoped planning/architecture/review + spec-tight execution | 6 | — | 4 | 5 | — | `low` converged / tight-spec execution; `high` thick-spec planning (registry `thickPlanning`); `max` every investigative or open lens and plan drafting — below `max` it answers from the brief instead of opening the repo | + scoped architecting, thick planning, tight-spec implementation, test authoring, parity/consolidation review; low fabrications on fresh architecting; audits at design altitude and follows a consequence across modules, returning ranked migration options and a stated `couldNotSatisfy`; − below every Anthropic row on scoped and fresh architecting, where it also contradicts its own cites, and no rung fixes it; weak at root-cause debugging and loose-spec execution; unattended it stops and asks — tell it to dig and not return until done; parity-lens severities need re-triage; slow for its tier, so never the arm on a wall-clock-bound lens set |
| gpt-6-astra (sidecar, codex) `excluded` | review, debugging, expansive architecting | 8 | — | 5 | 6 | — | `high` — the only supported rung | + strong review, debugging, and expansive architecting; − weaker on scoped architecting against wrong premises and chained research; asserts findings flat with no severity grading; **excluded as a DELEGATE, still launchable as a session driver** — availability gates dispatch, never the driver |
| muse-spark-1.3 (sidecar, opencode) | sonnet-tier architecting + execution | 5 | — | 4 | 10 | — | `max` everywhere — a lower pin is unsupported, not a saving | + low-cost scoped and fresh architecting, planning, debugging, and wide fan-out; − weak on chained research; false positives are its common failure mode, so triage every finding before acting |
| qwen-local (`ai-worker`) | I/O worker — free, local | 3 | — | 6 | 10 | — | low | + every fact COPYABLE from the supplied material: digest reads, doc prose, extraction; zero cost, so it routes in every band; − anything DERIVED, ordered or chained — route that to sonnet or above |
| haiku | `scout` — read-only locate/enumerate/extract | 2 | — | 9 | 9 | 1 | low | + read-only locate / enumerate / extract / read-and-report; − hallucinates paths; never for authoring a doc, patch, spec, or anything that must preserve values verbatim; never where ordering or supersession decides the answer |

**deepseek flash and pro have no rows because their transport is unavailable.** Restore a row only alongside the registry entry that makes it selectable again.

**Delegate selection: Intelligence > Taste > Cost > Speed.** Cost breaks a tie once intelligence and taste are satisfied, or outranks them when the budget band is tight.

**Session-model selection inverts on one axis:** an orchestrator scopes, dispatches and reviews rather than executing, so at equal intelligence the *lower-`deleg`* row is the better **delegate**. Canonical pairing: orchestrator-row session, executor-row delegates.

**`agentType: 'Explore'`** composes with any pin and halves per-dispatch input cost by loading no project doctrine. Read-only locate/enumerate/extract lenses only — never a lens that must APPLY project rules. It selects an agent type, not a model; pin one beside it.

## Axis definitions

Higher = better on every axis.

- **intel** — hardest problem handleable unsupervised.
- **deleg** — orchestration ability: scoping, spec-writing, dispatching, reviewing returns, holding a long-horizon plan. Independent of intel; governs who RUNS a session. `—` = never a session model.
- **taste** — UI/UX, code cleanliness, API design, and prose relative to the maintainer's stated preference.
- **speed** — time to completion.
- **cost** — tokens spent per unit of result: output, uncached input and cache reads read APART (a cache read is roughly a tenth of an input token). Never dollars imputed to a plan-quota transport, never turns × minutes — those measure the loop, not the model.
- **effort** — peak quality-per-token rung per work shape; doubles as the default pin for judgment-stage `agent()` dispatches and the session `/effort` under that model. `max` is banned on Anthropic pins (cost far exceeds gain). `—` = no effort knob.
- **A tied total across effort rungs can hide opposite failure classes.** Check which axis moved, not just the sum. More effort can trade integrity for completeness rather than buying safety.

**A third pin sits beside model and effort: how much harness the child loads.** It is a dispatch-mechanism knob, not a model property, so its costs and its one doctrine limit live in `orchestration` §5 *Per-dispatch harness cost*.
