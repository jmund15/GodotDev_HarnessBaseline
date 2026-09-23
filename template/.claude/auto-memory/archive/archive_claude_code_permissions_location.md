---
name: archive_claude_code_permissions_location
description: "Claude_Code_Permissions_Location — 3 archived observations (TechnicalKnowledge). Cold-tier reference."
metadata:
  node_type: memory
  type: reference
  tier: cold
  source: archive
  entity_type: TechnicalKnowledge
---

# Claude_Code_Permissions_Location

> Cold-tier archive (TechnicalKnowledge). Search-only — not auto-loaded.

- Permissions in .claude/settings.json are tracked by git and shared across worktrees. Permissions in .claude/settings.local.json are gitignored and local-only.
- To share allowed Bash commands/tool exceptions across worktrees, put them in settings.json not settings.local.json.
- 'Allow for this session' is in-memory only and does NOT persist. 'Always allow' writes to settings.local.json. Most session approvals are session-scoped and evaporate on session end.

