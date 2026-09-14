#!/usr/bin/env python3
"""Check session-close evidence, not mentions of phase instructions.

Receipt completion is author-recorded and freshness-checked, not a semantic audit.
Read/Skill/tool-success observations alone never prove that a full phase finished.
Default check is precommit; --stage final additionally checks commit and reindex.
--resume reports gaps without failing. --record stores one session-scoped receipt.
"""
import argparse
import glob
import hashlib
import importlib.util
import json
import os
import re
import sys

# (phase, command stem, mandatory) — mirrors commands/session_end.md, which owns the phase list.
PHASES = [
    ("0   Session digest",        "session_digest",        True),
    ("1   Session audit",         "session_audit",         True),
    ("2   Autolearn",             "autolearn",             True),
    ("3   Self-evaluate",         "self_evaluate",         True),
    ("3.5 Routing audit",         "routing_audit",         True),
    # Conditional: no Workflow or attributable sidecar work means a documented skip.
    ("3.6 Orchestration metrics", "orchestration_metrics", False),
    ("4   Sync subsystems",       "sync_subsystems",       False),
    ("5   Regression gate",       "regression_gate",       False),
    ("5.4 Roadmap atlas",         "roadmap_atlas",         True),
    ("5.5 Roadmap drift",         "update_roadmap",        False),
    ("6   Worklog sweep",         "worklog",               True),
    ("7   Commit",                "commit_push",           True),
    ("8   Reindex search",        "reindex_search",        False),
]

# Anchored to the project like `--repo`: a cwd-relative path from any other cwd reads as
# "artifact missing", a false red the exit code cannot distinguish from a real gap.
_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ARCHIVE = os.path.join(_PROJECT_DIR, ".claude", "self_evaluate_archive.json")

# Anchored at a path boundary so a fragment embedded in prose or a doc path cannot match, and
# stems_seen() skips any value that names a nested worktree — a peer checkout under
# `.claude/worktrees/` opening `commands/worklog.md` is not THIS session's phase.
_STEM = re.compile(
    r"(?<![A-Za-z0-9_.-])\.claude[\\/](?:commands|tools|scripts)[\\/](?:[A-Za-z0-9_-]+[\\/])*"
    r"([A-Za-z0-9_-]+)\.(?:md|py)(?![A-Za-z0-9_])")
_WORKTREE = re.compile(r"\.claude[\\/]worktrees[\\/]")


def project_key(repo):
    """Claude Code's per-project transcript directory name for this repo."""
    # Underscores are dashed too: `...\Game_Dev\Godot_Projects\...` -> `-Game-Dev-Godot-Projects-`.
    p = os.path.abspath(repo)
    for ch in (":", "\\", "/", "_"):
        p = p.replace(ch, "-")
    return p.rstrip("-")


def transcript_for(session, repo):
    """Require this project's exact or unique-prefix session identity."""
    if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session):
        return None
    base = os.path.join(os.path.expanduser('~'), '.claude', 'projects', project_key(repo))
    exact = os.path.join(base, session + '.jsonl')
    if os.path.isfile(exact):
        return exact
    candidates = glob.glob(os.path.join(base, session + '*.jsonl'))
    return candidates[0] if len(candidates) == 1 else None


def phase_observations(path):
    """Stream actual assistant calls and joined results; keep observation types distinct."""
    observations = {stem: dict(accessed=False, invoked=False, tool_succeeded=False, completed=False)
                    for _, stem, _ in PHASES}
    pending = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get('isSidechain'):
                continue
            message = rec.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if rec.get('type') == 'user' and block.get('type') == 'tool_result':
                    tool_id = block.get('tool_use_id')
                    stems = pending.pop(tool_id, ()) if isinstance(tool_id, str) else ()
                    if block.get('is_error', False) is False:
                        for stem in stems:
                            observations[stem]['tool_succeeded'] = True
                    continue
                if rec.get('type') != 'assistant' or block.get('type') != 'tool_use':
                    continue
                ti = block.get('input')
                if not isinstance(ti, dict):
                    continue
                stems = set()
                if str(block.get('name', '')).split('.')[-1] == 'Skill':
                    stem = ti.get('skill')
                    if isinstance(stem, str) and stem in observations:
                        observations[stem]['invoked'] = True
                        stems.add(stem)
                for key in ('file_path', 'command', 'pattern', 'path'):
                    value = ti.get(key)
                    if not isinstance(value, str) or _WORKTREE.search(value):
                        continue
                    for match in _STEM.finditer(value):
                        stem = match.group(1)
                        if stem in observations:
                            observations[stem]['accessed'] = True
                            stems.add(stem)
                if isinstance(block.get('id'), str):
                    pending.setdefault(block['id'], stems)
    return observations


