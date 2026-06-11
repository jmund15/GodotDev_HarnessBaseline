#!/usr/bin/env python3
"""
Godot Log Analyzer for {{PROJECT_NAME}}

Analyzes Godot log files with structured multi-line block parsing, mode-based
output presets, and configurable JSON-stripping for token efficiency.

Usage:
    python analyze_godot_logs.py [log_path] [options]

    Summary (default):    python analyze_godot_logs.py
    Errors only:          python analyze_godot_logs.py --mode errors
    Last 20 blocks:       python analyze_godot_logs.py --mode tail --last 20
    Tag frequencies:      python analyze_godot_logs.py --mode tags
    Targeted timeline:    python analyze_godot_logs.py --target HSM Transition
    Previous-run logs:    python analyze_godot_logs.py --log previous --mode errors

    JSON output (for agents): add --json to any of the above.

Token-efficiency flags (apply to --json output):
    --include-raw        Include raw_lines field (default OFF — saves ~50% size)
    --max-message N      Truncate message field at N chars (default 300)
    --no-backtrace       Drop backtrace field entirely
    --fields F1,F2,...   Whitelist specific block fields
"""

import re
import sys
import argparse
import json
import platform
import glob
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from collections import Counter, defaultdict

# Force UTF-8 stdout on Windows (cp1252 can't handle JmoLogger's → arrows etc.)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Default log directory (platform-dependent)
_system = platform.system()
if _system == "Windows":
    DEFAULT_LOG_DIR = Path.home() / "AppData/Roaming/Godot/app_userdata/{{PROJECT_NAME}}/logs"
elif _system == "Darwin":
    DEFAULT_LOG_DIR = Path.home() / "Library/Application Support/Godot/app_userdata/{{PROJECT_NAME}}/logs"
else:
    DEFAULT_LOG_DIR = Path.home() / ".local/share/godot/app_userdata/{{PROJECT_NAME}}/logs"

DEFAULT_LOG_PATH = DEFAULT_LOG_DIR / "godot.log"


# --- Log file selection --------------------------------------------------

def resolve_log_path(spec: str | None) -> Path:
    """
    Resolve a log spec to a concrete file path. Spec semantics:
      None        -> DEFAULT_LOG_PATH (the active godot.log)
      "latest"    -> DEFAULT_LOG_PATH (alias)
      "previous"  -> second-most-recent timestamped log file
      "<int>"     -> Nth-most-recent (0 = latest active, 1 = previous, ...)
      "<path>"    -> explicit path (absolute or relative)

    Why "previous" matters: Godot truncates `godot.log` on every launch. After
    a crash + quit, "the active log" is empty; the timestamped historical file
    next to it is what holds the crash state.
    """
    if spec is None or spec == "latest":
        return DEFAULT_LOG_PATH

    # Numeric index into the timestamped-files list
    try:
        idx = int(spec)
        return _nth_most_recent_log(idx)
    except ValueError:
        pass

    if spec == "previous":
        return _nth_most_recent_log(1)

    # Treat as explicit path
    return Path(spec)


def _nth_most_recent_log(n: int) -> Path:
    """Return Nth-most-recent log file (0 = latest active, 1 = previous, ...)."""
    if n == 0:
        return DEFAULT_LOG_PATH
    # Glob timestamped logs, sort by mtime descending
    pattern = str(DEFAULT_LOG_DIR / "godot*.log")
    matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    # Filter out the active `godot.log` so n=1 means "first historical"
    historical = [p for p in matches if Path(p).name != "godot.log"]
    if not historical:
        return DEFAULT_LOG_PATH  # fallback — better than failing
    # n=1 -> index 0 in historical list, n=2 -> index 1, etc.
    target_idx = n - 1
    if target_idx >= len(historical):
        target_idx = len(historical) - 1
    return Path(historical[target_idx])


# --- Regex patterns ------------------------------------------------------

# JmoLogger context line (INFO/DEBUG — no severity prefix from Godot)
RE_CONTEXT = re.compile(
    r"^\[(\w+)\s+@\s+'([^']+)'\](?:\s*\(Owner:\s*([^)]+)\))?\s*$"
)

# JmoLogger context line duplicated by GD.PushWarning/PushError
RE_WARN_CONTEXT = re.compile(
    r"^(?:WARNING|ERROR):\s*\[(\w+)\s+@\s+'([^']+)'\](?:\s*\(Owner:\s*([^)]+)\))?\s*$"
)

