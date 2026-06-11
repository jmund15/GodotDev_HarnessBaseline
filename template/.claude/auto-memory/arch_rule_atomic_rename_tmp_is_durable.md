---
name: arch-rule-atomic-rename-tmp-is-durable
description: "In (write-tmp → delete-dest → rename) atomic recipes, failure ON rename means the tmp IS the durable new state — preserve it for recovery, don't clean it up."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: fd0b005f-63ab-4f79-a253-a2db6c715771
---

When implementing a (write-tmp → delete-dest → rename) atomic-replace recipe (Windows `MoveFileEx` without `REPLACE_EXISTING`, POSIX `rename(2)`, or any equivalent), the failure-on-rename branch MUST NOT delete the tmp. After the destination delete succeeds, the tmp is the only durable copy of the new content; cleaning it up converts a recoverable interruption into permanent data loss.

**Why:** P3 persistence audit (2026-05-18) caught `AtomicResourceFile.WriteAtomic.CleanupTemp(tmpPath)` running on rename failure, destroying the breadcrumb that `ReadIfExists`'s recovery branch was designed to promote. The "crash-survival" test passed because it exercised the EARLIER `delete-of-readonly` failure (where the tmp SHOULD be cleaned), not the rename-after-delete window. Same bug shipped in `AtomicConfigFile.SaveAtomic`.

**How to apply:** In any atomic-rename helper, branch failure-cleanup by WHERE in the recipe the failure happened. The tmp window opens at (1) write-tmp success and closes at (2) rename success. Inside that window, every failure path must leave the tmp in place. Outside it (write-tmp failed, delete-dest failed before rename touched anything), clean up. See also [[arch-rule-onexit-must-not-clobber-consumer-onenter]] — same shape (failure-path cleanup that destroys the consumer's input).

**Source:** `AtomicResourceFile.cs` and `AtomicConfigFile.cs` post-audit fixes, 2026-05-18.
