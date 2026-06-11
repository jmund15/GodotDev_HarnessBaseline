---
name: precedent-is-evidence-not-authority
description: "Don't default to matching existing code precedent; verify it against the documented guideline surface (skills/rules/CLAUDE.md/memory) before adopting. Guideline > precedent; no guideline → decide on merit."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d1a30c68-4d35-44f6-b137-f4d77dadec04
---

When a decision defaults to "match the existing precedent / what the codebase already does," that precedent is **evidence, not authority**. Verify it against the documented guideline surface (`skills/`, `.claude/rules/`, `CLAUDE.md`, auto-memory) before adopting. Guideline contradicts precedent → guideline wins (the precedent is the convention-violating exception). No guideline covers it → decide on merit; don't copy reflexively. The escape hatch: a precedent *verified by production* (it demonstrably works / is what the tooling accepts) is a concrete reason to keep it over a doc-only guideline.

**Why:** existing code can encode a mistake that predates or ignored a guideline; reflexively matching it propagates the mistake and reads as blindly following, not deciding.

**How to apply:** before writing "follow the existing X convention" in a plan, grep the guideline surface for the actual rule and cite *that*. Distinct from `feedback_inspect_existing_abstractions_first.md` (extend what exists) — this is don't-blindly-copy-what-exists-without-checking-it's-sanctioned.

**Concrete:** 2026-06-06 — a plan defaulted to the inline test-stub precedent in `PartialGraphTests`; user flagged that the documented convention (`Tests/Framework/Mocks/` for doubles + `csharp_patterns` "eliminate duplicated test setup") made that precedent the violating exception, not the rule.