# JmoLogger severity line with source location
RE_SEVERITY = re.compile(
    r"^(INFO|DEBUG|WARNING|ERROR|EXCEPTION|HANDLED EXCEPTION):\s*(.+?)\s+@\s+(\S+:\d+)\s+in\s+(\w+\(\))\s*$"
)

# Severity line WITHOUT source location (rare)
RE_SEVERITY_BARE = re.compile(
    r"^(WARNING|ERROR):\s*(.+)$"
)

# Backtrace indicators
RE_BACKTRACE_AT = re.compile(r"^\s+at:\s+(.+)$")
RE_BACKTRACE_HEADER = re.compile(r"^\s+C# backtrace")
RE_BACKTRACE_FRAME = re.compile(r"^\s+\[(\d+)\]\s+(.+)$")

# Godot engine line
RE_GODOT_AT = re.compile(r"^\s+at:\s+\S+\s+\(.+\.(cpp|h):\d+\)\s*$")

# Tags within messages: [HSM], [DIAG], [SpawnEffect], etc.
RE_TAG = re.compile(r"\[(\w+)\]")

# Boilerplate lines
RE_BOILERPLATE = re.compile(r"^(Godot Engine v|Vulkan |OpenGL )")


@dataclass
class LogBlock:
    """A parsed log entry — one logical event, potentially spanning multiple lines."""
    line_number: int
    block_type: str          # "jmologger" | "godot_engine" | "bare"
    level: str               # "INFO" | "DEBUG" | "WARNING" | "ERROR" | "EXCEPTION" | "NONE"
    type_name: str = ""
    node_path: str = ""
    owner_path: str = ""
    message: str = ""
    source_file: str = ""
    method: str = ""
    tags: list = field(default_factory=list)
    backtrace: list = field(default_factory=list)
    raw_lines: list = field(default_factory=list)


# --- State machine parser ------------------------------------------------

