---
name: switch(type) expresses CLOSED-SET intent — identical-behavior cases mean delete the type
description: A switch over types is a closed-set design statement; if a new case's behavior is identical to an existing one, the TYPE is the smell — delete it, don't add the case.
type: feedback
originSessionId: 15bc6648-e4d1-4a64-b970-d32e8c122873
---
A `switch (type)` statement that maps types to behaviors expresses a **CLOSED-SET design intent**. Adding a case requires justification — and if the proposed case's behavior is identical to an existing case, the TYPE itself is the smell: **delete the redundant type, repoint consumers to the surviving case.**

**Why:** A switch enumerates the legitimate variants. When two variants would do the same thing, they aren't actually distinct concepts — keeping them separate just multiplies surface area for no semantic gain. Adding the case entrenches the false distinction; deleting the type collapses the distinction back to the actual design.

**How to apply:** When a switch fails to handle a type, FIRST ask *"what distinct behavior does this type need?"* before adding the case. If the honest answer is "nothing different from case X," the next move is to delete the type, not to add a duplicate case.

**Concrete (2026-05-10):** `KnockbackCollisionResponse` was an empty marker, identical to `PierceCollisionResponse`; conflated physics+combat layers. Deletion fixed the layer violation; adding the case to the existing switch would have entrenched it.

**User verbatim:** *"I don't like switch states normally but [they] aren't going to extend the amount of options."*

**Migrated from MCP** (was `Architectural_Closed_Set_Switch`, entityType `architectural_rule`) 2026-05-11 — moved to auto-memory so the rule loads at SessionStart.
