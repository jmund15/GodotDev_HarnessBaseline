"""SessionStart, cloud only (class: process; it never denies): refresh the vault mirror and print its line.

A cloud VM has no Obsidian vault. `tools/vault_sync.py mirror` fetches origin/vault and extracts it
under the home directory, where the harness vault readers look. In cloud sessions it also injects
rules/vault_mirror.md without its path-scoped frontmatter when the file is present. This hook prints that tool's one
line as `[vault] <line>`, or `[vault] vault mirror UNAVAILABLE: <reason>` when the tool is missing,
silent or past its time. Off the cloud it prints a `[vault] sync REFUSED: <reason>` or
`[vault] sync STALE: <age>` line when this machine's `sync_status.json` says the last sync failed or is
older than two sync intervals, and nothing when the sync is healthy or never ran here. It always exits 0:
a lost mirror must not fail the session, and the line tells the agent to treat design docs as unavailable.

The folders and names come from tools/vault_sync.project.json. With that file absent the hook prints
nothing, on the cloud or off it: a project that never adopted the vault gets no line and no
`<cloud-session>` block. With it invalid, the cloud prints `[vault] vault mirror UNAVAILABLE: <reason>` and
a workstation prints `[vault] sync NOT-CONFIGURED: <reason>`.
"""
import calendar
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bounded_run import run_bounded
from _claude_scope import is_cloud

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import vault_sync
from vault_sync import SYNC_INTERVAL_MIN, default_git_dir

TOOL_TIMEOUT_S = 100


def mirror_line(root: Path, timeout: int = TOOL_TIMEOUT_S) -> str:
    tool = root / '.claude' / 'tools' / 'vault_sync.py'
    try:
        result = run_bounded([sys.executable, str(tool), 'mirror'], timeout, cwd=root)
    except OSError as exc:
        return f'vault mirror UNAVAILABLE: {type(exc).__name__}'
    if result.returncode is None:
        return f'vault mirror UNAVAILABLE: no answer in {timeout} s'
    return result.stdout.strip() or f'vault mirror UNAVAILABLE: tool printed nothing (exit {result.returncode})'


def sync_status_line(git_dir: Path, now: float | None = None) -> str:
    """The line for a failed or stale local sync, '' when healthy, absent or unreadable."""
    try:
        row = json.loads((git_dir / 'sync_status.json').read_text(encoding='utf-8'))
        at = calendar.timegm(time.strptime(row['at'], '%Y-%m-%dT%H:%M:%SZ'))
    except (OSError, ValueError, KeyError, TypeError):
        return ''
    if not row.get('ok'):
        reasons = [f"{r.get('kind')} {r.get('path')}" for r in row.get('refusals') or [] if isinstance(r, dict)]
        return f"[vault] sync REFUSED: {'; '.join(reasons) or row.get('error') or 'no reason recorded'}"
    age_min = ((time.time() if now is None else now) - at) / 60
    if age_min > 2 * SYNC_INTERVAL_MIN:
        return f'[vault] sync STALE: last sync {int(age_min)} min ago'
    return ''


def main() -> int:
    if vault_sync.CONFIG_STATE == 'absent':
        return 0
    if vault_sync.CONFIG_STATE == 'invalid':
        reason = vault_sync.CONFIG_REASON
        print(f'[vault] vault mirror UNAVAILABLE: {reason}' if is_cloud() else f'[vault] sync NOT-CONFIGURED: {reason}')
        return 0
    if not is_cloud():
        line = sync_status_line(default_git_dir())
        if line:
            print(line)
        return 0
    root = Path(os.environ.get('CLAUDE_PROJECT_DIR') or Path(__file__).resolve().parents[2])
    print(f'[vault] {mirror_line(root)}')
    try:
        text = (root / '.claude/rules/vault_mirror.md').read_text(encoding='utf-8')
        parts = text.split('---', 2) if text.startswith('---') else []
        if len(parts) == 3:
            text = parts[2]
        print(f'<cloud-session>\n{text.strip()}\n</cloud-session>')
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