def parse_log(lines: list[str]) -> list[LogBlock]:
    """Parse log lines into structured LogBlock entries using a state machine."""
    blocks = []
    state = "IDLE"

    pending_ctx = None
    pending_block = None
    pending_bt_lines = []

    def finalize_backtrace():
        nonlocal pending_block, pending_bt_lines
        if pending_block and pending_bt_lines:
            pending_block.backtrace = compress_backtrace(pending_bt_lines)
            pending_block.raw_lines.extend(pending_bt_lines)
            pending_bt_lines = []

    def emit_block(block):
        if block.message:
            block.tags = RE_TAG.findall(block.message)
        blocks.append(block)

    def emit_bare(line_num, text, raw):
        emit_block(LogBlock(
            line_number=line_num, block_type="bare", level="NONE",
            message=text, raw_lines=[raw],
        ))

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        line_num = i + 1

        if not line:
            continue

        if state == "IDLE":
            m = RE_CONTEXT.match(line)
            if m:
                pending_ctx = (line_num, m.group(1), m.group(2), m.group(3) or "", line)
                state = "CONTEXT_SEEN"
                continue

            m = RE_WARN_CONTEXT.match(line)
            if m:
                pending_ctx = (line_num, m.group(1), m.group(2), m.group(3) or "", line)
                state = "CONTEXT_SEEN"
                continue

            m = RE_SEVERITY.match(line)
            if m:
                block = LogBlock(
                    line_number=line_num, block_type="jmologger",
                    level=m.group(1), message=m.group(2),
                    source_file=m.group(3), method=m.group(4),
                    raw_lines=[line],
                )
                pending_block = block
                emit_block(block)
                state = "SEVERITY_SEEN"
                continue

            m = RE_SEVERITY_BARE.match(line)
            if m:
                block = LogBlock(
                    line_number=line_num, block_type="godot_engine",
                    level=m.group(1), message=m.group(2),
                    raw_lines=[line],
                )
                pending_block = block
                emit_block(block)
                state = "SEVERITY_SEEN"
                continue

            if RE_BOILERPLATE.match(line):
                continue

            emit_bare(line_num, line, line)

        elif state == "CONTEXT_SEEN":
            ctx_ln, ctx_type, ctx_path, ctx_owner, ctx_raw = pending_ctx

            m = RE_SEVERITY.match(line)
            if m:
                block = LogBlock(
                    line_number=ctx_ln, block_type="jmologger",
                    level=m.group(1), type_name=ctx_type,
                    node_path=ctx_path, owner_path=ctx_owner,
                    message=m.group(2), source_file=m.group(3),
                    method=m.group(4), raw_lines=[ctx_raw, line],
                )
                pending_block = block
                emit_block(block)
                pending_ctx = None
                state = "SEVERITY_SEEN"
                continue

            m = RE_SEVERITY_BARE.match(line)
            if m:
                block = LogBlock(
                    line_number=ctx_ln, block_type="jmologger",
                    level=m.group(1), type_name=ctx_type,
                    node_path=ctx_path, owner_path=ctx_owner,
                    message=m.group(2), raw_lines=[ctx_raw, line],
                )
                pending_block = block
                emit_block(block)
                pending_ctx = None
                state = "SEVERITY_SEEN"
                continue

            emit_bare(ctx_ln, ctx_raw, ctx_raw)
            pending_ctx = None
            state = "IDLE"
            m2 = RE_CONTEXT.match(line)
            if m2:
                pending_ctx = (line_num, m2.group(1), m2.group(2), m2.group(3) or "", line)
                state = "CONTEXT_SEEN"
                continue
            m2 = RE_WARN_CONTEXT.match(line)
            if m2:
                pending_ctx = (line_num, m2.group(1), m2.group(2), m2.group(3) or "", line)
                state = "CONTEXT_SEEN"
                continue
            if not RE_BOILERPLATE.match(line):
                emit_bare(line_num, line, line)

        elif state == "SEVERITY_SEEN":
            if RE_BACKTRACE_AT.match(line) or RE_BACKTRACE_HEADER.match(line):
                pending_bt_lines = [line]
                state = "BACKTRACE"
                continue

            pending_block = None
            state = "IDLE"
            m = RE_CONTEXT.match(line)
            if m:
                pending_ctx = (line_num, m.group(1), m.group(2), m.group(3) or "", line)
                state = "CONTEXT_SEEN"
                continue
            m = RE_WARN_CONTEXT.match(line)
            if m:
                pending_ctx = (line_num, m.group(1), m.group(2), m.group(3) or "", line)
                state = "CONTEXT_SEEN"
                continue
            m = RE_SEVERITY.match(line)
            if m:
                block = LogBlock(
                    line_number=line_num, block_type="jmologger",
                    level=m.group(1), message=m.group(2),
                    source_file=m.group(3), method=m.group(4),
                    raw_lines=[line],
                )
                pending_block = block
                emit_block(block)
                state = "SEVERITY_SEEN"
                continue
            m = RE_SEVERITY_BARE.match(line)
            if m:
                block = LogBlock(
                    line_number=line_num, block_type="godot_engine",
                    level=m.group(1), message=m.group(2),
                    raw_lines=[line],
                )
                pending_block = block
                emit_block(block)
                state = "SEVERITY_SEEN"
                continue
            if not RE_BOILERPLATE.match(line):
                emit_bare(line_num, line, line)

        elif state == "BACKTRACE":
            if RE_BACKTRACE_FRAME.match(line) or RE_BACKTRACE_HEADER.match(line) or RE_BACKTRACE_AT.match(line) or RE_GODOT_AT.match(line):
                pending_bt_lines.append(line)
                continue

            finalize_backtrace()
            pending_block = None
            state = "IDLE"
            m = RE_CONTEXT.match(line)
            if m:
                pending_ctx = (line_num, m.group(1), m.group(2), m.group(3) or "", line)
                state = "CONTEXT_SEEN"
                continue
            m = RE_WARN_CONTEXT.match(line)
            if m:
                pending_ctx = (line_num, m.group(1), m.group(2), m.group(3) or "", line)
                state = "CONTEXT_SEEN"
                continue
            m = RE_SEVERITY.match(line)
            if m:
                block = LogBlock(
                    line_number=line_num, block_type="jmologger",
                    level=m.group(1), message=m.group(2),
                    source_file=m.group(3), method=m.group(4),
                    raw_lines=[line],
                )
                pending_block = block
                emit_block(block)
                state = "SEVERITY_SEEN"
                continue
            m = RE_SEVERITY_BARE.match(line)
            if m:
                block = LogBlock(
                    line_number=line_num, block_type="godot_engine",
                    level=m.group(1), message=m.group(2),
                    raw_lines=[line],
                )
                pending_block = block
                emit_block(block)
                state = "SEVERITY_SEEN"
                continue
            if not RE_BOILERPLATE.match(line):
                emit_bare(line_num, line, line)

    if state == "CONTEXT_SEEN" and pending_ctx:
        emit_bare(pending_ctx[0], pending_ctx[4], pending_ctx[4])
    elif state == "BACKTRACE":
        finalize_backtrace()

    return blocks


