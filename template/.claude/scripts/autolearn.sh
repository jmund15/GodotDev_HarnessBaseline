#!/bin/bash
# autolearn.sh - Pre-compact hook script to trigger autolearn skill
# This script outputs JSON that instructs Claude to run the autolearn analysis
# before the conversation is compacted.

# Debug: log to stderr (visible in verbose mode or terminal)
echo "[PreCompact Hook] autolearn.sh triggered at $(date)" >&2

# Output structured JSON for the PreCompact hook
# The additionalContext field adds context that Claude will see before compacting
cat << 'EOF'
{
  "continue": true,
  "hookSpecificOutput": {
    "hookEventName": "PreCompact",
    "additionalContext": "IMPORTANT: Before compacting this conversation, run the /autolearn skill to analyze this session for corrections, preferences, and architectural decisions that should be captured in auto-memory or Skills. This ensures learnings are preserved before the conversation history is condensed."
  }
}
EOF
