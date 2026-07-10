---
name: feedback_pathspec_commit_stage_infra_deps
description: "Pathspec-committing tests must also stage their (often untracked) mock/fixture deps, else the commit won't build in isolation."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 83b89f38-76ed-4be6-84f9-095f353c42de
---

A pathspec commit (`git commit -- <paths>`, the concurrent-safe idiom) silently excludes any unlisted path — including a test's own mock/builder/fixture dependencies. A dep you READ as if it existed but that's actually untracked WIP gets left out, so the commit doesn't build in a fresh checkout (a later commit / parallel session has to backfill it).

**Why:** existence-on-disk ≠ tracked. A file your tests depend on can be untracked WIP (an interrupted prior session, or a parallel session's in-flight work).

**How to apply:** before a pathspec commit of tests, confirm each test-infra dep (mock/builder/fixture) is tracked via `git ls-files <path>`; stage the untracked ones with your tests. Pairs with [[feedback_separate_preexisting_changes_before_commit]] (exclude foreign) + [[gotcha_concurrent_session_shared_index_collision]] (the WIP often comes from a parallel session).
