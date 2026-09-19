# Model Ladder

**The role → model ladder. Load it whenever you pin a model or an effort.** §Role guidance is the only home for which model fills which role and what each is for / never for. Dispatch *rules* are `orchestration` §5; CLAUDE.md §Model Delegation keeps the copyable-vs-derived route and the which-currency rule; how to launch a sidecar is `reference/sidecar_dispatch.md`.

**Authoring an entry:** the reader is an orchestrator picking a model and an effort at a glance. Lead with the routing verdict. State capability in the instrument's own terms (its score, its fabrication count), never relative to another row — rows retire.

**The `±` and `effort` cells state what the model TENDS to do — never what a run of it did.** A comparison earns an edit only once it converts into a tendency: *"tops the board on scoped architecting, cites cleanly there"*, not *"53/77 with 0 contradicted cites"*. So neither cell carries a score, a task id, a date, an arm count, a wall-clock figure, a multiplier, a run description or a link to a per-run write-up. One incident that recurs is a tendency and goes in as one; one that does not recur belongs nowhere. Every ingest rewrites an existing clause or adds none. `tools/ladder_prose_check.py` fails on all of it in **both** columns — bind one and the figures move one cell left.

**Availability is not authored here.** `reference/external_models.json` holds it; `python3 .claude/tools/model_registry.py available` enumerates. An excluded model is out of consideration — re-select from what remains.

**A selectable model with no row here is UNMEASURED, not forgotten** (today: `terra`). It claims no role and nothing has scored it, so pinning one is a bet — run `/pin_ab` and give it a row, or take a row that has one.

Benchmark results (Obsidian, `Claude/Meta/Benchmark/`): `model-effort-v1.0/REPORT.md` (7-task cross-domain battery), `results/SCORES-*.md` (sidecar campaigns), `Model Effort Calibration Baseline.md`. **Every BENCHMARKED claim below traces there by task id**, which is how §Pick by work shape keys its rows — and why no `±` or `effort` cell repeats a figure. Rows that make no benchmark claim (`qwen-local`, availability, tier metadata) trace to `external_models.json` instead. Read the boards only when this file cannot settle the pick.

---

## Pick by work shape

Measured verdicts from the battery (task ids point at the Obsidian boards). Read this first; the role table below says what each row is for. **The two scoped-architecting rows split on the PREMISES**: a brief whose stated premises are wrong or self-contradicting is the T12 row; an ordinary thin-spec design is the T3/T5 row.

**Reading the boards, once:** figures never pool across instrument generations (`[inst 1.1]`, `[inst 1.2]`, `[inst 1.3]` are different instruments), a broad-T2 number is a recall probe and not a ranking, and a VOID-across-frames row is not citable. These govern the boards, not the pick — they are here so no `never` cell has to repeat them. A pin carrying a bracketed transport name — `[codex]`, `[opencode]` — is NOT on Anthropic: reaching it from an Anthropic session is a sidecar hop. An untagged pin is Anthropic. `model_registry.py for-role <tier>` prints the hop command.

