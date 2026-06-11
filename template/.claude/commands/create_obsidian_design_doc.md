---
disable-model-invocation: true
---

Create a comprehensive design doc in Obsidian covering this session's full scope of work.
Prioritze (when applicable):
1. **FIRST SECTION**: Delineate the workflow and mindset of a developer who is going to use this system effectively. Proper usage, potential common mistakes, and tips for maximum production. View the devleper as smart and experienced in game design and godot, but not experienced in using this specific system.
2. Clear, concise reasoning of why design and architectural decisions were made.
3. Documentation of the tests per section, showcasing the most holistic tests of each domain (logic, integration, e2e).
4. Issues encountered during development, and how they were resolved.
5. Future enhancement of system, including:
    * Necessary improvements, next steps to FULL system completion.
    * Cool ideas and addons to potentially create (use your imagination!)
    * QOL and tweaks that could improve the overall usability or performance of the system
    * Any regrets or potential design code smells made during design.
6. Aesthetic presentation of information, utilizing all available Obsidain-supported formatting and tools when applicable.

Place this document in 'DevProjects/{{PROJECT_NAME}}/Claude/Documentation'. If there's more than one document for this system/topic, create a folder for them.

## Before Writing
Follow steps 1-3 from [Doc Before Writing](agents/doc_before_writing.md), then deep-read the session's full scope of work (code, tests, decisions, hiccups) so you can name concrete claims and facts.

## Writing via `write_doc`
Generate the prose through `write_doc`, not by hand (Documentation Delegation Rule, HARD) — follow **Reason, Then Delegate** in [Doc Before Writing](agents/doc_before_writing.md) with `doc_type="design"` and `Voice/tone: terse-technical`. Build the spec's `Outline` from the prioritized sections above (developer-workflow-first, then design rationale, tests-per-domain, issues, future enhancements). The `obsidian` modifier auto-applies from the vault `doc_path`, so Obsidian callouts/wikilinks/formatting are injected worker-side — fold callout intent into the spec rather than hand-formatting. For a multi-doc topic folder, write each doc with its own `write_doc` call.