def compress_backtrace(bt_lines: list[str]) -> list[str]:
    """Extract user-relevant stack frames ({{PROJECT_NAME}}/Jmodot, excluding generated)."""
    user_frames = []
    for line in bt_lines:
        m = RE_BACKTRACE_FRAME.match(line)
        if not m:
            continue
        frame_text = m.group(2)
        if ".generated.cs" in frame_text:
            continue
        if "GodotSharp" in frame_text or "NativeInterop" in frame_text:
            continue
        if "ScriptManagerBridge" in frame_text or "CSharpInstanceBridge" in frame_text:
            continue
        paren_idx = frame_text.rfind("(")
        if paren_idx > 0 and frame_text.endswith(")"):
            path_part = frame_text[paren_idx + 1:-1]
            method_part = frame_text[:paren_idx].strip()
            sig_paren = method_part.find("(")
            if sig_paren > 0:
                method_part = method_part[:sig_paren] + "()"
            filename = path_part.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            user_frames.append(f"  {method_part} @ {filename}")
        else:
            user_frames.append(f"  {frame_text.strip()}")

    return user_frames[:5]


# --- Filtering -----------------------------------------------------------

def filter_blocks(blocks: list[LogBlock], target: list[str] | None,
                  target_any: list[str] | None, levels: list[str] | None,
                  node: str | None, type_filter: str | None) -> list[LogBlock]:
    """Filter blocks by search terms, levels, node path, and/or type name."""
    result = []
    for block in blocks:
        if levels and block.level.lower() not in levels:
            continue
        if node and node.lower() not in block.node_path.lower() and \
           node.lower() not in block.owner_path.lower():
            continue
        if type_filter and type_filter.lower() != block.type_name.lower():
            continue
        if target or target_any:
            searchable = _build_searchable(block)
            terms = target or target_any
            is_and = target is not None
            if is_and:
                if not all(t.lower() in searchable for t in terms):
                    continue
            else:
                if not any(t.lower() in searchable for t in terms):
                    continue
        result.append(block)
    return result


def _build_searchable(block: LogBlock) -> str:
    """Build a single lowercase string from all searchable fields."""
    parts = [
        block.type_name, block.node_path, block.owner_path,
        block.message, block.source_file, block.method,
        " ".join(block.tags),
    ]
    parts.extend(block.raw_lines)
    return " ".join(parts).lower()


# --- Block JSON shaping (token-efficiency) -------------------------------

# Default field whitelists by mode. None = include all.
_MODE_FIELD_DEFAULTS = {
    "errors": ["line_number", "level", "message", "source_file", "method", "type_name", "node_path"],
    "tail": None,         # Include all by default for tail; user can override
    "timeline": None,     # Include all by default
    "entity": None,
}


def _block_to_dict(block: LogBlock, *, fields: list[str] | None = None,
                    include_raw: bool = False, max_message: int = 300,
                    no_backtrace: bool = False) -> dict:
    """
    Convert a LogBlock to dict with token-efficiency shaping applied.
    - fields: whitelist of LogBlock attributes to include (None = all)
    - include_raw: keep raw_lines (default off — saves ~50% JSON size)
    - max_message: truncate message at N chars (None = no truncation)
    - no_backtrace: drop backtrace field entirely
    Empty list/string fields are also dropped to save tokens.
    """
    d = asdict(block)

    # Field whitelist
    if fields is not None:
        d = {k: v for k, v in d.items() if k in fields}

    # Strip raw_lines unless explicitly requested
    if not include_raw and "raw_lines" in d:
        d.pop("raw_lines", None)

    # Drop backtrace if requested
    if no_backtrace and "backtrace" in d:
        d.pop("backtrace", None)

    # Truncate message
    if max_message and "message" in d and isinstance(d["message"], str):
        if len(d["message"]) > max_message:
            d["message"] = d["message"][:max_message] + f"…(+{len(d['message']) - max_message})"

    # Drop empty fields (saves "field": "" or "field": [] tokens)
    d = {k: v for k, v in d.items() if v not in ("", [], None)}

    return d


# --- Output formatters ---------------------------------------------------

