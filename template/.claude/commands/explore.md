---
allowed-tools: Bash(git ls-files:*), Bash(git ls-tree:*), Bash(git branch:*), Bash(git grep:*), Bash(git check-ignore:*), Bash(node:*), Bash(bash:*), Glob, Grep, Read, Write, Workflow, Task
description: Pre-design exploration fan-out — evidence-backed claims dossier on the CURRENT state of code, memory, docs, harness, and tests. Two floor lenses plus trigger-gated ones; advisory, emits no verdict.
---

Establish what IS, before anything proposes what should be. `/explore` fans out discovery lenses over a topic and returns a **claims dossier** — each claim carrying its polarity, its verbatim evidence, and how it bears on the work: a premise the code refutes, a family that already owns the concern, a constraint to respect, a consumer that would break.

**This command emits no verdict and gates nothing.** `/plan_check` judges a plan; there is no plan yet. The dossier's job is to make the plan that follows correct on the first draft, and to surface premise contradictions while they still cost nothing to absorb.

## When to invoke

Fired by the drive commands and both brainstorm skills at their context-gathering step (`feature_drive` 1, `part_drive` 2 — in both its modes, `design_drive` preflight, `_brainstorm_shared/common.md` §1). Standalone whenever a topic is nameable but the design is not yet drafted.

**SKIP** when the topic is a single mechanical fix with a known root cause, or when a `/explore` dossier for the same topic already exists this session — re-run only the lenses whose surface has since changed.

### Modes

`/explore "<topic>"` — the default sweep described below.

`/explore --make-this-easy "<upcoming change>"` — same sweep, plus `exp-change-ease`, which asks *what would make THIS change easy?* against only the subsystems the change touches. A different question from "what exists": it inventories the friction the change would have to fight, the seams it could ride, and the invariants it must not disturb. Requires a named change — `--make-this-easy` with a system name and no change is refused the same way 1a refuses a vague `TOPIC`, because "make this subsystem better" has no bounded surface and returns generic cleanup.

## Composition with other audits

| Lifecycle stage | Tool | What it covers |
|---|---|---|
| Pre-design (no plan yet) | `/explore` | Current state: memory, prior art, blast radius, design source, harness, external APIs, test/data reality |
| Any stage, external fact | `/research` | What an engine, library, or spec actually does, cited and tiered ([`source_trust.md`](../rules/source_trust.md)) |
| Plan-entry (roadmap Part) | `/plan_part` | Design surface load verbatim + drift classification |
| Plan-time (plan drafted) | `/plan_check` | Does the plan walk into a gotcha, parallel an abstraction, ship untestable |
| Post-implementation | `/session_audit`, `/pr_ready` | Code-quality lenses, parity, doc coverage |

`/explore` is **upstream** of `/plan_check` and feeds it directly: `constraint` claims populate the plan's `Constraints` heading, and `reuse-candidate` claims pre-answer `plc-pattern-fit`. A plan whose Constraints section cites dossier claims lets plan-check's lenses verify against evidence rather than re-deriving it.

---

## Phase 1: Scope & Seed

### 1a. State the topic

One paragraph: what is about to be designed or planned, in the caller's words. Store as `TOPIC`. A vague topic yields a vague dossier — if `TOPIC` names no system, file, or capability, say so and ask for one sentence more rather than dispatching.

### 1b. Read the budget band FIRST

Find the most recent `[budget-posture]` line. Provider choice precedes every other pin, and an unread band silently bills the whole floor to plan quota with nothing downstream to detect it. Absent a hook line, read `<TEMP>/cc-cachestat-<session_id>.json`. Record the band.

### 1c. Infer domains

Case-insensitive keyword match of `TOPIC` against the CLAUDE.md *Proactive Context Loading* table (the same table `plan_memory_reminder.py` mirrors). Build `INFERRED_DOMAINS`. Unlike `/plan_check`, an empty domain list does **not** abort — `exp-prior-art` and `exp-harness-governance` still apply to a topic that matches no gameplay domain.

### 1d. Evaluate lens triggers as rules

Walk the trigger table in [`explore_agents.md`](agents/explore_agents.md) against `TOPIC`. The two floor lenses always run. For each triggered lens, record which clause fired — the report states it, so lens selection is auditable rather than a judgment nobody can check.

In `--make-this-easy` mode, `exp-change-ease` is added on top of whatever the table selected; it never replaces a lens. Friction claims are only actionable read against the prior-art and blast-radius claims that say what already exists and who breaks.

