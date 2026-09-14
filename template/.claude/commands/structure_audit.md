---
disable-model-invocation: true
allowed-tools: Bash(git ls-files:*), Bash(find:*), Bash(ls:*), Glob, Grep, Read, Workflow
description: Audit project layout, ownership, framework boundaries, and resource references.
---

Audit the full {{PROJECT_NAME}} tree against
[`structure_rules.md`](../skills/architecture_philosophy/structure_rules.md). The audit is advisory and
read-only until the user approves one action tier.

## Arguments

- No argument: run all three agents.
- `--quick=references`: run only `stra-reference-integrity`.
- Any other argument: stop and show the valid forms.

A completed clean run is a no-op. On resume, reuse a complete manifest, context artifact, and Workflow
result whose source hashes still match; otherwise rebuild only the stale artifact.

## 1. Build context

### Project manifest

List every project path except:
- `.git/`, `.godot/`, `.config/`, `bin/`, `obj/`, and `TestResults/`
- third-party, generated, and test-adapter roots declared by the project
- `.claude/`
- contents of registry `framework_paths`; include each framework root itself for R11

Record:
1. every project-root entry;
2. every included file path;
3. counts by `.cs`, `.tscn`/`.tres`, and other asset.

### Rules and registry

Read:
- [`structure_rules.md`](../skills/architecture_philosophy/structure_rules.md) in full;
- `.claude/skills/project_subsystems/SKILL.md` and its full machine-readable registry.

Validate the registry before dispatch:
- each subsystem has `id`, `paths`, `organization`, `domain`, and `summary`;
- `organization` is `feature`, `layer`, `ui`, or `hybrid`;
- `domain` is `logic`, `gameplay`, `data`, `meta`, or `framework`;
- `conventions` supplies folder case, namespace root, framework paths, playtest paths, and reserved paths.

The registry supplies both R12 ownership and the folder organization style. Do not build a second
folder-style map.

### Memory and C# symbols

Unless `--quick=references`, semantic-search auto-memory for `structure`, `organization`, and
`framework boundary`; inject relevant hits.

Build a Class Symbol Manifest from anchored C# declarations. Map each class, record, and interface to
its path and namespace. Exclude tests, generated output, third-party code, and registry framework
roots. This lets the reference agent verify scene/resource script classes without LSP.

`--quick=references` skips memory lookup and runs only the reference agent. Keep it under two minutes.

## 2. Dispatch

Read [`structure_audit_agents.md`](agents/structure_audit_agents.md). Assemble each selected template
with one shared CONTEXT containing:
- project root;
- root inventory and full manifest;
- full rules;
- full validated subsystem registry;
- memory hits, when loaded;
- Class Symbol Manifest;
- finding-schema path.

Resolve the `fanout` and `executor` roles through the current model registry. Pin model and effort on
every job. Full mode uses:
- `stra-layout-hygiene`: fanout role, low effort;
- `stra-domain-coherence`: executor role, high effort;
- `stra-reference-integrity`: executor role, high effort.

Pass all jobs together to `.claude/workflows/review_fanout.js`. Quick mode passes only the reference
job. Do not replace a denied fan-out with direct Agent calls. Verify that each requested job returned
and that the Workflow journal records the intended model. A null or missing result is incomplete, not
clean.

## 3. Report

Apply [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md):
1. dedupe by `file:line`; retain the more specific or critical finding;
2. sort critical first, then FIX, ASK, PLAN, then category;
3. show scanned counts and finding counts by action and category;
4. rate the result:
   - `CLEAN`: no critical and at most 3 findings;
   - `MINOR POLISH`: no critical and 4–15 findings;
   - `REVIEW RECOMMENDED`: any critical or at least 16 findings.

Add notes only for a cross-agent pattern that changes the next action. Do not turn a missing agent,
unreadable input, or zero-engagement scan into a clean verdict.

## 4. Apply approved actions

Ask once before applying tiers. Then process FIX, ASK, and PLAN in order.

- Before a move, rename, or delete, find inbound path, basename, UID, and source references.
- Use `git mv` for moves. Update all references in the same change.
- A delete with any inbound reference becomes ASK.
- Build once after the approved FIX batch.
- Walk ASK findings one at a time.
- Route PLAN findings through the project's normal planning flow.

Do not change behavior. Do not audit inside third-party or reusable-framework roots. Do enforce R11 at
the framework boundary. Run the full audit after broad structural work; use quick mode after a rebase,
class rename, or file move.