def stems_seen(path):
    """Compatibility view: actual assistant accesses, never a completion signal."""
    return {stem for stem, observed in phase_observations(path).items() if observed['accessed']}


def session_id_of(path):
    """Claude Code names transcripts `<session-id>.jsonl`."""
    return os.path.splitext(os.path.basename(path))[0]


def phase3_artifact_ok(archive_path, sid):
    """Phase 3 (`self_evaluate`) artifact check: a `structured_entries` row for THIS session."""
    try:
        with open(archive_path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return False
    entries = doc.get("structured_entries") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return False
    return any(isinstance(e, dict) and e.get("session_id") == sid for e in entries)


def _file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _receipt_hash(receipt):
    return hashlib.sha256(json.dumps(receipt, sort_keys=True).encode('utf-8')).hexdigest()


def make_receipt(sid, phase, status, input_paths, evidence_paths, reason, dependencies=None, available=None):
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
        raise ValueError('receipt requires exact session identity')
    if not isinstance(phase, str) or phase not in {stem for _, stem, _ in PHASES} or status not in ('completed', 'skipped', 'blocked'):
        raise ValueError('unknown phase or receipt status')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('receipt needs a reason naming the result or skip predicate')
    if status in ('completed', 'skipped') and not input_paths:
        raise ValueError('terminal receipt needs the checked input scope')
    if status == 'completed' and not evidence_paths:
        raise ValueError('completed receipt needs result evidence')
    snapshots = {}
    for key, paths in (('inputs', input_paths), ('evidence', evidence_paths)):
        snapshots[key] = {os.path.abspath(path): _file_hash(path) for path in paths}
    dependencies = dependencies or {}
    for stem, receipt in dependencies.items():
        if receipt.get('phase') != stem or receipt_status(receipt, sid, available or dependencies) not in ('completed', 'skipped'):
            raise ValueError('dependency is not current: ' + stem)
    return dict(version=1, session_id=sid, phase=phase, status=status, reason=reason.strip(),
                basis='author-recorded; file hashes verify freshness, not semantic quality',
                dependencies={stem: _receipt_hash(receipt) for stem, receipt in dependencies.items()},
                **snapshots)


def receipt_status(receipt, sid, receipts=None, visiting=None):
    if not isinstance(receipt, dict) or type(receipt.get('version')) is not int or receipt.get('version') != 1 or receipt.get('session_id') != sid:
        return 'unknown'
    phase, status = receipt.get('phase'), receipt.get('status')
    if not isinstance(phase, str) or phase not in {stem for _, stem, _ in PHASES} or status not in ('completed', 'skipped', 'blocked'):
        return 'unknown'
    if not isinstance(receipt.get('reason'), str) or not receipt['reason'].strip():
        return 'unknown'
    for key in ('inputs', 'evidence'):
        snapshots = receipt.get(key)
        if not isinstance(snapshots, dict):
            return 'unknown'
        if not snapshots and (status == 'completed' or (status == 'skipped' and key == 'inputs')):
            return 'unknown'
        for path, expected in snapshots.items():
            try:
                if not os.path.isabs(path) or _file_hash(path) != expected:
                    return 'stale'
            except (OSError, ValueError, TypeError):
                return 'stale'
    deps = receipt.get('dependencies')
    if not isinstance(deps, dict):
        return 'unknown'
    visiting = set(visiting or ())
    if phase in visiting:
        return 'unknown'
    visiting.add(phase)
    for stem, expected in deps.items():
        parent = (receipts or {}).get(stem)
        if not isinstance(parent, dict) or parent.get('phase') != stem or _receipt_hash(parent) != expected:
            return 'stale'
        if receipt_status(parent, sid, receipts, visiting) not in ('completed', 'skipped'):
            return 'stale'
    return status


def required_phases(stage='precommit'):
    return [stem for _, stem, _ in PHASES if stage == 'final' or stem not in ('commit_push', 'reindex_search')]


def load_receipts(directory, sid):
    receipts = {}
    for _, stem, _ in PHASES:
        try:
            with open(os.path.join(directory, stem + '.json'), encoding='utf-8') as fh:
                receipt = json.load(fh)
            if isinstance(receipt, dict) and receipt.get('phase') == stem and receipt.get('session_id') == sid:
                receipts[stem] = receipt
        except (OSError, ValueError):
            pass
    return receipts


def render(path, sid, archive_path, artifacts, receipts=None, stage='precommit'):
    """Print observation and receipt status separately; every phase needs a disposition."""
    observations, receipts = phase_observations(path), receipts or {}
    print('session_end evidence — ' + sid)
    print('transcript: ' + os.path.basename(path))
    print('Completion is author-recorded; only declared inputs, evidence and dependencies are freshness-checked.')
    missing, resume_label = [], None
    for label, stem, mandatory in PHASES:
        if stem not in required_phases(stage):
            continue
        status = receipt_status(receipts.get(stem), sid, receipts)
        if status == 'skipped' and mandatory:
            status = 'invalid-skip'
        if artifacts and stem == 'self_evaluate' and status == 'completed' and not phase3_artifact_ok(archive_path, sid):
            status = 'artifact-missing'
        seen = observations[stem]
        print(f'  [{status}] {label} ({stem}) — accessed={seen["accessed"]}, invoked={seen["invoked"]}')
        if status not in ('completed', 'skipped'):
            missing.append(label.strip())
            resume_label = resume_label or label
    return missing, resume_label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID") or None,
                    help="exact session id or unique prefix (default: active session; no newest fallback)")
    ap.add_argument("--repo", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--transcript", help="path to a transcript JSONL (proofs only; skips discovery)")
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE, help="self-evaluate archive path")
    ap.add_argument("--resume", action="store_true",
                     help="print the table + RESUME AT line; always exits 0")
    ap.add_argument("--artifacts", action="store_true",
                     help="also check Phase 3's archive artifact; exit 1 on any gap")
    ap.add_argument('--stage', choices=('precommit', 'final'), default='precommit')
    ap.add_argument('--receipts', help='session receipt directory')
    ap.add_argument('--record', choices=[stem for _, stem, _ in PHASES])
    ap.add_argument('--status', choices=('completed', 'skipped', 'blocked'))
    ap.add_argument('--input', action='append', default=[])
    ap.add_argument('--evidence', action='append', default=[])
    ap.add_argument('--depends', action='append', default=[])
    ap.add_argument('--reason', default='')
    a = ap.parse_args()

    path = a.transcript or transcript_for(a.session, a.repo)
    if not path:
        print("No transcript found — phase invocation is UNKNOWN, never 'all ran'.")
        return 2

    sid = session_id_of(path)
    if a.transcript and ((a.session is not None and a.session != sid) or (a.record and not a.session)):
        print('REFUSED: --transcript requires matching exact session identity when supplied or recording.')
        return 2
    directory = a.receipts or os.path.join(a.repo, '.claude', 'scratch', 'session_end', sid)
    receipts = load_receipts(directory, sid)
    if a.record:
        try:
            dependencies = {stem: receipts[stem] for stem in a.depends}
            receipt = make_receipt(sid, a.record, a.status, a.input, a.evidence, a.reason, dependencies, receipts)
            target = os.path.join(directory, a.record + '.json')
            if os.path.exists(target) and a.record not in receipts:
                raise ValueError('existing receipt has unknown or foreign ownership')
            spec = importlib.util.spec_from_file_location('_close_state',
                os.path.join(_PROJECT_DIR, '.claude', 'hooks', '_hook_state.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not module.write_json_atomic(target, receipt):
                raise ValueError('receipt write failed')
        except (OSError, ValueError, KeyError) as error:
            print('REFUSED receipt: ' + str(error))
            return 2
        print('Recorded ' + a.record + ': ' + a.status)
        return 0
    missing, resume_label = render(path, sid, a.archive, a.resume or a.artifacts, receipts, a.stage)

    print()
    if a.resume:
        if resume_label is None:
            print("RESUME AT: none — all checked phase receipts are current; re-run any phase "
                  "whose source scope or result evidence changes")
        else:
            print(f"RESUME AT: {resume_label.split()[0]}")
        return 0

    if missing:
        print(f"{len(missing)} phase(s) lack current completion or a valid conditional skip:")
        for m in missing:
            print(f"  - {m}")
        print("Finish the phase and record evidence, or record its specific blocker. A mention is not completion.")
        return 1
    print("All checked phases have current receipts; verify their stated scope before claiming full closeout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
