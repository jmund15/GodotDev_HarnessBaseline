#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transcript Summary Builder - Creates concise summaries (~10-20KB) from transcripts.

Optimized for:
- Autolearn (60%): Corrections, TDD feedback loops, error->resolution pairs
- Post-compaction resume (30%): Task state, last request, todo status
- Human review (10%): Readable narrative flow

Used by transcript_backup.py during streaming copy.
"""

import json
import re
from datetime import datetime
from typing import Any


# =============================================================================
# Pattern Library (copied from logs/parse_transcripts.py)
# =============================================================================

# Patterns indicating user corrections/signals
CORRECTION_PATTERNS = [
    r'\bno[,.]?\s',              # "no, " "no."
    r'\bdon\'?t\b',              # "don't", "dont"
    r'\binstead\b',              # "instead"
    r'\balways\b',               # "always"
    r'\bnever\b',                # "never"
    r'\bnot\s+right\b',          # "not right"
    r'\bwrong\b',                # "wrong"
    r'\bcorrect\b',              # "correct"
    r'\bshould\s+be\b',          # "should be"
    r'\bshould\s+have\b',        # "should have"
    r'\bactually\b',             # "actually"
    r'\bremember\b',             # "remember"
    r'\bmake\s+sure\b',          # "make sure"
    r'\bprefer\b',               # "prefer"
    r'\bwant\b',                 # "want"
    r'\buse\s+\w+\s+instead\b',  # "use X instead"
    r'\bnext\s+time\b',          # "next time"
    r'\bTDD\b',                  # TDD discussions
    r'\btest\s+first\b',         # "test first"
    r'\bfailing\s+test\b',       # "failing test"
    r'\bred\s+phase\b',          # "red phase" (TDD)
    r'\bfollow\b.*\brule\b',     # "follow the rule"
    r'\bdid\s+you\b',            # questions about actions
    r'\bwhy\s+did\b',            # questioning actions
    r'\bwhy\s+didn\'?t\b',       # questioning inaction
]

# Compile patterns for efficiency
COMPILED_CORRECTION_PATTERNS = [(p, re.compile(p, re.IGNORECASE)) for p in CORRECTION_PATTERNS]

# Error detection patterns by type
ERROR_PATTERNS = {
    'build_error': [
        re.compile(r'error CS\d+:', re.IGNORECASE),
        re.compile(r'MSBUILD.*error', re.IGNORECASE),
        re.compile(r'Build FAILED', re.IGNORECASE),
    ],
    'test_failure': [
        re.compile(r'Expected:.*Actual:', re.IGNORECASE),
        re.compile(r'Failed!.*Failed:\s*\d+', re.IGNORECASE),
        re.compile(r'Assert\w+\s+failed', re.IGNORECASE),
    ],
    'tool_error': [
        re.compile(r'"is_error":\s*true', re.IGNORECASE),
        re.compile(r'Exit code [1-9]'),
        re.compile(r'command not found', re.IGNORECASE),
    ],
}

# Resolution detection patterns by type
RESOLUTION_PATTERNS = {
    'build_success': [
        re.compile(r'Build succeeded', re.IGNORECASE),
        re.compile(r'0 Error\(s\)', re.IGNORECASE),
    ],
    'test_pass': [
        re.compile(r'Passed!.*Failed:\s*0', re.IGNORECASE),
        re.compile(r'All tests passed', re.IGNORECASE),
    ],
}


# =============================================================================
# Utility Functions
# =============================================================================

def extract_content(entry: dict) -> str:
    """
    Extract text content from a transcript entry, handling polymorphic content.

    Content can be:
    - str: Direct text
    - list: Array of {type: "text", text: "..."} objects
    """
    content = entry.get('content', '')

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        # Handle content arrays (Claude's format)
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    parts.append(item.get('text', ''))
                elif item.get('type') == 'tool_use':
                    # Include tool name for context
                    tool_name = item.get('name', 'unknown_tool')
                    parts.append(f"[Tool: {tool_name}]")
                elif item.get('type') == 'tool_result':
                    # Tool results can have content as string or nested
                    result_content = item.get('content', '')
                    if isinstance(result_content, str):
                        parts.append(f"[Result: {result_content[:200]}...]" if len(result_content) > 200 else f"[Result: {result_content}]")
        return ' '.join(parts)

    return str(content)


def detect_correction_signals(content: str) -> list[str]:
    """Return list of matched correction pattern strings."""
    content_lower = content.lower()
    matched = []

    for pattern_str, compiled in COMPILED_CORRECTION_PATTERNS:
        if compiled.search(content_lower):
            matched.append(pattern_str)

    return matched


def detect_error_type(content: str) -> str | None:
    """Detect if content contains an error and return its type."""
    for error_type, patterns in ERROR_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(content):
                return error_type
    return None


def detect_resolution_type(content: str) -> str | None:
    """Detect if content contains a resolution and return its type."""
    for resolution_type, patterns in RESOLUTION_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(content):
                return resolution_type
    return None


def truncate_at_sentence(content: str, max_len: int = 500) -> str:
    """Truncate content at sentence boundary, respecting max_len."""
    if len(content) <= max_len:
        return content

    # Find last sentence boundary before max_len
    truncated = content[:max_len]

    # Look for sentence endings
    for ending in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
        last_idx = truncated.rfind(ending)
        if last_idx > max_len // 2:  # Don't truncate too aggressively
            return truncated[:last_idx + 1].strip() + '...'

    # Fallback to word boundary
    last_space = truncated.rfind(' ')
    if last_space > max_len // 2:
        return truncated[:last_space].strip() + '...'

    return truncated.strip() + '...'


def classify_message_signal(content: str) -> str:
    """Classify the primary signal type of a user message."""
    content_lower = content.lower()

    # Check for questions
    if '?' in content and any(w in content_lower for w in ['why', 'how', 'what', 'where', 'when', 'did you']):
        return 'question'

    # Check for corrections
    if any(compiled.search(content_lower) for _, compiled in COMPILED_CORRECTION_PATTERNS[:10]):  # High-signal patterns
        return 'correction'

    # Check for instructions
    if any(w in content_lower for w in ['implement', 'create', 'add', 'fix', 'update', 'run', 'make']):
        return 'instruction'

    # Check for approval/confirmation
    if any(w in content_lower for w in ['yes', 'ok', 'good', 'looks good', 'approved', 'lgtm', 'proceed']):
        return 'approval'

    return 'other'


# =============================================================================
# Error Tracker (for TDD Feedback Loops)
# =============================================================================

class ErrorTracker:
    """
    Tracks errors and pairs them with resolutions within a window.

    Key design: Errors expire after RESOLUTION_WINDOW messages to avoid
    false pairings with unrelated resolutions.
    """

    RESOLUTION_WINDOW = 50  # Messages to wait for resolution
    MAX_PENDING = 10        # Max pending errors to track

    def __init__(self):
        self.pending_errors: list[dict] = []  # [{type, preview, message_idx}]
        self.resolved_pairs: list[dict] = []  # [{error_type, error_preview, resolution_type, resolved}]
        self.message_count = 0

    def record_error(self, error_type: str, content: str, message_idx: int):
        """Record a new error, evicting oldest if at capacity."""
        if len(self.pending_errors) >= self.MAX_PENDING:
            # Evict oldest, mark as unresolved
            oldest = self.pending_errors.pop(0)
            self.resolved_pairs.append({
                'error_type': oldest['type'],
                'error_preview': oldest['preview'],
                'resolution_type': None,
                'resolved': False
            })

        self.pending_errors.append({
            'type': error_type,
            'preview': truncate_at_sentence(content, 300),
            'message_idx': message_idx
        })

    def check_resolution(self, resolution_type: str, message_idx: int):
        """Check if a resolution matches any pending errors."""
        # Map resolution types to compatible error types
        compatibility = {
            'build_success': ['build_error'],
            'test_pass': ['test_failure'],
        }

        compatible_errors = compatibility.get(resolution_type, [])

        # Find matching pending error (FIFO - oldest first)
        for i, error in enumerate(self.pending_errors):
            if error['type'] in compatible_errors:
                # Check if within resolution window
                if message_idx - error['message_idx'] <= self.RESOLUTION_WINDOW:
                    resolved_error = self.pending_errors.pop(i)
                    self.resolved_pairs.append({
                        'error_type': resolved_error['type'],
                        'error_preview': resolved_error['preview'],
                        'resolution_type': resolution_type,
                        'resolved': True
                    })
                    return True

        return False

    def expire_old_errors(self, current_idx: int):
        """Move expired pending errors to unresolved list."""
        expired = []
        remaining = []

        for error in self.pending_errors:
            if current_idx - error['message_idx'] > self.RESOLUTION_WINDOW:
                expired.append(error)
            else:
                remaining.append(error)

        self.pending_errors = remaining

        for error in expired:
            self.resolved_pairs.append({
                'error_type': error['type'],
                'error_preview': error['preview'],
                'resolution_type': None,
                'resolved': False
            })

    def finalize(self) -> tuple[list[dict], list[dict]]:
        """
        Return (resolved_pairs, unresolved_errors).
        Moves any remaining pending to unresolved.
        """
        # Move all remaining pending to unresolved
        for error in self.pending_errors:
            self.resolved_pairs.append({
                'error_type': error['type'],
                'error_preview': error['preview'],
                'resolution_type': None,
                'resolved': False
            })

        self.pending_errors = []

        resolved = [p for p in self.resolved_pairs if p['resolved']]
        unresolved = [p for p in self.resolved_pairs if not p['resolved']]

        return resolved, unresolved


# =============================================================================
# Main Summary Builder
# =============================================================================

class TranscriptSummaryBuilder:
    """
    Builds a concise summary from transcript JSONL lines during streaming copy.

    Usage:
        builder = TranscriptSummaryBuilder(session_id, transcript_path)
        for line in source_file:
            dest_file.write(line)
            builder.process_line(line)  # Safe - catches all exceptions
        summary = builder.finalize()
    """

    def __init__(self, session_id: str, transcript_path: str):
        self.session_id = session_id
        self.transcript_path = transcript_path
        self.start_time = datetime.now()

        # Counters
        self.message_count = 0
        self.tool_call_count = 0

        # Collected data
        self.user_messages: list[dict] = []        # High-signal user messages
        self.tool_counts: dict[str, int] = {}       # Tool usage counts
        self.recent_tools: list[dict] = []          # Last N tool calls
        self.last_user_request: str | None = None   # Most recent user message
        self.todo_states: list[dict] = []           # Final todo state
        self.files_modified: dict[str, int] = {}    # {file_path: edit_count}

        # Error tracking
        self.error_tracker = ErrorTracker()

        # Timestamps
        self.first_timestamp: str | None = None
        self.last_timestamp: str | None = None

    def process_line(self, line: str):
        """
        Process a single JSONL line. Safe - catches all exceptions.
        Called during streaming copy for zero-overhead summarization.
        """
        try:
            line = line.strip()
            if not line:
                return

            entry = json.loads(line)
            self._process_entry(entry)

        except json.JSONDecodeError:
            pass  # Skip malformed lines
        except Exception:
            pass  # Never fail the backup

    def _process_entry(self, entry: dict):
        """Process a parsed transcript entry."""
        self.message_count += 1

        # Track timestamps
        timestamp = entry.get('timestamp')
        if timestamp:
            if self.first_timestamp is None:
                self.first_timestamp = timestamp
            self.last_timestamp = timestamp

        # Claude transcript format: content is nested in 'message' object
        # Format: {type: 'user'|'assistant', message: {role: '...', content: '...'}}
        message = entry.get('message', {})
        entry_type = entry.get('type', '')

        # Determine role: check entry.type first, then message.role
        role = entry_type if entry_type in ('user', 'assistant') else message.get('role', '')

        # Process by role
        if role == 'user':
            self._process_user_message(entry, message)
        elif role == 'assistant':
            self._process_assistant_message(entry, message)

    def _process_user_message(self, entry: dict, message: dict):
        """Process a user message, extracting signals."""
        # Content comes from the nested message object, not entry directly
        content = extract_content(message) if message else extract_content(entry)

        if len(content) < 10:
            return  # Skip very short messages

        # Filter out system-injected content (not actual user input)
        is_system_content = (
            content.startswith('[Result:') or
            content.startswith('<') and '>' in content[:50] or
            'This session is being continued' in content or
            entry.get('isMeta', False)  # Meta messages are system-injected
        )

        # Only update last_user_request for actual user input
        if not is_system_content:
            self.last_user_request = truncate_at_sentence(content, 500)

        # Detect signals
        signals = detect_correction_signals(content)
        signal_type = classify_message_signal(content)

        # Only include in summary if:
        # 1. Has interesting signals or is substantial
        # 2. Is NOT system-injected content (unless it has correction signals)
        is_high_signal = bool(signals) or signal_type in ['correction', 'question']
        is_substantial = len(content) > 50

        # Skip system content unless it has correction patterns (might be user corrections)
        if is_system_content and not signals:
            return

        if is_high_signal or is_substantial:
            self.user_messages.append({
                'index': self.message_count,
                'timestamp': entry.get('timestamp'),
                'content_preview': truncate_at_sentence(content, 500),
                'signals': [signal_type] if signal_type != 'other' else [],
                'matched_patterns': signals[:5],  # Limit to top 5 patterns
                'is_system': is_system_content  # Mark for context
            })

        # Check for errors in user messages (paste from console)
        error_type = detect_error_type(content)
        if error_type:
            self.error_tracker.record_error(error_type, content, self.message_count)

    def _process_assistant_message(self, entry: dict, message: dict):
        """Process an assistant message, tracking tool calls and resolutions."""
        # Content comes from the nested message object
        content = message.get('content', []) if message else entry.get('content', [])

        if not isinstance(content, list):
            # Simple text response - check for embedded errors/resolutions
            content_str = str(content)
            self._check_errors_and_resolutions(content_str)
            return

        # Process content blocks
        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get('type', '')

            if block_type == 'tool_use':
                self._process_tool_use(block)
            elif block_type == 'tool_result':
                self._process_tool_result(block)
            elif block_type == 'text':
                self._check_errors_and_resolutions(block.get('text', ''))

    def _process_tool_use(self, block: dict):
        """Track tool usage and file modifications."""
        tool_name = block.get('name', 'unknown')
        input_data = block.get('input', {})

        # Count by tool
        self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1
        self.tool_call_count += 1

        # Track recent calls (keep last 20)
        self.recent_tools.append({
            'tool': tool_name,
            'index': self.message_count
        })
        if len(self.recent_tools) > 20:
            self.recent_tools.pop(0)

        # Track file modifications (survives compaction via summary JSON)
        if tool_name in ('Edit', 'Write', 'NotebookEdit'):
            file_path = input_data.get('file_path') or input_data.get('notebook_path') or ''
            if file_path:
                self.files_modified[file_path] = self.files_modified.get(file_path, 0) + 1
        elif tool_name in ('mcp__filesystem__write_file', 'mcp__filesystem__edit_file'):
            file_path = input_data.get('path', '')
            if file_path:
                self.files_modified[file_path] = self.files_modified.get(file_path, 0) + 1

        # Check for TodoWrite - capture final state
        if tool_name == 'TodoWrite':
            todos = input_data.get('todos', [])
            if todos:
                self.todo_states = todos  # Keep overwriting with latest

    def _process_tool_result(self, block: dict):
        """Check tool results for errors/resolutions."""
        content = block.get('content', '')
        if isinstance(content, str):
            self._check_errors_and_resolutions(content)

            # Mark tool success/failure for recent tools
            is_error = block.get('is_error', False)
            if self.recent_tools and 'success' not in self.recent_tools[-1]:
                self.recent_tools[-1]['success'] = not is_error

    def _check_errors_and_resolutions(self, content: str):
        """Check content for errors and resolutions, updating tracker."""
        # Check for errors
        error_type = detect_error_type(content)
        if error_type:
            self.error_tracker.record_error(error_type, content, self.message_count)

        # Check for resolutions
        resolution_type = detect_resolution_type(content)
        if resolution_type:
            self.error_tracker.check_resolution(resolution_type, self.message_count)

        # Periodically expire old errors
        if self.message_count % 20 == 0:
            self.error_tracker.expire_old_errors(self.message_count)

    def finalize(self) -> dict:
        """
        Generate the final summary dictionary.
        Call after processing all lines.
        """
        # Finalize error tracking
        resolved_errors, unresolved_errors = self.error_tracker.finalize()

        # Build TDD feedback loops from resolved errors
        tdd_feedback_loops = [
            {
                'error_type': e['error_type'],
                'error_preview': e['error_preview'],
                'resolution_type': e['resolution_type'],
                'resolved': e['resolved']
            }
            for e in resolved_errors
        ]

        # Calculate duration
        duration_seconds = None
        if self.first_timestamp and self.last_timestamp:
            try:
                # Parse ISO timestamps
                start = datetime.fromisoformat(self.first_timestamp.replace('Z', '+00:00'))
                end = datetime.fromisoformat(self.last_timestamp.replace('Z', '+00:00'))
                duration_seconds = int((end - start).total_seconds())
            except Exception:
                pass

        # Build summary
        return {
            'schema_version': '1.1',
            'session_id': self.session_id,
            'generated_at': datetime.now().isoformat(),
            'transcript_path': self.transcript_path,

            'metadata': {
                'total_messages': self.message_count,
                'total_tool_calls': self.tool_call_count,
                'duration_seconds': duration_seconds,
                'first_timestamp': self.first_timestamp,
                'last_timestamp': self.last_timestamp,
            },

            'user_messages': self.user_messages[-50:],  # Keep last 50 high-signal messages

            'tdd_feedback_loops': tdd_feedback_loops,

            'tool_summary': {
                'by_tool': self.tool_counts,
                'recent_calls': self.recent_tools[-10:]  # Last 10 for quick reference
            },

            'errors': {
                'resolved': [e for e in resolved_errors],
                'unresolved': unresolved_errors
            },

            'task_state': {
                'last_user_request': self.last_user_request,
                'todo_final_state': self.todo_states
            },

            'files_modified': sorted(self.files_modified.keys()),
            'files_modified_counts': dict(sorted(self.files_modified.items())),
        }


def write_summary(summary_path: str, summary: dict) -> bool:
    """
    Write summary to file. Returns True on success.
    Uses ensure_ascii=False for readability, but handles encoding safely.
    """
    try:
        # Use ASCII to avoid Windows cp1252 issues (per Memory gotcha)
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=True)
        return True
    except Exception:
        return False