def format_timeline(blocks: list[LogBlock], total_blocks: int,
                    filter_desc: str, limit: int) -> str:
    """Format blocks as a chronological timeline (human-readable)."""
    shown = blocks[:limit]
    lines = [
        "=" * 55,
        "TARGETED LOG ANALYSIS",
        "=" * 55,
        f"Filter: {filter_desc}",
        f"Matching: {len(blocks)} of {total_blocks} total blocks"
        + (f" (showing first {limit})" if len(blocks) > limit else ""),
        "",
    ]

    if not shown:
        lines.append("No matching log entries found.")
        return "\n".join(lines)

    for block in shown:
        owner_tag = ""
        if block.owner_path:
            owner_name = block.owner_path.rsplit("/", 1)[-1]
            owner_tag = f" [Owner: {owner_name}]"

        level_str = block.level if block.level != "NONE" else "---"
        type_str = block.type_name if block.type_name else "(bare)"

        lines.append(f"--- [L{block.line_number}] {level_str} {type_str}{owner_tag} ---")
        lines.append(block.message)

        if block.source_file:
            lines.append(f"@ {block.source_file} in {block.method}")

        if block.backtrace:
            lines.append(f"Backtrace ({len(block.backtrace)} user frames):")
            lines.extend(block.backtrace)

        lines.append("")

    return "\n".join(lines)


def format_timeline_json(blocks: list[LogBlock], total_blocks: int,
                         filter_desc: str, *, mode: str = "timeline",
                         fields: list[str] | None = None,
                         include_raw: bool = False, max_message: int = 300,
                         no_backtrace: bool = False) -> dict:
    """Format blocks as structured JSON with token-efficiency shaping."""
    if fields is None:
        fields = _MODE_FIELD_DEFAULTS.get(mode)
    return {
        "mode": mode,
        "filter": filter_desc,
        "total_blocks": total_blocks,
        "matching_blocks": len(blocks),
        "blocks": [
            _block_to_dict(b, fields=fields, include_raw=include_raw,
                           max_message=max_message, no_backtrace=no_backtrace)
            for b in blocks
        ],
    }


def format_tags_json(blocks: list[LogBlock], total_blocks: int,
                     filter_desc: str) -> dict:
    """
    Tag-frequency view: count [TAG] markers across (optionally filtered) blocks.
    No block dump — just the histogram. Smallest possible useful output.
    """
    tag_counts = Counter()
    tag_by_level = defaultdict(Counter)
    for block in blocks:
        for tag in block.tags:
            tag_counts[tag] += 1
            tag_by_level[block.level][tag] += 1
    return {
        "mode": "tags",
        "filter": filter_desc,
        "total_blocks": total_blocks,
        "blocks_scanned": len(blocks),
        "tags": dict(tag_counts.most_common()),
        "tags_by_level": {
            lvl: dict(cnts.most_common(10))
            for lvl, cnts in tag_by_level.items()
        },
    }


def format_tags_human(data: dict) -> str:
    """Human-readable tag frequency report."""
    lines = [
        "=" * 50,
        "TAG FREQUENCY",
        "=" * 50,
        f"Filter: {data['filter']}",
        f"Scanned: {data['blocks_scanned']} of {data['total_blocks']} blocks",
        "",
    ]
    if not data["tags"]:
        lines.append("No tagged entries found.")
        return "\n".join(lines)
    lines.append("## Tags (overall)")
    for tag, count in data["tags"].items():
        lines.append(f"  [{tag}]  {count}")
    return "\n".join(lines)


# --- Summary mode (backward compatible) ----------------------------------

