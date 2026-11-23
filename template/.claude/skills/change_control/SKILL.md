---
name: Change Control
description: >-
  Use to decide which check a change runs and when: inside a slice, at a drive's close, after the land.
  SKIP writing or judging tests (testing).
---

# Change Control

## Gate cadence

This file says when each check runs and why. The commands are the project's: `skills/project_subsystems/adaptation.json` `gate_cadence` holds them, one key per row below (`adaptation.py` `CADENCE_KEYS` owns the set).

The gate asks whether a change broke anything outside the scope it was made in. That question has no answer until work in that scope stops, so the unit is the commit, never the slice.

| When | Run (`gate_cadence` key) | Why at this point |
|---|---|---|
| Inside a slice, a delegate's whole job included | `slice`: the changed unit's own tests; after a failure, only the failed tests | Fast feedback on the unit; a wider run here re-proves code nobody changed |
| Once at a drive's close, before the push | `close`: the scoped run over the drive's accumulated changes | Catches interactions between earlier slices and later edits to shared code |
| Before a merge | `merge`: the scoped run over what the merge brings in | Proves the joined result, which no slice saw |

- **A harness change** runs its own unit's test file and nothing wider; the shared repository's CI runs the whole harness suite on every push.
- **The full gate runs after each land on the default branch**, by the main check (`adaptation.json` `main_check`, not a cadence key): it costs minutes, and after the land it costs the session nothing.
- **A session runs the full gate itself** only to reproduce a red the main check found.
- **A skipped check names the trigger that brings it back.** An unnamed skip reads the same as a broken check.

## A red on main

Landing first moves the full gate after the push, so its red needs an owner. The session whose commits a red names fixes it before its turn ends; once that session has ended, the next session working in that area fixes it. A red blocks no other work: later checks read it as pre-existing, which excuses only the same failures. A red in another repository's CI that tests this one at its default branch is a red here, owned by whoever landed the range since that CI's last green.
