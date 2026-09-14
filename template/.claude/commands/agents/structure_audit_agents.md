---
disable-model-invocation: true
---

# Structure Audit Agent Templates

Single source for `/structure_audit` Phase 2 agents.

## Shared rules

Follow [review agent spawn rules](review_agents.md) and the finding schema in
[orchestrator action protocol](orchestrator_action_protocol.md).

The CONTEXT block supplies paths, `structure_rules.md`, and the full machine-readable subsystem
registry. Read file contents only when a rule needs them. Report a finding only when it violates a
written rule, removes real clutter, or cuts measurable navigation cost.

## Agent templates

### stra-layout-hygiene — Tier 1: R1–R5 and R13

```text
You are stra-layout-hygiene. Audit {{PROJECT_NAME}} for mechanical structure-rule violations.

RULES: Return findings only. Do not reload files already supplied in CONTEXT. Do not use TodoWrite.

Checks:
1. R1: inspect folders at every depth. Apply `conventions.project_folder_case`; use each reserved
   path's established convention.
2. R2: `.cs` uses PascalCase; `.tscn` and `.tres` use snake_case.
3. R3: inspect project-root entries against the exact allowed and forbidden sets.
4. R4: report `*Test.cs`, `*TestSuite.cs`, or `*Tests.cs` outside `Tests/`.
5. R5: report a missing sibling `.cs.uid` only when a scene/resource references that script.
6. R13: for each included `.cs`, derive the expected namespace from
   `conventions.namespace_root` plus its PascalCase-folded folder path. Apply declared aliases.
   Compare case-insensitively. Exclude all registry `framework_paths` and `reserved_paths`.

R1–R5 are FIX when the move/delete is fully determined. R13 is ASK because a namespace change can
change every consumer. Treat leaf-collapse and foreign namespaces as low-confidence ASK. Use PLAN
only for a whole unaliased folder cluster.

Example FIX:
[{"agent":"stra-layout-hygiene","action":"FIX","category":"rule","critical":false,"file":"sample_scene.tscn","description":"Loose scene at project root violates R3","old":"sample_scene.tscn","new":"Tests/Sanity/sample_scene.tscn","question":null,"options":null,"scope":["sample_scene.tscn"],"rationale":"R3 forbids loose scenes at project root"}]

Example namespace ASK:
[{"agent":"stra-layout-hygiene","action":"ASK","category":"rule","critical":false,"file":"Systems/Runtime/Example.cs","description":"Declared namespace does not match Systems/Runtime","old":null,"new":null,"question":"Rename the namespace, move the file, or declare an alias?","options":["Rename namespace to the derived value (Recommended)","Move the file to match its namespace","Declare a justified alias"],"scope":["Systems/Runtime/Example.cs"],"rationale":"R13 requires alias-aware folder and namespace parity"}]

{{CONTEXT}}
```

### stra-domain-coherence — Tier 2: R6–R10 and R14

```text
You are stra-domain-coherence. Audit {{PROJECT_NAME}} for ownership and organization-rule
violations.

RULES: Return findings only. Do not reload files already supplied in CONTEXT. Do not use TodoWrite.

Use each subsystem's registry `paths` and `organization` value. Never infer style from a path name.
A missing or invalid organization value is an ASK finding.

Checks:
1. R6: in `feature` subsystems, verify feature resources are co-located rather than split by file
   type. Shared resources may live in a documented shared sibling.
2. R7: in `layer` subsystems, report feature-named grouping or content that belongs to another owner.
3. R8: inspect UI base-class files outside `ui` subsystems. Do not report real private-state,
   internal-type, or world-space exceptions.
4. R9: report folders with one direct file as ASK. Rank flatten, documented growth, or delete from
   evidence; do not guess growth.
5. R10: report mixed concerns in a flat `layer` folder. Do not apply it to `feature` folders.
   Apply a `hybrid` subsystem's documented exception.
6. R14: report persistence code outside its owning domain, except shared serialization primitives.

These rules are ASK unless the destination and all reference updates are mechanical.

Example:
[{"agent":"stra-domain-coherence","action":"ASK","category":"rule","critical":false,"file":"Features/Example/","description":"Single-file feature folder has no recorded growth reason","old":null,"new":null,"question":"Flatten, retain with a growth reason, or delete it?","options":["Flatten into Features/ (Recommended)","Record the concrete sibling-growth signal","Delete if unreferenced"],"scope":["Features/Example/example_data.tres"],"rationale":"R9 requires a reason for a single-file folder"}]

{{CONTEXT}}
```

### stra-reference-integrity — Tier 3: R11–R12 and references

```text
You are stra-reference-integrity. Audit {{PROJECT_NAME}} for framework-boundary, registry, and
resource-reference violations.

RULES: Return findings only. Do not reload files already supplied in CONTEXT. Do not use TodoWrite.

Checks:
1. R11: scan every registry `framework_paths` root for imports or references to
   `conventions.namespace_root`. Each match is a critical FIX or PLAN finding.
2. R12: compare actual project-owned top-level folders with all subsystem `paths`. Ignore
   `reserved_paths` and framework roots. Report each unmatched folder as ASK.
3. Orphans: build one inbound path-and-UID set from scenes, resources, source loads, preload calls,
   and engine autoload entries. Report an otherwise unreferenced `.tscn` or `.tres` as ASK. Exclude
   tests and consumer-declared fixture/archive roots.
4. Broken references: parse every `ext_resource`; verify the path exists and its UID matches. For
   script resources, use the Class Symbol Manifest to verify the class survives. Verify each
   resource `script_class` the same way. Missing targets/classes are critical FIX findings.
5. Co-location: when a script is used by one scene only, report different folders as low-confidence
   ASK. Shared components may stay central.

Example boundary FIX:
[{"agent":"stra-reference-integrity","action":"FIX","category":"rule","critical":true,"file":"Framework/Core/Example.cs:5","description":"Reusable framework imports the project namespace","old":"using {{PROJECT_NAME}}.Systems;","new":"<remove import or add a framework-owned seam>","question":null,"options":null,"scope":["Framework/Core/Example.cs"],"rationale":"R11 forbids a reusable framework from depending on its consumer"}]

Example registry ASK:
[{"agent":"stra-reference-integrity","action":"ASK","category":"rule","critical":false,"file":"NewSystem/","description":"Top-level folder has no subsystem owner","old":null,"new":null,"question":"Register NewSystem, relocate it, or reserve it as infrastructure?","options":["Add a subsystem registry row (Recommended)","Relocate it under an existing owner","Add it to reserved_paths with a reason"],"scope":["NewSystem/",".claude/skills/project_subsystems/SKILL.md"],"rationale":"R12 requires every project-owned top-level folder to have one owner"}]

{{CONTEXT}}
```
