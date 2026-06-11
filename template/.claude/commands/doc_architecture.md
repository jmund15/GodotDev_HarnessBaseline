---
disable-model-invocation: true
---

Create or update an Architecture Document for a system/feature in Obsidian.

## Audience
A developer who will be reading, modifying, or extending this code. They need a comprehensive understanding of WHAT the system does, WHY it was built this way, HOW the code works, and WHY it's pieced together like this. Start from a high level and then dive into details one-by-one.

## Before Writing

Follow steps 1-3 from [Doc Before Writing](agents/doc_before_writing.md). Target doc path: `{SystemName}/Architecture.md`.

### 4. Deep-Read the Codebase
- Read ALL source files for the system — scripts, interfaces, base classes, resources, scenes.
- Map class hierarchies, signal connections, dependency chains.
- Identify design patterns in use (strategy, observer, factory, etc.).
- Note extension points and invariants.
- Read relevant tests to understand behavioral contracts.

### 5. Write via `write_doc`
Generate the prose through `write_doc`, not by hand — follow **Reason, Then Delegate** in [Doc Before Writing](agents/doc_before_writing.md) with `doc_type="architecture"` and `Voice/tone: terse-technical`. The Document Structure below is the spec's `Outline` (and the per-section shape to request) — not prose for you to type.

## Document Structure

Place at: `DevProjects/{{PROJECT_NAME}}/Claude/Documentation/{SystemName}/Architecture.md` (absolute vault path → `doc_path`)

```markdown
# {SystemName} — Architecture

> [!abstract] Executive Summary
> System boundary, core responsibilities, and key types in 3-5 sentences.
> What problem does this system solve? What are its inputs and outputs?

## Table of Contents
- [[#High-Level Overview]]
- [[#Component Deep-Dives]]
- [[#Cross-Cutting Concerns]]
- [[#Test Coverage]]
- [[#Future Enhancements]]
- [[#Changelog]]

## High-Level Overview
System diagram (Mermaid), component relationships, and data flow.
Describe the major moving parts and how they connect.
This section should give a reader enough context to navigate the rest of the doc.

> [!example]- System Diagram
> ```mermaid
> graph TD
>     SpellFactory[Spell Factory] --> SpellInstance[Spell Instance]
>     SpellInstance --> BehaviorRunner[Behavior Runner]
> ```

> [!info]- Data Flow
> How data moves through the system from input to output.
> Include key types/interfaces at each boundary.

## Component Deep-Dives
One collapsible callout per major class/component. Ordered from most fundamental to most dependent.


> [!info]- {ClassName} ({file_path})
> **Purpose:** What this class is responsible for.
> **Key Methods:**
> - `MethodName(params)` — What it does, its contract, return value meaning.
> **Logic Explanation (situational):** NOTE: Use this as a dedicated optional section for classes/functions that contain complex logic that includes deeper levels of math, physics, calculations, or anything intricate.
> - Detailed explanations and breakdowns of complex logic.
> **Design Decisions:**
> - Why this pattern was chosen over alternatives.
> **Extension Points:**
> - How a developer would extend this (subclass, compose, configure).
> **Invariants:**
> - What must always be true for this component to function correctly.

> [!info]- {Next Component}
> ...

## Cross-Cutting Concerns
How this system interacts with other systems in the codebase.

> [!info]- Dependencies
> What this system requires from others (signals listened to, services consumed, resources expected).

> [!info]- Dependents
> What other systems depend on this one (who listens to its signals, who consumes its output).

> [!info]- Signals & Events
> Signal map: who emits what, who listens, and the data contract.

## Test Coverage
Overview of the most holistic and important tests, organized by domain.

> [!success]- Logic Tests ({test_file})
> What behavioral contracts these tests verify.
> Highlight the most comprehensive tests that would catch regressions.

> [!success]- Integration Tests ({test_file})
> What end-to-end flows these tests cover.

> [!success]- Edge Cases & Boundary Tests
> Non-obvious tests that catch subtle bugs.

## Future Enhancements

> [!warning]- Necessary Next Steps
> Work required to reach full system completion. Prioritized.

> [!tip]- Creative Ideas & Addons
> Imaginative extensions that could enhance the system. Use your creativity here.

> [!abstract]- QOL & Performance
> Usability improvements, performance optimizations, developer experience tweaks.

> [!question]- Known Code Smells & Regrets
> Honest assessment of design compromises, technical debt, or things you'd do differently.

## Changelog
| Date | Summary |
|------|---------|
| YYYY-MM-DD | Initial creation / Description of update |

> [!info] Related Documents
> - [[Usage]] — Editor workflow and configuration guide
> - [[Retrospective]] — Development history
> - [[Quick Reference]] — System overview and document index
```

## Update Behavior
- **New doc:** spec carries the full structure above; `write_doc` writes the whole file.
- **Existing doc:** per *Reason, Then Delegate* — `Read` it, then call `write_doc` with an `Outline` of only the changed sections (codebase-changed sections + new Component Deep-Dive callouts for new classes + the System Diagram if architecture changed), `Edit`-merge those into the existing file, and append a new Changelog row with today's date and a summary. Untouched sections never pass through the worker.
- **Quick Reference:** If `Quick Reference.md` exists in the same folder, update the Architecture entry in its Document Index and Key Reference Table to reflect current content.

## Tiered Depth Approach
Not every reader needs every detail. Structure allows scanning:
1. **Executive Summary + High-Level Overview** — Enough to understand the system's role (2 min read)
2. **Component Deep-Dives** — Drill into specific classes as needed (collapsible, read on demand)
3. **Cross-Cutting + Tests + Future** — Full context for maintainers and architects

## Formatting Rules
The write_doc spec + call live in *Reason, Then Delegate* ([Doc Before Writing](agents/doc_before_writing.md)). Obsidian callout/wikilink formatting is auto-injected worker-side (`modifier.obsidian`); the callout map below is structural guidance to fold into the spec, not something you hand-format.
- Callout types: `info` (components, reference), `success` (tests), `warning` (critical future work), `tip` (ideas), `question` (rationale/regrets), `example` (diagrams), `abstract` (QOL)
- Include Mermaid diagrams for system structure and data flow where they add clarity — follow the `mermaid_diagrams` skill (type selection, canonical palette, renderer constraints).
- Reference actual file paths and class names — this is a technical document.
- Be honest about trade-offs and regrets. This doc serves future maintainers.
