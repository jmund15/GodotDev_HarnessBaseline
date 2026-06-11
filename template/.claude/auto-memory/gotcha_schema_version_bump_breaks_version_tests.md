---
name: gotcha-schema-version-bump-breaks-version-tests
description: "Bumping a persisted schema-version constant breaks tests asserting a hardcoded post-load version — Load() migrates loaded data to current, so fresh/old-file loads return the bumped value."
metadata: 
  node_type: memory
  type: gotcha
  originSessionId: 15d022f2-dd83-460d-abb5-9563357a5979
---

Bumping a repository's `CurrentSchemaVersion` constant (to register a new migration)
silently breaks tests that assert a hardcoded post-load version. `Load()` normalizes
loaded data to the current version via the migrator, so a no-file or old-file load now
returns the BUMPED value, not the old one. These tests pass in isolation but the full
suite fails — exactly the masking the regression gate's full-run exists to catch.

**How to apply:** after bumping the version constant, grep tests for the old version
int literal near the repo type and update post-load assertions to the new current
version. To probe round-trip *preservation* (not migration), save data AT the current
version so `Load()` runs no migration.

**Concrete:** `CurrentSchemaVersion` 1→2 (`MetaProgressionRepository`) broke
`LoadWithNoFile_ReturnsDefaultData` + `SaveLoadRoundTrip_PreservesData` (both asserted
`==1`); only the full `/regression_gate` Integration run surfaced them. 2026-06-05.

Related: [[gotcha_save_repository_load_hardening]] (the Load() forward-version guard this interacts with).