def format_summary(blocks: list[LogBlock], log_path: str, total_lines: int) -> dict:
    """Build summary results from parsed blocks."""
    counts = {"errors": 0, "warnings": 0, "critical": 0, "info": 0, "debug": 0}
    grouped_warnings = Counter()
    grouped_errors = Counter()

    pool_pat = re.compile(r"(ReturnToPool|no pool exists|PooledArchetype)", re.IGNORECASE)
    tree_pat = re.compile(r"!is_inside_tree\(\)", re.IGNORECASE)
    spawn_pat = re.compile(r"\[SpawnEffect\]", re.IGNORECASE)
    multishot_pat = re.compile(r"\[MultiShot\]", re.IGNORECASE)
    spawner_pat = re.compile(r"\[SpellSpawner\]", re.IGNORECASE)

    patterns = {
        "spawn_effect": [], "multishot": [], "spell_spawner": [],
        "pool_issues": [], "tree_errors": [],
    }

    for block in blocks:
        level = block.level.upper()
        if level == "ERROR" or level == "EXCEPTION":
            counts["errors"] += 1
            grouped_errors[normalize_message(block.message)] += 1
        elif level == "WARNING" or level == "HANDLED EXCEPTION":
            counts["warnings"] += 1
            grouped_warnings[normalize_message(block.message)] += 1
        elif level == "INFO":
            counts["info"] += 1
        elif level == "DEBUG":
            counts["debug"] += 1

        msg = block.message
        if pool_pat.search(msg):
            patterns["pool_issues"].append(msg)
        if tree_pat.search(msg):
            patterns["tree_errors"].append(msg)
        if spawn_pat.search(msg):
            patterns["spawn_effect"].append(msg)
        if multishot_pat.search(msg):
            patterns["multishot"].append(msg)
        if spawner_pat.search(msg):
            patterns["spell_spawner"].append(msg)

    pattern_counts = {k: len(v) for k, v in patterns.items()}
    for key in patterns:
        patterns[key] = patterns[key][:5]

    results = {
        "mode": "summary",
        "log_path": log_path,
        "total_lines": total_lines,
        "total_blocks": len(blocks),
        "counts": counts,
        "patterns": patterns,
        "pattern_counts": pattern_counts,
        "grouped_warnings": grouped_warnings.most_common(10),
        "grouped_errors": grouped_errors.most_common(10),
        "recommendations": generate_recommendations(counts, pattern_counts),
    }
    return results


def normalize_message(msg: str) -> str:
    """Normalize a message for grouping (remove variable parts)."""
    msg = re.sub(r"\s*@\s*\S+:\d+\s+in\s+\w+\(\)", "", msg)
    msg = re.sub(r"0x[0-9a-fA-F]+", "<addr>", msg)
    msg = re.sub(r"for \S+\.tres", "for <resource>", msg)
    msg = re.sub(r"'[^']*'", "'<name>'", msg)
    return msg.strip()


def generate_recommendations(counts: dict, pattern_counts: dict) -> list:
    """Generate fix recommendations based on analysis."""
    recs = []
    pool_count = pattern_counts.get("pool_issues", 0)
    if pool_count > 0:
        recs.append({
            "issue": "Pool management issues detected",
            "count": pool_count,
            "likely_cause": "PooledArchetype not set or ReturnToPool called on non-pooled instance",
            "fix": "Set charScene.PooledArchetype = archetype in InstantiateForPool()",
            "severity": "HIGH" if pool_count > 50 else "MEDIUM",
        })

    tree_count = pattern_counts.get("tree_errors", 0)
    if tree_count > 0:
        recs.append({
            "issue": "Node not in tree errors detected",
            "count": tree_count,
            "likely_cause": "GlobalPosition accessed before AddChild() or after tree removal",
            "fix": "Ensure AddChild() is called before setting GlobalPosition",
            "severity": "HIGH" if tree_count > 10 else "MEDIUM",
        })

    if counts.get("warnings", 0) > 100:
        recs.append({
            "issue": "High warning count indicates systematic issue",
            "count": counts["warnings"],
            "likely_cause": "Check most common warning pattern in grouped_warnings",
            "fix": "Address the root cause of the most frequent warning",
            "severity": "HIGH",
        })

    return recs


def format_summary_report(results: dict) -> str:
    """Format summary results as human-readable report."""
    if "error" in results:
        return f"Error: {results['error']}"

    lines = [
        "=" * 50,
        "GODOT LOG ANALYSIS REPORT",
        "=" * 50,
        f"Log file: {results['log_path']}",
        f"Lines analyzed: {results['total_lines']:,}",
        f"Blocks parsed: {results['total_blocks']:,}",
        "",
        "## Summary",
        f"- ERRORS: {results['counts']['errors']}",
        f"- WARNINGS: {results['counts']['warnings']}",
        f"- INFO: {results['counts']['info']}",
        f"- DEBUG: {results['counts']['debug']}",
        "",
    ]

    if results["grouped_warnings"]:
        lines.append("## Top Warnings (grouped)")
        for i, (msg, count) in enumerate(results["grouped_warnings"][:5], 1):
            lines.append(f"{i}. ({count}x) {msg[:100]}{'...' if len(msg) > 100 else ''}")
        lines.append("")

    if results["grouped_errors"]:
        lines.append("## Top Errors (grouped)")
        for i, (msg, count) in enumerate(results["grouped_errors"][:5], 1):
            lines.append(f"{i}. ({count}x) {msg[:100]}{'...' if len(msg) > 100 else ''}")
        lines.append("")

    active_patterns = {k: v for k, v in results["pattern_counts"].items() if v > 0}
    if active_patterns:
        lines.append("## Pattern Statistics")
        for pattern, count in active_patterns.items():
            lines.append(f"- {pattern}: {count} occurrences")
        lines.append("")

    if results["recommendations"]:
        lines.append("## Recommendations")
        for rec in results["recommendations"]:
            lines.append(f"\n### [{rec['severity']}] {rec['issue']}")
            lines.append(f"- Occurrences: {rec['count']}")
            lines.append(f"- Likely Cause: {rec['likely_cause']}")
            lines.append(f"- Fix: {rec['fix']}")

    return "\n".join(lines)


