---
name: gotcha-grep-brace-glob-silent-zero
description: "Grep-tool glob with brace expansion ({a,b}/**/*.cs) can silently match zero files — treat 0 hits on a broad negative sweep as a liveness signal, not a clean result"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 8d59ce77-9de6-4405-9305-e5e56b10bc71
---

The Grep tool's `glob` parameter with brace expansion (`{Game,Meta,Sim,Tests}/**/*.cs`) can return zero matches without error even when matching files exist. Zero hits on a broad negative sweep (stub-marker scan, TODO audit, leak check) is a **positive-liveness signal**: prove the lens ran before trusting the absence — rerun per-directory or with the `type` filter.

**Verified:** 2026-07-06 session-audit stub sweep — the brace glob returned 0; the identical pattern rerun per-directory returned 15 hits.

Sibling of [[arch-rule-autonomous-loop-positive-liveness]] and [[gotcha-cascade-gate-vacuous-without-godot-bin]] — never read "0 findings" as success without proving the search executed.