| work shape | pin | also | never |
|---|---|---|---|
| deep review / red-team / adversarial plan review (T11) | `opus·xhigh`, and the same in both runs; the gain tracks turns SPENT rather than tier — an engagement effect | `luna·max` [codex] — near the top of the board, and it spends CODEX quota rather than Anthropic's, never nothing | opus at `high` or below — the last rung buys the whole gain; sonnet at any rung; fable, which stops early at either rung and contradicts a cite each way |
| scoped architecting against wrong premises (T12) | `opus·high` tops the board and is the executor pick; `fable·high` sits one step below it, so the orchestrator tier buys nothing here | `muse·max` [opencode] — free, mid-board, no contradicted cite | `opus·xhigh` — below `high` by more than the judge band, at twice the tokens; sonnet — no rung reaches the leading arms; luna — it contradicts its own cites here |
| expansive fresh architecting (T2 / T2N) | `sol·high` [codex] — inside the band of the board's top arm, far above every Anthropic arm, clean cites, on CODEX quota; `opus·high` is the on-transport pick, and `xhigh` lands on the same score. The T2N key ranks arms on the NECESSARY decisions; `astra·high` [codex] tops it and is excluded | `muse·max` [opencode] — free and above every Anthropic arm, no contradicted cite; `luna·max` [codex] ties opus at `max` only | luna below `max` — the lower rungs fall away; `fable·low` — it contradicts its own cites here (archive T2) |
| chained research / cross-doc synthesis (T1) | `opus·high` — the rung buys the chain, and `opus·medium` keeps barely two-thirds of it | `fable·high` (sealed-era, in-sample anchor) | opus below `high`; astra, muse and `sol·high` [codex], all under `opus·medium`; the small opencode arms, far under |
| root-cause debugging with fix + test (T8) | `muse·max` [opencode] — near the top of the current instrument and free; `astra·high` [codex] alone clears it and is excluded. Opus and fable are UNMEASURED on the current instrument — their sealed-era tie with `sonnet·medium` is archive | `sonnet·high` — mid-board, the only Anthropic row measured here | luna — half the board; the small opencode arms score nothing |
| parity refactor (T9) and test authoring under a mutation oracle (T10) | ceilinged: `sonnet·low` reaches the max on the parity refactor and is the cheapest Anthropic row there; on test authoring `opus·high` is the cheapest Anthropic row at the max | `muse·low` [opencode] — free, at the max on both | `sonnet·low` or haiku on test authoring — both score NOTHING there (a no-effort rung does not write a suite); buying effort on the parity refactor |
| converged tight-spec execution (T6) | UNMEASURED on the current instrument — take the T9 pick | — | reading the sealed-era tie as current |
| loose-spec execution (T7) | RANKS arms and nobody reaches the scale maximum, so pick by the board; `opus·xhigh` leads it (sealed-era) | — | reading it as ceilinged |
| thick-spec planning (T4) | RANKS arms: `opus·medium` tops it, `sonnet·low` close behind is the cheapest competent pick, luna needs `high`. Nonexistent cites sit BESIDE the figure, never inside it — read the fabs column | `muse·max` [opencode] — free, a tier below | a luna draft at `low` — it answers from the brief, every count `unknown`; haiku — bottom of the board; calling it ceilinged (only its v1.x binary axes were) |
| scoped architecting / thin-spec planning (T3/T5) | RANK arms and nobody sits at the top — pick by the board, never by "any row": `opus·high` leads scoped architecting | `sol·high` [codex] — top of the thin-spec board and a step under `opus·high` on scoped architecting, clean cites, on CODEX quota; `muse·max` [opencode] — free, a tier below | calling them ceilinged (only their v1.x binary axes were) |
| multi-arm plan-check on an architectural plan | `opus·low` is the canonical arm — it carries the run and its criticals hold up | `muse·max` [opencode] as a second arm: free, fast, and it folds in defects the canonical arm misses | `sonnet·medium` — lenses come back empty or as subsets of the opus arm |
| post-execution line-level audit after executor slices | `opus·low` robustness + design lenses — they find critical defects that both the slices' own green reports and the plan-check missed | `sonnet·medium` testability lens | skipping it because every slice reported green — that is the state it exists to catch |

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
| fable | `orchestrator` — ideal design/architecture, elegant creative | 9 | 9 | 3 | 2 | 9 | `high` default / `medium` guiding a long-horizon session / `low` converged spec, anchored lens, enumerable survey — but never `low` on a turn that must verify the current state of a named thing / `xhigh` only for a SHORT capability-sensitive one-off, never a long prose deliverable, where `high` beats it | + long-horizon sessions, cross-system seams, the ideal-design verdict, spec-writing for delegates, campaign-class problems; − the most expensive row, and less thorough drilling into one narrow topic; at `low` on fresh architecting it cites work that does not exist, so `low` on any judgment lens is a challenger and never the default |
| opus | `executor` — architect & executor | 8 | 5 | 4 | 4 | 7 | `xhigh` hardest design, buried-fork verification, and every deep review / red-team / adversarial plan-review lens — below it most planted defects go unfound, and `max` buys nothing further; NOT for scoped or fresh architecting, where `xhigh` finds no more than `high` (less, against wrong premises) and costs far more / `high` chained research — `medium` loses much of the chain / `high` while ambiguous / `medium` exec / `low` tight specs | + scoped work that fits one context window: coding/execution, scoped architecture, red-team, debugging; − a cite or two per architecting deliverable does not resolve — run the citation verifier; needs intent specified — unscoped it circles; loses the thread and makes trivial mistakes on long-horizon ambiguous tasks |
| sonnet | `fanout` — fan-out, validation, & tight exec | 5 | 6 | 6 | 7 | 5 | `high` more depth / `medium` tight spec; effort never rescues a plan-review lens — every rung stays far under the executor tier there | + read-heavy surveys, authoring/execution under tight spec, validation verdicts, thick-spec planning at `low` — the cheapest competent row there; − lower quality on open-ended design; never above `high` (effort INVERTS); `low` effort does not think AT ALL, only for strict no-effort execution — it scores nothing on test authoring under an oracle |
| gpt-5.6-luna (sidecar) | scoped planning/architecture/review + spec-tight execution | 6 | — | 4 | 5 | — | `low` converged / tight-spec execution; `high` thick-spec planning (registry `thickPlanning`); `max` every investigative or open lens and plan drafting — below `max` it answers from the brief instead of opening the repo | + scoped architecting, thick planning, tight-spec implementation, test authoring, parity/consolidation review; low fabrications on fresh architecting; audits at design altitude and follows a consequence across modules, returning ranked migration options and a stated `couldNotSatisfy`; − below every Anthropic row on scoped and fresh architecting, where it also contradicts its own cites, and no rung fixes it; weak at root-cause debugging and loose-spec execution; unattended it stops and asks — tell it to dig and not return until done; parity-lens severities need re-triage; slow for its tier, so never the arm on a wall-clock-bound lens set |
| gpt-5.6-sol (sidecar, codex) | expansive + scoped architecting, thin-spec planning | 7 | — | 3 | 5 | — | `high` — the only measured rung | + expansive fresh architecting near the top of the board and far above opus, thin-spec planning at the top, scoped architecting a step under the executor pick — clean cites on all three; spends CODEX quota, so it is the pick for those shapes whenever Anthropic is the tight currency; − chained research mid-board, under `opus·medium`; slow for its tier (long turn counts on every shape); unmeasured on review, debugging and execution — take it for the three shapes above, not as a general executor |
| gpt-6-astra (sidecar, codex) `excluded` | review, debugging, expansive architecting | 8 | — | 5 | 6 | — | `high` — the only measured rung | + top of the review board at `high` beside opus·xhigh, the only arm past the debugging floor with a real distance sweep, and top of the fresh-architecting ranking with clean cites; − mid-board on scoped architecting against wrong premises, below opus on chained research, asserts every finding flat with no severity grading; **excluded as a DELEGATE, still launchable as a session driver** — availability gates dispatch, never the driver |
| muse-spark-1.3 (sidecar, opencode) | sonnet-tier architecting + execution | 5 | — | 4 | 10 | — | `max` everywhere — the only rung with scorable evidence, so a lower pin is a guess, not a saving | + ties gpt-5.6-luna across planning and debugging, and sits beside opus·high on scoped and fresh architecting with clean cites on either; fans out wide without trouble; the fastest and cheapest review arm on the roster; − weak on chained research; that planning tie rests on an open instrument gap, so read it as unconfirmed rather than as parity; its failure mode is false positives, never misses — recall is real and precision is not, so triage every finding before acting on one |
| qwen-local (`ai-worker`) | I/O worker — free, local | 3 | — | 6 | 10 | — | low | + every fact COPYABLE from the supplied material: digest reads, doc prose, extraction; zero cost, so it routes in every band; − anything DERIVED, ordered or chained — route that to sonnet or above |
| haiku | `scout` — read-only locate/enumerate/extract | 2 | — | 9 | 9 | 1 | low | + read-only locate / enumerate / extract / read-and-report; − hallucinates paths; bottom of the board even on thick-spec planning; never for authoring a doc, patch, spec, or anything that must preserve values verbatim; never where ordering or supersession decides the answer |

