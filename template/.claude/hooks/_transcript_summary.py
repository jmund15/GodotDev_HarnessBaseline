#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transcript Summary Builder - Creates concise summaries (~10-20KB) from transcripts.

Optimized for:
- Post-compaction resume (primary): task state, last request, todo status
- Correction/error->resolution extraction: what got corrected, what broke and how
  it got fixed — useful signal for any session, not just code ones
- Human review: readable narrative flow

Used by transcript_backup.py during streaming copy.
"""

import heapq
import json
import re
from datetime import datetime
from typing import Any


# =============================================================================
# Pattern Library
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
    r'\bfollow\b.*\brule\b',     # "follow the rule"
    r'\bdid\s+you\b',            # questions about actions
    r'\bwhy\s+did\b',            # questioning actions
    r'\bwhy\s+didn\'?t\b',       # questioning inaction
]

# Compile patterns for efficiency
COMPILED_CORRECTION_PATTERNS = [(p, re.compile(p, re.IGNORECASE)) for p in CORRECTION_PATTERNS]

# Process friction voiced by the user: blocking waits, regressions in how the agent works, workarounds.
FRICTION_PATTERNS = [
    r'\bblock(?:ing|ed)\b', r'\bused\s+to\b', r'\bunacceptable\b', r'\bannoying\b', r'\bworkaround\b',
    r'\bwhy\s+(?:do|are|is)\s+you\b', r'\bagain\b', r'\bstill\b', r'\bskipp?(?:ed|ing)\b', r'\bglossed\b',
    r'\bhindered\b', r'\bfriction\b', r'\bnever\s+again\b', r'\blapse\b',
]
COMPILED_FRICTION_PATTERNS = [(p, re.compile(p, re.IGNORECASE)) for p in FRICTION_PATTERNS]
COMPACTION_MARKER = 'This session is being continued'

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
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    parts.append(item.get('text', ''))
                elif item.get('type') == 'tool_use':
                    tool_name = item.get('name', 'unknown_tool')
                    parts.append(f"[Tool: {tool_name}]")
                elif item.get('type') == 'tool_result':
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


def detect_friction_signals(content: str) -> list[str]:
    return [p for p, c in COMPILED_FRICTION_PATTERNS if c.search(content)]


def _block_text(content) -> str:
    """Flatten a tool_result content field (str or list of text blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ' '.join(str(b.get('text', '')) for b in content if isinstance(b, dict))
    return str(content)


# A friction row is only actionable if the failing call can be REPRODUCED from it, and the old
# 200-char cap silently stripped the token that caused the failure (54 of 55 rows in one session).
# But unbounded is its own defect: 200 retained rows x a large tool input is hundreds of MB in the
# builder and a multi-MB digest. 8 KB reproduces any real shell command and bounds the file.
_INPUT_FULL_CAP = 8192


def _tool_input_value(name: str, input_data: dict) -> str:
    """The input string a friction row is about, bounded at `_INPUT_FULL_CAP`."""
    # Every identifying field, not the first truthy one: a failed Write keeps `file_path` and
    # drops `content`, and a failed Edit drops both strings — neither row can be reproduced.
    parts = []
    for key in ('command', 'file_path', 'pattern', 'query', 'prompt',
                'old_string', 'new_string', 'content', 'description'):
        v = input_data.get(key)
        if v:
            parts.append(f"{key}={v}" if len(parts) or key != 'command' else str(v))
    if parts:
        return " | ".join(parts)[:_INPUT_FULL_CAP]
    return json.dumps(input_data)[:_INPUT_FULL_CAP] if input_data else ''


def _tool_input_excerpt(name: str, input_data: dict) -> str:
    """Display-length excerpt. Reproduction uses the fuller bounded value instead."""
    return _tool_input_value(name, input_data)[:200]


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

    truncated = content[:max_len]

    for ending in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
        last_idx = truncated.rfind(ending)
        if last_idx > max_len // 2:  # Don't truncate too aggressively
            return truncated[:last_idx + 1].strip() + '...'

    last_space = truncated.rfind(' ')
    if last_space > max_len // 2:
        return truncated[:last_space].strip() + '...'

    return truncated.strip() + '...'


def classify_message_signal(content: str) -> str:
    """Classify the primary signal type of a user message."""
    content_lower = content.lower()

    if '?' in content and any(w in content_lower for w in ['why', 'how', 'what', 'where', 'when', 'did you']):
        return 'question'

    if any(compiled.search(content_lower) for _, compiled in COMPILED_CORRECTION_PATTERNS[:10]):  # High-signal patterns
        return 'correction'

    if any(w in content_lower for w in ['implement', 'create', 'add', 'fix', 'update', 'run', 'make']):
        return 'instruction'

    if any(w in content_lower for w in ['yes', 'ok', 'good', 'looks good', 'approved', 'lgtm', 'proceed']):
        return 'approval'

    return 'other'


# =============================================================================
# Error Tracker (error -> resolution pairing)
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
        compatibility = {
            'build_success': ['build_error'],
            'test_pass': ['test_failure'],
        }

        compatible_errors = compatibility.get(resolution_type, [])

        for i, error in enumerate(self.pending_errors):
            if error['type'] in compatible_errors:
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

class ContextCensus:
    """Observed main-transcript usage and payload sizes, never an estimate of live context."""

    USAGE_FIELDS = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')

    def __init__(self):
        self.coverage = dict(malformed_rows=0, invalid_rows=0, excluded_sidechain_rows=0,
                             assistant_rows_without_message_id=0, tool_results_without_id=0,
                             attachments_without_id=0, processing_errors=0)
        self.messages = {}
        self.tool_names = {}
        self.results = {}
        self.attachments = {}
        self.boundaries = {}
        self.pending = []
        self.row_number = 0

    @staticmethod
    def _tokens(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    @staticmethod
    def _bytes(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))

    @staticmethod
    def _text_bytes(value):
        if isinstance(value, str):
            return len(value.encode('utf-8'))
        if isinstance(value, list):
            parts = [b.get('text') for b in value if isinstance(b, dict) and isinstance(b.get('text'), str)]
            return sum(len(p.encode('utf-8')) for p in parts) if parts or not value else None
        return None

    def observe(self, entry):
        self.row_number += 1
        if not isinstance(entry, dict):
            self.coverage['invalid_rows'] += 1
            return
        if entry.get('isSidechain'):
            self.coverage['excluded_sidechain_rows'] += 1
            return
        row_id = entry.get('uuid') or f'row:{self.row_number}'
        if entry.get('subtype') == 'compact_boundary':
            if row_id not in self.boundaries:
                metadata = entry.get('compactMetadata') or entry.get('compact_metadata') or entry.get('metadata') or {}
                if not isinstance(metadata, dict):
                    metadata = {}
                self.boundaries[row_id] = dict(
                    id=row_id, timestamp=entry.get('timestamp'), trigger=metadata.get('trigger'),
                    pre_tokens=self._tokens(metadata.get('preTokens')),
                    post_tokens=self._tokens(metadata.get('postTokens')),
                    duration_ms=self._tokens(metadata.get('durationMs')),
                    next_assistant_id=None, attachment_body_utf8_bytes={})
                self.pending.append(row_id)
            return

        message = entry.get('message')
        message = message if isinstance(message, dict) else {}
        if entry.get('type') == 'assistant':
            message_id = message.get('id')
            if not message_id:
                self.coverage['assistant_rows_without_message_id'] += 1
                message_id = row_id
            if message_id not in self.messages:
                self.messages[message_id] = dict(message_id=message_id, timestamp=entry.get('timestamp'),
                                                 model=message.get('model'), usage={k: None for k in self.USAGE_FIELDS})
                for boundary_id in self.pending:
                    self.boundaries[boundary_id]['next_assistant_id'] = message_id
                self.pending.clear()
            record = self.messages[message_id]
            usage = message.get('usage')
            if isinstance(usage, dict):
                for field in self.USAGE_FIELDS:
                    value = self._tokens(usage.get(field))
                    if value is not None:
                        record['usage'][field] = value

        content = message.get('content', [])
        for index, block in enumerate(content if isinstance(content, list) else []):
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and entry.get('type') == 'assistant':
                if block.get('id'):
                    self.tool_names[block['id']] = block.get('name') or 'unknown'
            elif block.get('type') == 'tool_result':
                tool_id = block.get('tool_use_id')
                if not tool_id:
                    self.coverage['tool_results_without_id'] += 1
                key = tool_id or f'{row_id}:{index}'
                self.results[key] = dict(id=key, tool_id=tool_id,
                                         text_utf8_bytes=self._text_bytes(block.get('content')),
                                         payload_utf8_bytes=self._bytes(block))

        if entry.get('type') == 'attachment':
            item = entry.get('attachment')
            if not isinstance(item, dict):
                self.coverage['invalid_rows'] += 1
                return
            if not entry.get('uuid'):
                self.coverage['attachments_without_id'] += 1
            kind = item.get('type') or 'unknown'
            body = self._text_bytes(item.get('content', item.get('text')))
            if kind in ('instructions', 'invoked_skills'):
                members = item.get('files' if kind == 'instructions' else 'skills')
                if isinstance(members, list) and all(isinstance(m, dict) and isinstance(m.get('content'), str) for m in members):
                    body = sum(len(m['content'].encode('utf-8')) for m in members)
            if row_id not in self.attachments:
                self.attachments[row_id] = dict(type=kind, body_utf8_bytes=body,
                                                payload_utf8_bytes=self._bytes(item))
                if kind != 'prompt_snapshot' and body is not None:
                    for boundary_id in self.pending:
                        sizes = self.boundaries[boundary_id]['attachment_body_utf8_bytes']
                        sizes[kind] = sizes.get(kind, 0) + body

    @staticmethod
    def _sum_observed(values):
        measured = [v for v in values if v is not None]
        return sum(measured) if measured else None

    def _message(self, key):
        if key is None:
            return None
        record = self.messages[key]
        values = [record['usage'][field] for field in self.USAGE_FIELDS[:3]]
        return {**record, 'reported_input_sum': sum(values) if all(v is not None for v in values) else None}

    def finalize(self):
        usage = {}
        for field in self.USAGE_FIELDS:
            values = [m['usage'][field] for m in self.messages.values()]
            measured = sum(v is not None for v in values)
            usage[field] = dict(observed_total=self._sum_observed(values), measured_messages=measured,
                                missing_messages=len(values) - measured)
        by_tool = {}
        for record in self.results.values():
            name = self.tool_names.get(record['tool_id'], 'unknown')
            row = by_tool.setdefault(name, dict(count=0, text_utf8_bytes=0, payload_utf8_bytes=0, missing_text_results=0))
            row['count'] += 1
            row['payload_utf8_bytes'] += record['payload_utf8_bytes']
            if record['text_utf8_bytes'] is None:
                row['missing_text_results'] += 1
            else:
                row['text_utf8_bytes'] += record['text_utf8_bytes']
        by_type = {}
        for record in self.attachments.values():
            row = by_type.setdefault(record['type'], dict(count=0, body_utf8_bytes=0, payload_utf8_bytes=0, missing_body_records=0))
            row['count'] += 1
            row['payload_utf8_bytes'] += record['payload_utf8_bytes']
            if record['body_utf8_bytes'] is None:
                row['missing_body_records'] += 1
            else:
                row['body_utf8_bytes'] += record['body_utf8_bytes']
        boundaries = []
        for record in self.boundaries.values():
            row = {k: v for k, v in record.items() if k != 'next_assistant_id'}
            row['next_assistant'] = self._message(record['next_assistant_id'])
            boundaries.append(row)
        return dict(
            schema_version=1, coverage=self.coverage, assistant_messages=len(self.messages), usage=usage,
            first_assistant=self._message(next(iter(self.messages), None)), boundaries=boundaries,
            tool_results=dict(count=len(self.results), by_tool=by_tool,
                              text_utf8_bytes=self._sum_observed([r['text_utf8_bytes'] for r in self.results.values()]),
                              largest=heapq.nlargest(20, self.results.values(),
                                                     key=lambda r: r['payload_utf8_bytes'])),
            attachments=dict(by_type=by_type),
            limits=['Usage is provider-reported, not wire-attested; repeated message IDs are merged.',
                    'Payload/body UTF-8 bytes are cumulative transcript evidence, not live context tokens.',
                    'Attachment records do not prove exact rendered prompt bytes; prompt_snapshot is diagnostic.',
                    'Missing IDs limit deduplication; absent usage is unknown, not zero.'])


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

        self.message_count = 0
        self.tool_call_count = 0

        self.user_messages: list[dict] = []        # High-signal user messages
        self.tool_counts: dict[str, int] = {}       # Tool usage counts
        self.recent_tools: list[dict] = []          # Last N tool calls
        self.last_user_request: str | None = None   # Most recent user message
        self.todo_states: list[dict] = []           # Final todo state
        self.files_modified: dict[str, int] = {}    # {file_path: edit_count}

        self.error_tracker = ErrorTracker()
        self.context_census = ContextCensus()

        self.friction: list[dict] = []              # Tool errors/denials + the assistant's next move
        self._tool_inputs: dict[str, tuple] = {}    # tool_use_id -> (tool, input excerpt)
        self._open_friction: dict | None = None     # Last friction row awaiting the assistant's response
        self.compaction_markers: list[str | None] = []  # Timestamps of continuation summaries

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
            try:
                self.context_census.observe(entry)
            except Exception:
                self.context_census.coverage['processing_errors'] += 1
            if isinstance(entry, dict):
                self._process_entry(entry)

        except json.JSONDecodeError:
            self.context_census.coverage['malformed_rows'] += 1
        except Exception:
            pass  # Never fail the backup

    def _process_entry(self, entry: dict):
        """Process a parsed transcript entry."""
        self.message_count += 1

        timestamp = entry.get('timestamp')
        if timestamp:
            if self.first_timestamp is None:
                self.first_timestamp = timestamp
            self.last_timestamp = timestamp

        message = entry.get('message', {})
        entry_type = entry.get('type', '')

        role = entry_type if entry_type in ('user', 'assistant') else message.get('role', '')

        if role == 'user':
            self._process_user_message(entry, message)
        elif role == 'assistant':
            self._process_assistant_message(entry, message)

    def _process_user_message(self, entry: dict, message: dict):
        """Record every real user prompt verbatim; route tool_result rows to friction capture."""
        if entry.get('isSidechain'):
            return  # Subagent prompts are the orchestrator's, not the user's

        raw = (message or {}).get('content', entry.get('content', ''))
        if isinstance(raw, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in raw):
            for block in raw:
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    self._process_tool_result(block, entry)
            return

        content = extract_content(message) if message else extract_content(entry)

        if COMPACTION_MARKER in content:
            self.compaction_markers.append(entry.get('timestamp'))
            return

        if content.startswith('<command-name>'):
            # Slash-command row: the user's words are the args; the rest is harness plumbing.
            name = re.search(r'<command-name>(.*?)</command-name>', content, re.S)
            args = re.search(r'<command-args>(.*?)</command-args>', content, re.S)
            args_text = (args.group(1).strip() if args else '')
            if not args_text:
                return
            content = f"{name.group(1).strip() if name else ''} {args_text}".strip()
        elif content.startswith('<') and '>' in content[:50] or entry.get('isMeta', False):
            return  # Hook output / system injection

        if content.startswith('[Request interrupted'):
            self.user_messages.append({'index': self.message_count, 'timestamp': entry.get('timestamp'),
                                       'content': content[:120], 'signals': ['interrupt'], 'matched_patterns': []})
            return

        if len(content) < 10:
            return

        self.last_user_request = truncate_at_sentence(content, 500)

        signals = detect_correction_signals(content)
        signal_type = classify_message_signal(content)
        friction = detect_friction_signals(content)

        self.user_messages.append({
            'index': self.message_count,
            'timestamp': entry.get('timestamp'),
            'content': truncate_at_sentence(content, 2000),
            'signals': ([signal_type] if signal_type != 'other' else []) + (['friction'] if friction else []),
            'matched_patterns': (signals + friction)[:6],
        })

        error_type = detect_error_type(content)
        if error_type:
            self.error_tracker.record_error(error_type, content, self.message_count)

    def _process_assistant_message(self, entry: dict, message: dict):
        """Process an assistant message, tracking tool calls and resolutions."""
        if entry.get('isSidechain'):
            return

        content = message.get('content', []) if message else entry.get('content', [])

        if not isinstance(content, list):
            content_str = str(content)
            self._check_errors_and_resolutions(content_str)
            return

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get('type', '')

            if block_type == 'tool_use':
                self._process_tool_use(block)
            elif block_type == 'tool_result':
                self._process_tool_result(block)
            elif block_type == 'text':
                text = block.get('text', '')
                if self._open_friction is not None and text.strip():
                    self._open_friction['response'] = truncate_at_sentence(text.strip(), 400)
                    self._open_friction = None
                self._check_errors_and_resolutions(text)

    def _process_tool_use(self, block: dict):
        """Track tool usage and file modifications."""
        tool_name = block.get('name', 'unknown')
        input_data = block.get('input', {})

        self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1
        self.tool_call_count += 1

        if self._open_friction is not None:
            self._open_friction['response'] = f"{tool_name}: {_tool_input_excerpt(tool_name, input_data if isinstance(input_data, dict) else {})}"
            self._open_friction = None

        tool_id = block.get('id')
        if tool_id:
            _full = _tool_input_value(tool_name, input_data if isinstance(input_data, dict) else {})
            self._tool_inputs[tool_id] = (tool_name, _full[:200], _full)
            if len(self._tool_inputs) > 500:
                self._tool_inputs.pop(next(iter(self._tool_inputs)))

        self.recent_tools.append({
            'tool': tool_name,
            'index': self.message_count
        })
        if len(self.recent_tools) > 20:
            self.recent_tools.pop(0)

        if tool_name in ('Edit', 'Write', 'NotebookEdit'):
            file_path = input_data.get('file_path') or input_data.get('notebook_path') or ''
            if file_path:
                self.files_modified[file_path] = self.files_modified.get(file_path, 0) + 1
        elif tool_name in ('mcp__filesystem__write_file', 'mcp__filesystem__edit_file'):
            file_path = input_data.get('path', '')
            if file_path:
                self.files_modified[file_path] = self.files_modified.get(file_path, 0) + 1

        if tool_name == 'TodoWrite':
            todos = input_data.get('todos', [])
            if todos:
                self.todo_states = todos  # Keep overwriting with latest

    def _process_tool_result(self, block: dict, entry: dict | None = None):
        """Check tool results for errors/resolutions; an is_error result opens a friction row."""
        content = _block_text(block.get('content', ''))
        self._check_errors_and_resolutions(content)

        is_error = bool(block.get('is_error', False))
        if self.recent_tools and 'success' not in self.recent_tools[-1]:
            self.recent_tools[-1]['success'] = not is_error

        if is_error:
            tool, excerpt, full = self._tool_inputs.get(
                block.get('tool_use_id'), ('unknown', '', ''))
            row = {
                'index': self.message_count,
                'timestamp': (entry or {}).get('timestamp'),
                'tool': tool,
                'input': excerpt,
                # The reproducible call. `input` stays short for rendering; a fixture or a
                # repro built off `input` alone loses whatever sat past char 200.
                'input_full': full,
                'error': truncate_at_sentence(content.strip(), 300),
                'denied': bool(re.search(r'\b(BLOCKED|denied|deny|not allowed|guardrail)\b', content, re.I)),
                'response': None,
            }
            self.friction.append(row)
            self._open_friction = row

    def _check_errors_and_resolutions(self, content: str):
        """Check content for errors and resolutions, updating tracker."""
        error_type = detect_error_type(content)
        if error_type:
            self.error_tracker.record_error(error_type, content, self.message_count)

        resolution_type = detect_resolution_type(content)
        if resolution_type:
            self.error_tracker.check_resolution(resolution_type, self.message_count)

        if self.message_count % 20 == 0:
            self.error_tracker.expire_old_errors(self.message_count)

    def finalize(self) -> dict:
        """
        Generate the final summary dictionary.
        Call after processing all lines.
        """
        resolved_errors, unresolved_errors = self.error_tracker.finalize()

        feedback_loops = [
            {
                'error_type': e['error_type'],
                'error_preview': e['error_preview'],
                'resolution_type': e['resolution_type'],
                'resolved': e['resolved']
            }
            for e in resolved_errors
        ]

        duration_seconds = None
        if self.first_timestamp and self.last_timestamp:
            try:
                start = datetime.fromisoformat(self.first_timestamp.replace('Z', '+00:00'))
                end = datetime.fromisoformat(self.last_timestamp.replace('Z', '+00:00'))
                duration_seconds = int((end - start).total_seconds())
            except Exception:
                pass

        return {
            'schema_version': '1.2',
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

            'user_messages': self.user_messages[-400:],  # Every real prompt, verbatim (cap is a safety net)

            'compactions': {'count': len(self.compaction_markers), 'timestamps': self.compaction_markers},
            'context_census': self.context_census.finalize(),
            'friction': self.friction[-200:],

            'error_resolution_pairs': feedback_loops,

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
    """
    try:
        # ASCII to avoid Windows cp1252 encode issues on write.
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=True)
        return True
    except Exception:
        return False
