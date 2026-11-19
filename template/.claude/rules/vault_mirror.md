---
paths:
  - ".claude/tools/vault_sync.py"
  - ".claude/hooks/vault_mirror_start.py"
---

# Design vault mirror

In cloud sessions, SessionStart prints `[vault] vault mirror: <sha>, last vault change <date> (<N> h ago), at <dir>`. The read-only mirror of the folders `tools/vault_sync.project.json` names is under `~/OneDrive/Documents/ObsidianVault/DevProjects`, where `vault_read.py` resolves the vault. Map a workstation's `<home>/OneDrive/...` path to `~/OneDrive/...`.

The date is the last commit that changed the vault, not the fetch time; a quiet vault can read old while in sync. Never write to the mirror, even when root can bypass its file modes. `AI/` is never mirrored. A design fact absent from the mirror is unavailable; never reconstruct it. `[vault] vault mirror UNAVAILABLE: <reason>`, or no `[vault]` line, means design docs are unavailable: report that gap rather than use an old extract as current evidence. Cloud worklog writes follow `commands/reference/worklog_cloud.md`.

## Held-back paths

A workstation's SessionStart line `[vault] sync pushed but HELD BACK` means the sync pushed everything except the listed paths, which match a key pattern. Open each path and decide. A real key: run `python3 .claude/tools/vault_sync.py mask "<path>"` (quote the path), then tell the owner to rotate that key, since it sat in the vault. A key-shaped example (a placeholder or a documented format): reword it so it no longer matches the pattern. The line shows at most the first few paths; the full list is in the sync status file.