**A selectable registry row with `roles: []` and `effort.evidence: unmeasured` gets no row here until a battery cell scores it.** It is dispatchable by explicit alias only, never a substitute chosen by rule. deepseek `flash` (DeepSeek-V4.1-Flash) and `pro` are that case; the retired V4-Flash-0731 figures stay on the boards as a different version.

**Delegate selection: Intelligence > Taste > Cost > Speed.** Cost breaks a tie once intelligence and taste are satisfied, or outranks them when the budget band is tight.

**Session-model selection inverts on one axis:** an orchestrator scopes, dispatches and reviews rather than executing, so at equal intelligence the *lower-`deleg`* row is the better **delegate**. Canonical pairing: orchestrator-row session, executor-row delegates.

**`agentType: 'Explore'`** composes with any pin and halves per-dispatch input cost by loading no project doctrine. Read-only locate/enumerate/extract lenses only — never a lens that must APPLY project rules. It selects an agent type, not a model; pin one beside it.

**A native Workflow agent compacts; a proxied one compacts only on an adapter build that detects the client's compaction control.** A long lane on a native pin keeps compaction enabled and needs no split. Evidence: `gotcha_native_workflow_agent_compaction`.

## Axis definitions

Higher = better on every axis.

- **intel** — hardest problem handleable unsupervised.
- **deleg** — orchestration ability: scoping, spec-writing, dispatching, reviewing returns, holding a long-horizon plan. Independent of intel; governs who RUNS a session. `—` = never a session model.
- **taste** — UI/UX, code cleanliness, API design, prose, relative to Jacob's preference.
- **speed** — time to completion.
- **cost** — tokens spent per unit of result: output, uncached input and cache reads read APART (a cache read is roughly a tenth of an input token). Never dollars imputed to a plan-quota transport, never turns × minutes — those measure the loop, not the model.
- **effort** — peak quality-per-token rung per work shape; doubles as the default pin for judgment-stage `agent()` dispatches and the session `/effort` under that model. `max` is banned on Anthropic pins (cost far exceeds gain). `—` = no effort knob.
- **A tied total across effort rungs can hide opposite failure CLASSES — check which axis moved, not just the sum.** More effort can trade integrity for completeness (forcing an out-of-scope fix through by editing a pre-existing test — an automatic-fail oracle violation) rather than buying safety (`opencode-muse-campaign-2026-09-03` T7 low/max, identical 18/22 via opposite axes).

**A third pin sits beside model and effort: how much harness the child loads.** It is a dispatch-mechanism knob, not a model property; its doctrine limit lives in `orchestration` §5 *Per-dispatch harness cost*, and current costs are measured per run rather than cited from a figure.

## Candidates from external sources

Opinion, not measurement, and never a pick: `reference/model_ladder_candidates.md` holds the claims with the measurement that would verify each. A claim reaches this file only as a `±` tendency once that measurement lands.
