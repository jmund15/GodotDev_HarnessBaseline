#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreCompact - Backup conversation transcript before compaction

Purpose:
- Saves conversation transcript before Claude compacts context
- Preserves potentially valuable context that would otherwise be lost
- Generates concise summary (~10-20KB) optimized for autolearn and post-compaction resume
- Backups stored in logs/transcript_backups/ with timestamps

Note: This hook cannot block compaction, only log/backup before it happens.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Import summary builder (local module with underscore prefix)
try:
    from _transcript_summary import TranscriptSummaryBuilder, write_summary
    SUMMARY_AVAILABLE = True
except ImportError:
    SUMMARY_AVAILABLE = False


# Raw transcript backups are only needed for recent-session forensics; the
# .summary.json files are the durable artifact (/autolearn, session_audit)
# and are kept indefinitely (small).
RAW_BACKUP_RETENTION_DAYS = 14


def _prune_old_raw_backups(backup_dir: Path) -> None:
    """Delete raw transcript_*.jsonl backups older than the retention window.
    Summaries are never pruned. Best-effort — failure never blocks the backup."""
    try:
        cutoff = datetime.now().timestamp() - RAW_BACKUP_RETENTION_DAYS * 86400
        for p in backup_dir.glob("transcript_*.jsonl"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                continue
    except Exception:
        pass


def backup_transcript_with_summary(
    transcript_path: str, trigger: str, session_id: str
) -> tuple[str | None, str | None]:
    """
    Create a timestamped backup of the transcript file with summary generation.
    Uses streaming processing to minimize overhead.

    Returns (backup_path, summary_path) - either can be None on failure.
    """
    backup_path = None
    summary_path = None

    try:
        source = Path(transcript_path)
        if not source.exists():
            return None, None

        # Create backup directory
        backup_dir = Path.cwd() / "logs" / "transcript_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Retention sweep BEFORE adding the new backup (dir reached 8.4 GB
        # before retention existed — raw transcripts are multi-MB each).
        _prune_old_raw_backups(backup_dir)

        # Create timestamped backup filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_short = session_id[:8] if session_id else "unknown"
        backup_name = f"transcript_{session_short}_{trigger}_{timestamp}.jsonl"
        backup_path = backup_dir / backup_name
        summary_name = f"transcript_{session_short}_{trigger}_{timestamp}.summary.json"
        summary_file = backup_dir / summary_name

        # Streaming copy with summary generation
        if SUMMARY_AVAILABLE:
            builder = TranscriptSummaryBuilder(session_id, backup_name)

            with open(source, 'r', encoding='utf-8') as src:
                with open(backup_path, 'w', encoding='utf-8') as dst:
                    for line in src:
                        dst.write(line)
                        builder.process_line(line)  # Safe - catches exceptions

            # Generate and write summary
            try:
                summary = builder.finalize()
                if write_summary(str(summary_file), summary):
                    summary_path = str(summary_file)
            except Exception:
                pass  # Summary generation failed, but backup succeeded

        else:
            # Fallback: simple copy without summary
            with open(source, 'r', encoding='utf-8') as src:
                with open(backup_path, 'w', encoding='utf-8') as dst:
                    dst.write(src.read())

        return str(backup_path), summary_path

    except Exception:
        # If we got the backup path but failed later, still return it
        if backup_path and backup_path.exists():
            return str(backup_path), None
        return None, None


def backup_transcript(transcript_path: str, trigger: str, session_id: str) -> str | None:
    """
    Legacy wrapper for backward compatibility.
    Returns only the backup path.
    """
    backup_path, _ = backup_transcript_with_summary(transcript_path, trigger, session_id)
    return backup_path


def log_compaction_event(input_data: dict, backup_path: str | None, summary_path: str | None = None):
    """Log the compaction event to a JSON file."""
    try:
        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "pre_compact.json"

        # Load existing logs
        existing = []
        if log_file.exists():
            try:
                existing = json.loads(log_file.read_text())
            except json.JSONDecodeError:
                existing = []

        # Add new entry
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": input_data.get("session_id", "unknown"),
            "trigger": input_data.get("trigger", "unknown"),
            "backup_path": backup_path,
            "summary_path": summary_path,
            "had_transcript": input_data.get("transcript_path") is not None
        }
        existing.append(entry)

        # Write back, capped to the most recent entries (full-array rewrite
        # grew unbounded — 293 KB before the cap existed).
        log_file.write_text(json.dumps(existing[-200:], indent=2))

    except Exception:
        pass  # Don't fail the hook on logging errors


def update_session_manifest(session_id: str, summary_path: str | None):
    """
    Update the per-session file manifest with the latest files_modified data.

    Since compaction summaries are cumulative (each is a superset of all prior ones),
    we just need the latest summary's files_modified list. This eliminates the need to
    read N individual .summary.json files during session_audit / commit_push.
    """
    if not session_id or session_id == "unknown" or not summary_path:
        return

    try:
        # Read the summary to extract files_modified
        summary_file = Path(summary_path)
        if not summary_file.exists():
            return

        summary = json.loads(summary_file.read_text(encoding="utf-8"))

        manifest_dir = Path.cwd() / "logs" / "session_files"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{session_id}.json"

        # Track compaction count across updates
        compaction_count = 1
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                compaction_count = existing.get("compaction_count", 0) + 1
            except (json.JSONDecodeError, Exception):
                pass

        manifest = {
            "session_id": session_id,
            "last_updated": datetime.now().isoformat(),
            "compaction_count": compaction_count,
            "files": summary.get("files_modified", []),
            "files_counts": summary.get("files_modified_counts", {}),
        }

        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8"
        )
    except Exception:
        pass  # Don't fail the hook on manifest errors


def main():
    # Read hook input from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Hook workaround #10463: Always print before exit to avoid false errors
        print("{}")
        sys.exit(0)

    session_id = input_data.get("session_id", "")
    transcript_path = input_data.get("transcript_path")
    trigger = input_data.get("trigger", "unknown")  # "manual" or "auto"

    # Attempt backup with summary generation if transcript path provided
    backup_path = None
    summary_path = None
    if transcript_path:
        backup_path, summary_path = backup_transcript_with_summary(
            transcript_path, trigger, session_id
        )

    # Log the compaction event
    log_compaction_event(input_data, backup_path, summary_path)

    # Update session manifest (single-read file for session_audit / commit_push)
    update_session_manifest(session_id, summary_path)

    # Output status and post-continuation reminder
    output_lines = ["<pre-compact-hook>"]

    if backup_path:
        output_lines.append(f"Transcript backed up to: {backup_path}")
        if summary_path:
            output_lines.append(f"Summary generated: {summary_path}")
        else:
            output_lines.append("(Summary generation skipped or failed)")
    else:
        output_lines.append("Compaction triggered (no transcript to backup)")

    # Add reminder for post-continuation context loading
    output_lines.append("")
    output_lines.append("POST-CONTINUATION REMINDER: After resuming from this compaction,")
    output_lines.append("immediately search auto-memory (semantic-search, restrictToDir=.claude/auto-memory) for task-relevant gotchas before proceeding.")
    output_lines.append("</pre-compact-hook>")

    print("\n".join(output_lines))

    # Hook workaround #10463: print before exit
    sys.exit(0)


if __name__ == "__main__":
    main()
