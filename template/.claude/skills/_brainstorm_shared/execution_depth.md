# Work triage

Triage every piece of work before choosing its path. Triage records the observable fields below, and the admission table
below turns them into the process the work gets. It is the only owner of process depth: a command keeps its mandate
catalog (what each lens checks), and this file decides which elements and conditional mandates run.

## Fields

Reach and oracle are computed from paths through the sensitivity tags: the planned paths at triage, and the staged
diff at commit. Reach takes the highest value any tag sets; oracle holds every value set, and each value admits its own
rows. A changed file in `load_census.py`'s standing census, including its `@` imports, derives standing reach.
The agent may raise reach and add oracle values, never lower or remove them. The other fields are judged.

| Field | Values | Litmus |
|---|---|---|
| Reach | local · shared · standing · external | shared: an in-repo contract, serialized data whose schema or consumers change, cross-system behaviour, or guard, hook and permission config. A data-file value change whose schema and consumers the diff does not change is local. standing: always-loaded text and its imports, derived rather than declared. external: a submodule or another repo, or a published artifact |
| Change kind | decision · mechanical | decision when the change adds or changes what a model, a tool or the game does; mechanical when it does not. Mechanical edits include a rename or move, a typo fix, removing a mention of something deleted, making docs match code, and applying a verified review finding's exact replacement text (an old/new pair or literal edit) unchanged. Implementing a described fix by writing code is decision. Absent kind reads decision |
| Reversibility | clean · residue · none | The classes are in §Reversibility |
| Design | settled · open | settled: an approved design doc or plan (the owner's approval of a concrete design in the conversation counts), a known root cause, or one pattern the codebase already uses for this. Design stays open while any owner fork on it is open |
| Grounding | verified · assumed | assumed: a load-bearing fact the plan relies on was not verified first-party this session. A rich brief carries a fact only after it is verified |
| Oracle | gate · new-test · runtime · owner | runtime: only an engine boot, live launch, client run or integration run can show it. owner: feel, taste or look |
| Size | one-context · milestones · parts | Decides decomposition, the plan file and the record. It bounds the fast path and never sets depth by itself. parts: some milestone cannot be planned concretely until earlier work lands, because its plan needs an outcome (a playtest, a measurement, a landed contract), or the whole cannot be planned and reviewed as one plan. Otherwise milestones, or one-context when one sitting carries it. Decide size at triage after grounding, or as the architect pass's output after the simpler-path question; a later stage changes it only through §Re-triage, as `drive_spine.md` H2's promotion does |

Size counts plan files. A Part is one plan, with one plan check, one approval and one drive; a roadmap orders two or
more Parts. A milestone is a checkpoint inside one plan where the drive reports. A slice is one test-first change
inside a milestone, landed in one commit. A plan that grows re-plans its milestones (`drive_spine.md` H2), and its task
record is the handoff across compactions and sessions.

## Reversibility

Judge reversibility at land time.
- **clean:** `git revert` undoes it and nothing builds on it yet.
- **residue:** undoing it requires repairing existing dependent data, content or consumers. A design, roadmap Part,
  contract or task dependency counts when reverting it requires changing that dependent work, not merely because
  future work will use it. It also includes a push to an unmerged
  feature or PR branch under standing push authority, and an edit to an outward artifact (a PR body or comment, a vault
  note) or to machine state outside the repo (an engine install, global tool config) that keeps a restorable copy of
  the prior content.
- **none:** a merge or push to a primary branch, a publish (baseline publication included), a force-push, deleting
  untracked or vault content, and an outward or out-of-repo edit with no restorable copy.

Owner approval for none follows `CLAUDE.core.md` §Git's authority list.

Add a recovery mechanism only when concrete loss or dependent repair avoided justifies its implementation and
verification cost. Re-classify from the resulting state, not the mechanism's presence. Recovery never waives the
approval required for none.

## Sensitivity tags

The registry is the `sensitivity_tags` key of the project's adaptation file, which `tools/adaptation.py` loads.
- Each entry names path globs and the field and value it sets. Optional `hunk` and `file` content patterns and `status` predicates narrow matches.
- An entry that sets oracle = runtime names its probe. The probe counts only after a run on the unchanged tree shows
  it can see that defect class; until then the lookup marks it unvalidated. Before a Verify stage trusts a clean result from an unvalidated probe, it validates the probe once. It plants one instance of the tag's defect class on a throwaway tree, runs the probe, and records the run in the entry's `probe_validated`. A probe that misses its plant is a tag defect, and the drive fixes it before relying on it.

`python3 .claude/tools/triage.py lookup <paths>` prints what planned paths set, with each conditional tag listed as
"applies if". `triage.py check-registry` exits 1 on an invalid entry, and an invalid entry never matches.

## Recording the triage

Record the triage with `python3 .claude/tools/task_record.py triage <task_id> --reach R --reversibility V --design D
--grounding G --oracle O[+O] --size S --paths P... [--kind decision|mechanical] [--outward TEXT...]`.
`triage.py sentences (--staged | --rev R)` shows the diff's added and removed doctrine sentences before the kind call.
- It runs the tag lookup over `--paths` and stores the derived block with the fields.
- It refuses a recorded reach below the derived reach, or an oracle set missing a value a tag sets.
- `--outward` lists the planned outward actions (a PR body or comment edit, a vault write), which set reversibility by
  §Reversibility.
- `task_record.py block` prints the recorded `triage:` line.

On the fast path the commit trailer is the record. Put one `Triage:` line in the `-F` scratch message using
`triage.py --help`'s grammar; reversibility and kind are optional and validated when supplied.
`Triage: reach=local design=settled grounding=verified oracle=gate+new-test size=one-context kind=decision`.
The last `Triage:` line counts. Declare local, shared or external reach; standing is computed.

A command that sizes work reads the recorded fields and collects only the facts the record lacks.

The commit check derives reach and oracle from the staged diff and prints any field raised above the recorded
triage. With neither trailer nor record, it appends a distinct `Triage-Derived:` trailer containing only reach and oracle,
and speaks only above local reach. It advises; re-triage on its raise. It also
prints `candidate review owed` only for admitted mandates with changed-since-reviewed pairs, once per change of the
owed set (`tools/review_ledger.py`). Never-reviewed files alone do not trigger it. The candidate review re-derives the path-derivable fields of the commits it covers (`triage.py rederive`) and flags any
recorded value below the derived one, and runs
`task_record.py check <task_id>`, which re-resolves each recorded condition and flags one that is unresolved or that
a delegate recorded.

## Admission

### Admission table

Each element runs when its row admits it, and only then. The owner column names the command that runs it today.

| Element | Admitted when | Owner |
|---|---|---|
| Task record | size ≥ milestones, a background job, oracle = owner, owner away, reach ≥ shared, or a run condition to record (§Conditions) | `task_record.py`, `orchestration` reference `checkpointing.md` |
| Simpler-path probe | design = open and an architect pass runs; never for PROBE or PROVISIONAL items | §Simpler-path probe (the architect pass's first question) |
| Grounding | grounding = assumed; reach ≥ shared and one search cannot enumerate the consumers; the work adds an authored or shared configuration surface whose owning family has not been inventoried this session; or the work has no nameable oracle | `/explore` |
| Spike | a load-bearing fact is empirical (engine behaviour, cost, feel viability) and cheaper to measure than to reason about; retire the spike when first-party sources settle the uncertainty | the prototype route, PROBE or PROVISIONAL |
| Design | design = open, and the item is not PROBE or PROVISIONAL | `/feature_drive`'s Design capture below size = parts, `/design_drive` at size = parts, or the brainstorm skills, per §Entry routing |
| Decomposition | size = parts | `architecture_brainstorm` Part authoring, applied through `/update_roadmap` |
| Owner design decision | design = open, and the answer depends on appetite or taste (`_brainstorm_shared/appetite_invariant.md`) or commits reach = external; never for PROBE or PROVISIONAL items | the calling command's owner question |
| Design challenge | design = open and size = parts: one design doc feeds several plans, and each Part's plan check then judges only its own plan. Below parts the plan check is the design's one review. For a prototype item, when it leaves PROVISIONAL | `/architecture_brainstorm_redteam` |
| Plan file | size ≥ milestones, the executor is a cold delegate, or the Plan check row admits it | `plan_file_format.md` |
| Plan check | the plan holds a new decision no reviewer has seen (a new type, export, authored surface, family or rule), and a miss found after execution costs more than one found before it: the design is open, a miss would leave dependent state or consumers to repair after landing, a consumer of the changed contract is unenumerated or no proof would show its breakage, or the decision commits external consumers; never for PROBE or PROVISIONAL items | `/plan_check` |
| Seam review | a milestone of a plan, or a mid-chain Part, closes, and a later milestone or Part holds the consuming call site of one of its pre-agreed seams or the named consumer of one of its authored surfaces (`plan_file_format.md` §Pre-agreed seams, §Authored surfaces); for Parts, the roadmap's Part dependencies name the consumer. Consumption inside one milestone, and a drive with no milestone list, get no early review: the drive-close sweep covers them | `drive_spine.md` §Slices |
| Harness guard change | the matched set or verdict changes; RED-first scope and runtime probes follow `rules/harness_tooling.md` §Every guard has a re-runnable proof | `rules/harness_tooling.md` |
| Integration verification | oracle = runtime, or reach = external | `drive_spine.md` §Stages Verify |
| Objective pre-screen | oracle = owner, before each owner-acceptance step | the project's agent playtest command |
| Candidate review | read rigor through §Conditions Rigor, reach, change kind, size and gate result through §Candidate width; run only when width is not `none`, once the unit is stable at drive close or `/session_end` Phase 1. Skip mandates the review ledger covers | `/session_audit`, from `drive_spine.md` §Close and `/session_end` Phase 1 |
| Harness-quality review seat | `decision` only; read rigor through §Conditions Rigor. At `light`, only standing reach; at `normal`, standing reach and, for size ≥ milestones, reach ≥ shared; at `thorough`, every decision. Never for `mechanical` | `/session_audit` |
| Later round after a fold | `later_rounds.md`'s **Design no reviewer saw** class | `orchestration` §2 *Later rounds* |
| Premises block | the change rests on a claim (a cause, count or ruling); check at commit, not push | `reference/evidence_contract.md` §Load-bearing premises |
| Instruction-quality load and focused audit | a `decision` edit to a rule, guard or model-facing string | `instruction_quality` §5 |
| Owner acceptance | oracle = owner | the owner's playtest or review |
| Owner approval | the action is of the none class, whether or not the triage record names reversibility (floor) | §Reversibility |

A plan file exists because conversation context dies at compaction. A plan the Plan file row does not admit stays in
the conversation, and fast-path work needs none.

When no Plan stage is admitted, the fast path writes a brief inline scope, files, test intent and done-condition;
it needs no shaped sections or N/A lines. Shaped sections, including `plan_file_format.md`'s short-plan N/A lines,
apply only when a Plan stage is admitted. An admitted plan without a Plan check uses a short plan with no open
question, pinned per `orchestration` §5. Moving, merging or de-duplicating rules with their meaning intact adds no
new decision; verify its consumers by grep instead.

Settled, mechanical or cheaply reversible work goes straight to execution. An admitted candidate review carries the design-conformance mandate the skipped plan check would have held. Prototype work spends on building and measuring, not on a spec.

### Candidate width

`triage.admits("candidate-lenses", ...)` returns the width. Admission is policy: a mechanical change under a green gate whose size is one-context or reach is local gets `none`, no candidate review, below thorough; gate unknown counts as not green. Thorough gets `all`, the command's full roster with its conditional mandates admitted below. Every other change gets `sized`: it carries `sa-architecture-sweep` (plan conformance and executor design), and the session adds each of `sa-design-semantics`, `sa-robustness-performance` and `sa-intuitiveness-testability` whose risk the change carries, against the rigor bar in `orchestration` reference `review_width.md`'s size rule, and reports the lenses and why.

Every stage that dispatches, reviews or folds reads rigor through §Conditions Rigor; this layer's stages are in [`rigor_stages.md`](rigor_stages.md).

### Simpler-path probe

The simpler-path probe is the first required output section of the architect pass: the simplest change that meets
the requirements, and why the chosen design is or is not it. It runs only when design is open and a design stage
dispatches an architect pass, with no brief or pin of its own. The pass reads the `/explore` dossier when grounding
ran, and the repo otherwise, at its own pin (the architect cell of `reference/model_ladder_evidence.md` §Role
guidance). Grounding never waits on it.
- When the session verifies that the answer meets the oracle through a pattern the codebase already uses, design is
  settled: re-triage with `--reason "simpler-path probe"` and take the path the new fields admit.
- Otherwise the Design artifact names the answer as a candidate and why it was not taken.

Done when the task record or the Design artifact holds the architect's simplest-change answer and its disposition.

### Mandate admission

A conditional mandate runs only when the change holds the input it reads: a data-integrity mandate needs a changed
authored data file, and a playtest mandate needs a player-facing change. Each command keeps the mandate text; this
table holds when each conditional one runs.

| Command | Conditional mandate or mode | Admitted when |
|---|---|---|
| `/architecture_brainstorm_redteam` | `rt-systemic` | adjacent committed design docs or roadmaps exist in the touched domains |
| `/architecture_brainstorm_redteam` | `rt-vision` | the design is user-, player- or designer-facing (mechanics, feel, content, UI, authoring surfaces), or a task requirement names a quality the design's users must get |
| `/idea_brainstorm_fanout` | the fan-out Diverge | design = open, and the cluster's design axis has orthogonal sub-shapes a single pass under-populates |
| `/research` | Shape B | the question rests on an external fact with grounding = assumed and reach ≥ shared or design = open; the fact is version-contested, or first-party and community sources are expected to disagree; or a Shape A answer came back unclear or contested, which is a re-triage |
| `/session_audit` | `sa-architecture-sweep` | at drive close, whenever §Candidate width admits review (`sized` or `all`); otherwise design = open, reach ≥ shared, a new authored surface, or the invoking step names it, subject to the Candidate review row |
| `/session_audit` | `sa-harness-quality` | the change adds or changes a decision in standing or shared harness text, or rigor through §Conditions Rigor is thorough; apply the Harness-quality review seat row's kind, size, reach and light-run limits |
| `/session_audit` | `sa-holistic` | at `/session_end`, the session landed work no single review saw together: `review_ledger.py whole-scope` returns `wholeScope: false` for the session's files |
| `/pr_ready` | the parity and API lenses | the change holds code or authored data; a meta-only change holds no input for them |
| `/session_audit` and the review commands | the code-quality and test-quality checklists | an admitted review changes code or tests those checklist items check; load only the applicable sections |
| the prototype route | the composed-encounter verification obligations | the question has an open fork (design = open), or its answer spans more than one mechanism (oracle = runtime) |

### Entry routing

Pick the entry command from the fields; the first matching row wins. A fork that needs the owner's taste is a gate
inside the chosen route (the Owner design decision row), never a reason to change route.

| Fields | Entry |
|---|---|
| an approved plan file exists | `/part_execute`, which enters at execute |
| the work is a roadmap Part | `/part_drive`; `--plan-only` stops at an approved plan |
| the work is logged worklog items | `/worklog drive` |
| design = open, and no candidate approaches exist yet | `/idea_brainstorm` |
| the owner asks to design in dialogue | `architecture_brainstorm`, optionally `--red_team` |
| size = parts with an approved design doc | the Decomposition row, then `/part_drive` per Part |
| size = parts | `/design_drive`: one design doc, its design challenge and roadmap, then a plan and plan check per Part |
| size below parts | `/feature_drive`: `/explore`, an architect pass when design = open, then one plan and its plan check when admitted |

A worklog item's `scope:` value maps to the fields and never sets depth by itself. Scope 1 and 2 read as one-context
with design = settled. Scope 3 reads as one-context or milestones, with design = open until its cross-file trade-off
resolves. Scope 4 reads as design = open, and as size = parts only when the Size litmus holds.
Triage the item's other fields from its own facts.

## Paths are outputs

The fast path is local, clean, settled, verified, one context, with a gate or new-test oracle: failing test, fix,
gate, commit with its trailer, in session. The standard, designed and Parts paths follow from the admission table.
Each milestone of a plan is triaged again, and the drive reports to the owner at each milestone boundary, not per task, and continues.

## Re-triage

Re-triage when a fact changes, with `task_record.py triage --reason`:
- a premise fails verification;
- the diff leaves the declared scope or touches a tagged path;
- an owner design decision changes a field, such as a fork answered toward external reach;
- the gate fails because of the change or its merge-base move;
- a delegate escalates;
- a drive resumes in a new session, which re-triages to bind its record.

A gate failure outside the change, including a red already on the target branch, goes to the gate's own adjudication
(fix now, known issue or abort) or into the merge approval. It re-triages only on fix now. Re-triage can lower
process as well as raise it.

## Readiness

Work with no nameable oracle is not ready. It goes to grounding, a spike or reshaping.

## Conditions

Conditions describe the run, not the work. They adjust when an admitted review runs, its width and its mandates, and the bar its findings clear. None waives a floor element (§Boundary's quality floor) or an owner approval.

### Rigor

Recording and reading the level, the bar, every panel's rule and the shared stages are `orchestration` reference `rigor.md`'s. The floor (§Boundary) never scales with it, and the Do-now gate's "declined with a reason and an offer" (`worklog_reference`) is the close of a below-bar finding. What rigor changes at each stage this layer adds is [`rigor_stages.md`](rigor_stages.md)'s table.

### Other conditions

- **Room:** room is low when `python3 .claude/tools/provider_bands.py room <tier> --json` returns `low: true`, read across every plan paying for the tier's rows, never from the transport an agent chose. Low room narrows a panel (`orchestration` §2) and waits for a window with room; work that does not depend on the waiting review proceeds. Room never moves an admitted pre-code review after the code and never lowers a lens's tier or effort.
- **Models:** the review a model's work gets follows its evidence, keyed by the declared tier and the domain (the triage tag names, or `untagged`). The evidence is its ladder row and its rated dispatch verdicts (`/orchestration_metrics`) and, for inline work, its ledger outcomes and escape rows by author model (`review_ledger.py rows`, `escape_log.py list`). A driver note or rail tier compensates a recorded weakness, and a weakness admits the mandate that covers it. Probation (`orchestration` reference `model_evidence.md`) runs the spine's spec verifier. The same evidence sizes delegation: a thinner brief and fewer handoffs for work the evidence covers.
- **Urgency:** an owner-stated deadline trades latency, never coverage. An admitted pre-code review runs beside execution, and the commit waits on its verdict.
- **Owner requests:** an explicit owner request to add or drop process overrides admission, except floor elements and owner approvals.
- **Spend:** the owner's lean condition makes cost the row bar; it changes pins, never fields, admission or width (`orchestration` reference `spend_lean.md`).

Record each deadline and request with `python3 .claude/tools/task_record.py condition <task_id> --kind urgency|request --quote "<the owner's words>" --effect "<what it changes>"`. The tool stores the id of this session's owner turn that holds the quote and refuses when none does. A delegate's context holds no owner turns, so a condition a delegate records is a finding.

## Boundary

- Commands keep their mandate catalogs; this file admits elements and conditional mandates, never rewrites a
  mandate. `orchestration` §2 seats one per admitted mandate and narrows only by its size rule, §Conditions Room, the review ledger's coverage and, for a later round, *Later rounds* (`orchestration` §2).
- Triage decides whether grounding runs. Lens selection and evidence reuse follow [`explore_agents.md`](../../commands/agents/explore_agents.md) §Lens Roster & Trigger Rules.
- The quality floor never scales: peer work, owner authority for irreversible or published actions, RED-first for Logic code (`CLAUDE.md` §Hybrid TDD), and the project's regression gate (`change_control` §Gate cadence names it), including its new-red release block. A blocking guard that protects this floor binds at every triage; a process guard binds per its admission.
- A stronger or cheaper model changes the pins on the admitted elements, never the fields; the review it gets changes only through §Conditions Models.