# --- CLI -----------------------------------------------------------------

def _build_filter_desc(args, levels) -> str:
    parts = []
    if args.target:
        parts.append(f"target={' AND '.join(args.target)}")
    if args.target_any:
        parts.append(f"target_any={' OR '.join(args.target_any)}")
    if args.node:
        parts.append(f"node={args.node}")
    if args.type:
        parts.append(f"type={args.type}")
    if levels:
        parts.append(f"level={','.join(levels)}")
    return "; ".join(parts) if parts else "(none)"


def main():
    parser = argparse.ArgumentParser(
        description="Godot Log Analyzer — modal output with token-efficient JSON"
    )
    parser.add_argument("log_path", nargs="?", default=None,
                        help="Path to Godot log file (default: standard location). "
                             "Overridden by --log if both given.")

    # Mode + log selection
    parser.add_argument("--mode",
                        choices=["summary", "errors", "timeline", "tail", "tags", "entity"],
                        default=None,
                        help="Output mode preset (default: summary, or timeline if filters given)")
    parser.add_argument("--log", default=None,
                        help="Log file selector: 'latest' (default), 'previous', "
                             "<int> (Nth most recent, 0=latest), or explicit path")

    # Output format
    parser.add_argument("--json", action="store_true",
                        help="Output structured JSON")
    parser.add_argument("--timeline", action="store_true",
                        help="(Legacy) Force chronological output — equivalent to --mode timeline")
    parser.add_argument("--summary", action="store_true",
                        help="(Legacy) Force summary output — equivalent to --mode summary")

    # Filters
    parser.add_argument("--target", nargs="+", default=None,
                        help="AND-mode search terms (all must match)")
    parser.add_argument("--target-any", nargs="+", default=None,
                        help="OR-mode search terms (any can match)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max entries to show (default: 100 timeline, 20 errors, all summary)")
    parser.add_argument("--last", type=int, default=20,
                        help="For --mode tail: number of trailing blocks (default: 20)")
    parser.add_argument("--level", default=None,
                        help="Comma-separated severity filter (info,debug,warning,error,exception)")
    parser.add_argument("--node", default=None,
                        help="Filter by node/owner path substring")
    parser.add_argument("--type", default=None,
                        help="Filter by class name")

    # Token-efficiency flags (apply to JSON output)
    parser.add_argument("--include-raw", action="store_true",
                        help="Include raw_lines in JSON (default OFF — ~50%% size reduction)")
    parser.add_argument("--max-message", type=int, default=300,
                        help="Truncate message field at N chars (default: 300; 0 = no truncation)")
    parser.add_argument("--no-backtrace", action="store_true",
                        help="Drop backtrace field from JSON output entirely")
    parser.add_argument("--fields", default=None,
                        help="Comma-separated whitelist of block fields to include in JSON")

    args = parser.parse_args()

    # --- Resolve log path ------------------------------------------------
    # Precedence: positional log_path > --log > default
    if args.log_path:
        log_path = Path(args.log_path)
    else:
        log_path = resolve_log_path(args.log)

    if not log_path.exists():
        result = {"error": f"Log file not found: {log_path}"}
        print(json.dumps(result, indent=2) if args.json else f"Error: {result['error']}")
        return

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_lines = f.readlines()

    blocks = parse_log(raw_lines)
    total_blocks = len(blocks)

    # Parse level filter
    levels = None
    if args.level:
        levels = [l.strip().lower() for l in args.level.split(",")]

    # Parse fields whitelist
    fields = None
    if args.fields:
        fields = [f.strip() for f in args.fields.split(",")]

    # --- Resolve effective mode ------------------------------------------
    # Mode resolution precedence:
    #   --mode explicit > legacy --summary/--timeline > inferred from filters
    if args.mode:
        mode = args.mode
    elif args.summary:
        mode = "summary"
    elif args.timeline:
        mode = "timeline"
    elif args.target or args.target_any or args.node or args.type or levels:
        mode = "timeline"  # Filters present without explicit mode → timeline
    else:
        mode = "summary"

    # --- Mode dispatch ---------------------------------------------------
    max_msg = args.max_message if args.max_message > 0 else None
    filter_desc = _build_filter_desc(args, levels)

    if mode == "summary":
        # Apply filters first if any (targeted summary on subset)
        target_blocks = blocks
        if args.target or args.target_any or args.node or args.type or levels:
            target_blocks = filter_blocks(blocks, args.target, args.target_any,
                                           levels, args.node, args.type)
        results = format_summary(target_blocks, str(log_path), len(raw_lines))
        if filter_desc != "(none)":
            results["filter"] = filter_desc
            results["filtered_blocks"] = len(target_blocks)
        print(json.dumps(results, indent=2) if args.json
              else format_summary_report(results))
        return

    if mode == "errors":
        # Errors mode: error+exception level, filtered + minimal fields
        # Force level to error,exception (overrides --level if user gave something else)
        err_levels = ["error", "exception"]
        filtered = filter_blocks(blocks, args.target, args.target_any,
                                  err_levels, args.node, args.type)
        limit = args.limit or 20
        filtered = filtered[:limit]
        if args.json:
            print(json.dumps(format_timeline_json(
                filtered, total_blocks, filter_desc, mode="errors",
                fields=fields, include_raw=args.include_raw,
                max_message=max_msg, no_backtrace=args.no_backtrace,
            ), indent=2))
        else:
            print(format_timeline(filtered, total_blocks,
                                   filter_desc + "; level=error,exception", limit))
        return

    if mode == "tail":
        # Tail mode: last N blocks (filtered if any filters provided)
        target_blocks = blocks
        if args.target or args.target_any or args.node or args.type or levels:
            target_blocks = filter_blocks(blocks, args.target, args.target_any,
                                           levels, args.node, args.type)
        n = args.last
        tail_blocks = target_blocks[-n:] if len(target_blocks) > n else target_blocks
        desc = filter_desc + f"; tail={n}" if filter_desc != "(none)" else f"tail={n}"
        if args.json:
            print(json.dumps(format_timeline_json(
                tail_blocks, total_blocks, desc, mode="tail",
                fields=fields, include_raw=args.include_raw,
                max_message=max_msg, no_backtrace=args.no_backtrace,
            ), indent=2))
        else:
            print(format_timeline(tail_blocks, total_blocks, desc, n))
        return

    if mode == "tags":
        # Tag frequency view
        target_blocks = blocks
        if args.target or args.target_any or args.node or args.type or levels:
            target_blocks = filter_blocks(blocks, args.target, args.target_any,
                                           levels, args.node, args.type)
        data = format_tags_json(target_blocks, total_blocks, filter_desc)
        print(json.dumps(data, indent=2) if args.json else format_tags_human(data))
        return

    if mode == "entity":
        # Entity mode: requires --node; groups events by class for one entity
        if not args.node:
            err = {"error": "--mode entity requires --node <path-substring>"}
            print(json.dumps(err) if args.json else f"Error: {err['error']}")
            return
        filtered = filter_blocks(blocks, args.target, args.target_any,
                                  levels, args.node, args.type)
        limit = args.limit or 100
        filtered = filtered[:limit]
        if args.json:
            print(json.dumps(format_timeline_json(
                filtered, total_blocks, filter_desc, mode="entity",
                fields=fields, include_raw=args.include_raw,
                max_message=max_msg, no_backtrace=args.no_backtrace,
            ), indent=2))
        else:
            print(format_timeline(filtered, total_blocks, filter_desc, limit))
        return

    # mode == "timeline" (default for filtered queries)
    filtered = filter_blocks(blocks, args.target, args.target_any,
                              levels, args.node, args.type)
    limit = args.limit or 100
    if args.json:
        print(json.dumps(format_timeline_json(
            filtered, total_blocks, filter_desc, mode="timeline",
            fields=fields, include_raw=args.include_raw,
            max_message=max_msg, no_backtrace=args.no_backtrace,
        ), indent=2))
    else:
        print(format_timeline(filtered, total_blocks, filter_desc, limit))


if __name__ == "__main__":
    main()
