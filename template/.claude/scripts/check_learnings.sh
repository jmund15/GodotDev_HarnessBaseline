#!/bin/bash
# check_learnings.sh - Basic pattern detection for learning signals in transcript
# Called by Stop hook to suggest autolearn when corrections may have occurred
#
# LIMITATIONS: This script only does text pattern matching on the transcript.
# It CANNOT detect agent trial-and-error learning or semantic corrections.
# When in doubt, it suggests running autolearn.
#
# Usage: echo "$HOOK_INPUT_JSON" | bash check_learnings.sh

# Read hook input JSON from stdin
INPUT_JSON=$(cat)

# Extract transcript path from the input
TRANSCRIPT_PATH=$(echo "$INPUT_JSON" | grep -oP '"transcript_path"\s*:\s*"\K[^"]+' 2>/dev/null | head -1)

# Fallback for Windows grep without -P
if [ -z "$TRANSCRIPT_PATH" ]; then
    TRANSCRIPT_PATH=$(echo "$INPUT_JSON" | sed -n 's/.*"transcript_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

# If no transcript or file doesn't exist, suggest autolearn anyway (safe default)
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    cat << 'EOF'
{
  "ok": true,
  "reason": "Session complete. Consider running /autolearn if there were corrections or learnings. @.claude/skills/autolearn/SKILL.md"
}
EOF
    exit 0
fi

# Patterns that suggest user corrections or explicit learning requests
# (These are detectable via simple text matching)
USER_CORRECTION_PATTERNS="no, use|don't do|instead of|we always|never do|wrong|that's not|actually.* should|prefer .* over|remember this|from now on|going forward|i said|i told you"

# Patterns that might indicate errors/retries (agent trial-and-error)
# Less reliable but worth flagging
ERROR_PATTERNS="error|failed|exception|let me try|different approach|that didn't work|trying again"

# Count matches (case-insensitive), trim whitespace
USER_MATCHES=$(grep -ciE "$USER_CORRECTION_PATTERNS" "$TRANSCRIPT_PATH" 2>/dev/null | tr -d '[:space:]')
USER_MATCHES=${USER_MATCHES:-0}
ERROR_MATCHES=$(grep -ciE "$ERROR_PATTERNS" "$TRANSCRIPT_PATH" 2>/dev/null | tr -d '[:space:]')
ERROR_MATCHES=${ERROR_MATCHES:-0}

# Heuristic: suggest autolearn if we see correction patterns OR significant errors
if [ "$USER_MATCHES" -ge 2 ] || [ "$ERROR_MATCHES" -ge 5 ]; then
    cat << EOF
{
  "ok": true,
  "reason": "POTENTIAL LEARNINGS DETECTED (corrections: $USER_MATCHES, errors: $ERROR_MATCHES): Consider running /autolearn to capture durable patterns. @.claude/skills/autolearn/SKILL.md"
}
EOF
else
    cat << EOF
{
  "ok": true,
  "reason": "No significant learnings detected (corrections: $USER_MATCHES, errors: $ERROR_MATCHES). Skipping autolearn suggestion."
}
EOF
fi
