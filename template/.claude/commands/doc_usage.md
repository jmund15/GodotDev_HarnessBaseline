---
disable-model-invocation: true
---

Create or update a Usage Guide for a system/feature in Obsidian.

## Audience
A smart, experienced game designer/developer who needs to USE this system effectively in the Godot editor. They don't need to know how the code works internally — they need to know what to do, what to expect, and what to avoid.

## Before Writing

Follow steps 1-3 from [Doc Before Writing](agents/doc_before_writing.md). Target doc path: `{SystemName}/Designer Usage.md`.

### 4. Read the Codebase
- Read all relevant source files for the system (scripts, resources, scenes).
- Identify all `[Export]` properties, configuration points, and editor-facing interfaces.
- Understand the runtime behavior and in-game effects.

### 5. Write via `write_doc`
Generate the prose through `write_doc`, not by hand — follow **Reason, Then Delegate** in [Doc Before Writing](agents/doc_before_writing.md) with `doc_type="usage"` and `Voice/tone: instructional second-person`. The Document Structure below is the spec's `Outline` — not prose for you to type.

## Document Structure

Place at: `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/{SystemName}/Designer Usage.md` (absolute vault path → `doc_path`)

```markdown
# {SystemName} — Designer Usage

> [!abstract] Executive Summary
> 2-3 sentence description of what this system does and why a designer would use it.

## Table of Contents
- [[#Quick Start]]
- [[#Usage Guide]]
- [[#Configuration Reference]]
- [[#Common Mistakes & Pitfalls]]
- [[#Tips & Power Usage]]
- [[#Changelog]]

## Quick Start
Minimal steps (3-5) to get the system working in a basic configuration.
The reader should be able to follow these and see a result immediately.

## Usage Guide
Step-by-step editor workflow with concrete examples.

> [!example]- Setting Up {Component/Feature} in the Editor
> Detailed steps for configuring this in Godot's inspector/scene tree.
> Include which nodes to add, which resources to create, which exports to set.

> [!example]- Expected In-Game Behavior
> What happens at runtime when configured correctly.
> Include 2-3 concrete use cases showing "if you set X to Y, the result is Z."
>
> **Runtime flow** — include when the behavior spans 3+ actors/systems; a sequence diagram shows causality and ordering that prose flattens. Follow the `mermaid_diagrams` skill.
> ```mermaid
> sequenceDiagram
>     Designer->>System: configures Export X
>     System->>GameManager: registers on scene load
>     GameManager->>System: triggers on game event
>     System->>Player: applies visible result
> ```

> [!example]- {Additional Workflow Sections as Needed}
> One callout per distinct workflow or configuration scenario.

## Configuration Reference

> [!info]- Properties & Exports
> Table of all key configurable properties:
> | Property | Type | Default | Description |
> |----------|------|---------|-------------|
> | ... | ... | ... | ... |

> [!info]- Resource Types
> Any `.tres` resources the designer needs to create or configure.

## Common Mistakes & Pitfalls

> [!warning]- {Mistake Title}
> What goes wrong, why, and how to fix/avoid it.

## Tips & Power Usage

> [!tip]- {Tip Title}
> Advanced usage patterns, shortcuts, or non-obvious capabilities.

## Changelog
| Date | Summary |
|------|---------|
| YYYY-MM-DD | Initial creation / Description of update |

> [!info] Related Documents
> - [[Architecture]] — Code structure and design decisions
> - [[Retrospective]] — Development history
> - [[Quick Reference]] — System overview and document index
```

## Update Behavior
- **New doc:** spec carries the full structure above; `write_doc` writes the whole file.
- **Existing doc:** per *Reason, Then Delegate* — `Read` it, call `write_doc` with an `Outline` of only the changed/new-capability sections, `Edit`-merge those in, and append a new Changelog row with today's date and a summary. Untouched sections never pass through the worker.
- **Quick Reference:** If `Quick Reference.md` exists in the same folder, update the Usage entry in its Document Index and Key Reference Table to reflect current content.

## Formatting Rules
The write_doc spec + call live in *Reason, Then Delegate* ([Doc Before Writing](agents/doc_before_writing.md)). Obsidian callout/wikilink formatting is auto-injected worker-side (`modifier.obsidian`); the callout map below is structural guidance to fold into the spec, not something you hand-format.
- Callout types: `tip` (recommendations), `warning` (pitfalls), `example` (walkthroughs), `info` (reference data), `question` (rationale)