### 1e. Assemble CONTEXT

Write ONE scratchpad file, passed to every lens by path (keeps `args` small — `gotcha_workflow_args_generation_fidelity`). It contains:

1. `TOPIC` in full.
2. `INFERRED_DOMAINS`.
3. Any facts already established this session that bear on the topic — **labelled as unverified claims to confirm first-party**, never as ground truth.
4. The repo-relative paths worth starting from, if known.

**Push facts, never conclusions.** No verdicts, no "this is probably redundant", no pre-decided answers. A lens handed a conclusion confirms it; the dossier's value is independent state.

---

## Phase 2: Dispatch

**Sub-agent dispatch is MANDATORY — this command's instruction IS the Workflow authorization.**

**The engine IS the Anthropic transport.** `Workflow` dispatches only on the session's own endpoint, so `explore_fanout.js` cannot reach the sidecar at ANY lens count. Engine-vs-sidecar is therefore a **provider** decision, never a scale decision, and lens count does not move it. Reaching the sidecar means N parallel `deepseek_sidecar.sh` calls — one per lens, whatever N is.

**Choose the provider by BAND first (recorded in 1b), then dispatch:**

- **Surplus band → the engine, every lens, Anthropic fallback pins.** No sidecar spend when the plan quota is already paid for.

- **On-pace band or above → the sidecar for every lens whose roster row carries a sidecar primary pin**, however many lenses fired:
  `bash .claude/scripts/deepseek_sidecar.sh -m flash -e low -f <mandate-file> -S .claude/workflows/explore_fanout.schema.json -R <record.json> -l "explore:<key>" -G survey -d <repo-root>`
  Launch them in the background. Pass `-a <vault-path>` only if a mandate needs the Obsidian vault. Lenses whose roster row has NO sidecar column (`—`) are Anthropic-only and go through the engine in the same run.

- **The engine, for whatever lenses remain on the Anthropic side:**
  `Workflow({scriptPath: ".claude/workflows/explore_fanout.js", args: {lenses: [{key, promptPath, model, effort}], contextPrefixPath: <ctx>, spillDir: ".claude/scratch/fanout_spills/<slug>", justification: <only if a pin is raised>}})`
  Write each resolved mandate to its own scratchpad file and pass `promptPath`. Pins come from the roster table; the engine rejects a missing or misspelled pin rather than defaulting one. Derive `<slug>` from `TOPIC` (alphanumeric-dash). `spillDir` makes each lens write its complete deliverable to disk BEFORE structured-output validation — a schema rejection then costs nothing to recover (no re-dispatch). Capture the transcript dir from the Workflow result — the salvage path needs it.

**One consolidator per run, not one transport.** The real constraint is that every claim reaches a single comparison point — `exp-prior-art` vs `exp-integration-surface` is the pair most likely to disagree about the same subject, and a disagreement nobody adjudicates is worse than either claim alone. The engine consolidates whatever it dispatched; **the orchestrator consolidates the rest by hand in Phase 3**, applying the same three checks. A split run is normal, not an exception.

**Named anti-pattern — billing the panel to plan quota in a non-Surplus band.** Reading a lens-count rule as a transport rule puts the whole sweep on the expensive provider, and nothing downstream detects it: the dossier looks identical either way. The band gates provider; count gates nothing.

Do NOT collapse lenses into one generic agent, do NOT run the sweep inline, and fall back to parallel `Task` dispatch only if `Workflow` is unavailable (bare subagents inherit session effort unpinned — the failure this engine removes).

### Dispatch doctrine — seed, don't scope

The three rules from `/plan_check` Phase 2 apply verbatim and are why the CONTEXT file is a seed: **seed, don't cap** (a lens searches beyond what it was handed); **orient, don't conclude** (facts in, verdicts out); **verify, don't trust** (every injected fact is a claim to confirm). The engine's contract restates the third to every lens at every tier.

---

## Phase 3: Consolidate & Report

The engine returns `{claims, contradictions, flags, gaps, counts, perLens}` having already stamped each claim's lens, merged corroborations, downgraded unbacked confidence, and ranked by bearing. Three checks remain the orchestrator's:

1. **Uncovered lenses are a headline, not a footnote.** Any `flags` entry of kind `lens-did-not-run` or `lens-no-return` means that dimension is UNCOVERED — not clear. Recovery order is laddered so completed work is never re-bought: (a) read the lens's spill file at `<spillDir>/<key>.spill.md` — it was written before validation, so a rejection leaves the deliverable intact; (b) if absent, salvage the agent transcript via `/salvage_fanout <transcriptDir> <key>`; (c) ONLY then re-dispatch or cover inline. A dossier presented as complete with an uncovered dimension is the failure mode this whole command exists to prevent (`feedback_leak_sweep_without_adjudication_reads_as_clean`).
2. **First-party-verify every premise contradiction.** Before reporting a `premise-contradiction` claim, read the cited file yourself and confirm the quoted evidence. Agents fabricate confident file-state claims (`feedback_delegate_output_trust`); a contradiction that reframes the topic must not rest on an unread quote.
3. **Adjudicate contradictions; never let one win by sort order.** For each `contradictions` entry, read the evidence on both sides and settle it first-party. Report the resolution and which lens was wrong — an unresolved contradiction handed to a planner is worse than either claim alone.

### Report format

```
╔══════════════════════════════════════════════════════╗
║          EXPLORATION DOSSIER — [DATE]                 ║
╠══════════════════════════════════════════════════════╣
║ Topic:        [one line]                              ║
║ Domains:      [inferred list]                         ║
║ Lenses:       [N run: keys]  [M skipped: key=trigger] ║
║ Transport:    [engine | sidecar]  Band: [posture]     ║
║ Claims:       [N total, V verified]                   ║
║ Premise hits: [count]   Contradictions: [count]       ║
║ UNCOVERED:    [none | lens keys — DIMENSION MISSING]  ║
╚══════════════════════════════════════════════════════╝
```

Then, in this order:

- **Premise contradictions** — each with its verified evidence quote. These change the topic; they lead.
- **Reuse candidates** — what already owns the concern, with sibling counts.
- **Constraints** — rules, gotchas, invariants, gates. This section is copied verbatim into the plan's `Constraints` heading.
- **Blast radius** — consumers and call sites, grouped by subsystem.
- **Contradictions resolved** — each with the adjudication and the losing lens.
- **Change friction** — `--make-this-easy` mode only, and it leads this list rather than trailing it. State each friction point with its cost, then derive — as the orchestrator, from the claims — the **minimal restructuring** that would make the named change cheap, and **what not to touch** (the invariants and settled decisions `exp-change-ease` claimed). Deriving is yours because lenses report state; a proposal that arrives inside a claim is discarded and re-derived here. Keep the derived list to changes the friction claims actually evidence.
- **Gaps** — what nobody could establish, including any LSP-dependent query needing a serialized run.
- **Context** — orienting facts, last and brief.

### Handoff

State explicitly which downstream surface consumes what: `Constraints` into the plan file, `reuse-candidate` claims into the plan's *Families* / *Authored surfaces* sections where `plc-pattern-fit` will check them, `gaps` into the caller's open-questions list, and — in `--make-this-easy` mode — the derived minimal restructuring into the plan's first slice, or back to the user as a preparatory Part when it outgrows one. A dossier nobody consumes was wasted tokens.

---

## Constraints

- **Read-only — by contract, observed but not enforced.** Lenses are instructed read-only by the per-lens `BASE_CONTRACT` string; the files the run legitimately writes are the CONTEXT scratchpad, per-lens mandate files, lens spill files under `fanout_spills/`, and sidecar records. Nothing *blocks* a lens that writes elsewhere: `readonly_marker_arm.py` arms a marker when this engine is dispatched and `readonly_lens_write_guard.py` then warns on stderr for a subagent write outside those paths, but it never denies — no hook payload field attributes a write to a specific run, so a deny would also block legitimate concurrent write-executors. Two gaps, stated rather than implied: a `Bash`-mediated write (`cp`, `>`, `sed -i`) is invisible to a `Write|Edit` matcher, and the warning is advisory, so a determined lens still writes.
- **No verdict, no gate.** `/explore` never returns APPROVE/REVISE and never blocks. It reports state.
- **No recommendations from lenses.** A lens that proposes a design has exceeded its mandate; discard the proposal and keep the underlying claim.
- **Evidence or unverified.** The engine downgrades any `exists`/`partial` claim without quoted evidence and any `absent` claim without a proving command. Do not re-promote a downgraded claim without verifying it yourself.
- **Pins are explicit.** The engine rejects a lens with a missing or invalid `model`/`effort`; there is no silent floor. Raising a pin above the roster value passes `args.justification` naming the ambiguity it resolves.
- **Cloud compatible.** No csharp-ls dependency (banned under the concurrency guard anyway); LSP-dependent questions surface as gaps.
