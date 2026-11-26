#!/usr/bin/env python3
"""Check session-close evidence, not mentions of phase instructions.

Receipt completion is author-recorded and freshness-checked, not a semantic audit.
Read/Skill/tool-success observations alone never prove that a full phase finished.
Default check is precommit; --stage final additionally checks commit and reindex, and prints one line per
shared-checkout artifact the session changed (index, lock rows, model registry, composed settings) with its check's
result (`shared_artifact_lines`), then the open worklog items this session added (`worklog_session_lines`); the lines report and leave the exit code to the phases.
--resume reports gaps without failing. --record stores one session-scoped receipt.
A gated phase (`GATED`) skips only on `--predicate <file>`: the predicate object `close_predicates.py`
wrote for that stem, stored in the receipt. A false predicate is the skip; a true or null predicate ran
the phase, so its skip needs an in-body `--reason`. Every check, `--resume` included, re-evaluates each
false-predicate skip (`current_predicates`): a predicate now true or null, or one that cannot be
evaluated, makes the skip `stale-skip`, because the stored predicate file does not change when the
facts behind it do.
A completed commit_push needs the branch pushed (`[unpushed]` otherwise). A skipped commit_push stands only
while the unpushed range holds a commit whose Claude-Session trailer is not this session's
(`unpublished_peer_sessions`): Phase 7a's deferred push. Any other skip of a mandatory phase is invalid.
A dependency binds to the parent's conclusion (`_conclusion_hash`): phase, status, reason, the refreshes
that changed more than inputs, and the evidence snapshot. A parent refreshed for an input-only change
leaves its dependents current; changed evidence stales them. A dependency hash stored as the old
whole-receipt hash still reads current. `--resume` names each stale receipt's delta
(`changed:` inputs, evidence, dependencies; `missing:` files that no longer exist).
--refresh <stem> --reason "<what the delta changed>" re-snapshots that receipt's declared inputs and
evidence (extra --input/--evidence paths join the set), re-binds its dependencies, keeps its phase,
status, original reason and dependency stems, and appends {reason, at, changed} to `refreshes`. It
refuses with no receipt, a blocked receipt, a dependency that is not current, or no change.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import datetime

# (phase, command stem, mandatory) — mirrors commands/session_end.md, which owns the phase list.
PHASES = [
    ("0   Session digest",        "session_digest",        True),
    ("1   Session audit",         "session_audit",         True),
    ("2   Autolearn",             "autolearn",             True),
    ("3.6 Orchestration metrics", "orchestration_metrics", False),
    ("3.7 Hot-set review",        "hot_set_review",        False),
    ("4   Sync subsystems",       "sync_subsystems",       False),
    ("5   Regression gate",       "regression_gate",       True),
    ("5.4 Roadmap atlas",         "roadmap_atlas",         False),
    ("5.5 Roadmap drift",         "update_roadmap",        False),
    ("5.6 Harness prune",         "harness_prune",         True),
    ("6   Worklog sweep",         "worklog",               True),
    ("7   Commit",                "commit_push",           True),
    ("8   Reindex search",        "reindex_search",        False),
]
# A mandatory phase that runs a project command binds only where the project ships that command, as
# Phase 5.4 binds only with an atlas generator: without it the phase records blocked and the close goes on.
PROJECT_COMMAND_PHASES = frozenset({"harness_prune"})

# Anchored to the project like `--repo`: a cwd-relative path from any other cwd reads as
# "artifact missing", a false red the exit code cannot distinguish from a real gap.
_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
_HOOKS_DIR = os.path.join(os.path.dirname(_TOOLS_DIR), 'hooks')
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
from _checkout_root import harness_root  # noqa: E402
from _git_run import run as git_run  # noqa: E402
import close_predicates  # noqa: E402
import worklog  # noqa: E402

# Stems whose skip is decided by a `close_predicates.py` predicate; a skip with no stored predicate is invalid.
GATED = frozenset(close_predicates.PREDICATES)

# Anchored at a path boundary so a fragment embedded in prose or a doc path cannot match, and
# phase_observations() skips any value that names a nested checkout — a peer checkout under
# `.claude/worktrees/` or `.claude/.cache/<name>-worktrees/` (e.g. baseline-worktrees)
# opening `commands/worklog.md` is not THIS session's phase.
_STEM = re.compile(
    r"(?<![A-Za-z0-9_.-])\.claude[\\/](?:commands|tools|scripts)[\\/](?:[A-Za-z0-9_-]+[\\/])*"
    r"([A-Za-z0-9_-]+)\.(?:md|py)(?![A-Za-z0-9_])")
_WORKTREE = re.compile(r"\.claude[\\/](?:worktrees|\.cache[\\/][\w.-]*worktrees)[\\/]")


def transcript_for(session, repo):
    """Require this project's exact or unique-prefix session identity, in the store of any checkout of
    `repo`: the session's own is the directory it was launched in, which `repo` may not be."""
    if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session):
        return None
    import session_digest
    hits = session_digest.store_transcripts(session_digest.project_dirs(os.path.abspath(repo)), session, prefix=True)
    return str(hits[0]) if len(hits) == 1 else None


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


def _file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _harness_prune_evidence_ok(receipt):
    for path in (receipt.get('evidence') or {}):
        try:
            with open(path, encoding='utf-8') as stream:
                text = stream.read()
        except OSError:
            continue
        sections = [re.search(r'^### ' + name + r'\s*$([\s\S]*?)(?=^### |\Z)', text, re.MULTILINE)
                    for name in ('Plans', 'Worktrees', 'Scratch')]
        if all(section and section.group(1).strip() for section in sections):
            return True
    return False


def _receipt_hash(receipt):
    return hashlib.sha256(json.dumps(receipt, sort_keys=True).encode('utf-8')).hexdigest()


def _conclusion_hash(receipt):
    """What a dependent binds to: the parent's conclusion, never its input snapshot."""
    inputs = receipt.get('inputs') if isinstance(receipt.get('inputs'), dict) else {}
    refreshes = receipt.get('refreshes') if isinstance(receipt.get('refreshes'), list) else []
    moved = [entry for entry in refreshes if isinstance(entry, dict)
             and any(item not in inputs for item in entry.get('changed') or ())]
    conclusion = {key: receipt.get(key) for key in ('phase', 'status', 'reason', 'evidence')}
    conclusion['refreshes'] = moved
    return hashlib.sha256(json.dumps(conclusion, sort_keys=True).encode('utf-8')).hexdigest()


def _binds(expected, parent):
    """A stored dependency hash matches the parent's conclusion or, in the old format, its whole receipt."""
    return expected in (_conclusion_hash(parent), _receipt_hash(parent))


def _predicate_problem(phase, status, reason, predicate):
    """Why a receipt's predicate does not fit its phase and skip, or None."""
    if phase not in GATED:
        return '--predicate is for gated phases' if predicate is not None else None
    if predicate is None:
        return 'a gated phase skip needs --predicate' if status == 'skipped' else None
    if not isinstance(predicate, dict) or 'value' not in predicate or predicate['value'] not in (True, False, None):
        return 'predicate is not a close_predicates.py object'
    if status == 'skipped' and predicate['value'] is not False:
        text = str(predicate.get('predicate', '')).strip()
        if not str(reason).strip() or str(reason).strip() == text:
            return 'a skip after a true or null predicate needs an in-body reason, not the predicate text'
    return None


def make_receipt(sid, phase, status, input_paths, evidence_paths, reason, dependencies=None, available=None,
                 predicate=None):
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid):
        raise ValueError('receipt requires exact session identity')
    if not isinstance(phase, str) or phase not in {stem for _, stem, _ in PHASES} or status not in ('completed', 'skipped', 'blocked'):
        raise ValueError('unknown phase or receipt status')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('receipt needs a reason naming the result or skip predicate')
    problem = _predicate_problem(phase, status, reason, predicate)
    if problem:
        raise ValueError(problem)
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
    receipt = dict(version=1, session_id=sid, phase=phase, status=status, reason=reason.strip(),
                   basis='author-recorded; file hashes verify freshness, not semantic quality',
                   dependencies={stem: _conclusion_hash(receipt) for stem, receipt in dependencies.items()},
                   **snapshots)
    if predicate is not None:
        receipt['predicate'] = predicate
    return receipt


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
    if phase == 'harness_prune' and status == 'completed' and not _harness_prune_evidence_ok(receipt):
        return 'unknown'
    deps = receipt.get('dependencies')
    if not isinstance(deps, dict):
        return 'unknown'
    visiting = set(visiting or ())
    if phase in visiting:
        return 'unknown'
    visiting.add(phase)
    for stem, expected in deps.items():
        parent = (receipts or {}).get(stem)
        if not isinstance(parent, dict) or parent.get('phase') != stem or not _binds(expected, parent):
            return 'stale'
        if receipt_status(parent, sid, receipts, visiting) not in ('completed', 'skipped'):
            return 'stale'
    return status


def receipt_delta(receipt, sid, receipts):
    """What a stale receipt's declared scope lost: {'changed': [(kind, path)], 'missing': [(kind, path)]},
    kind being inputs, evidence or dependency (a dependency's path is its stem)."""
    changed, missing = [], []
    for kind in ('inputs', 'evidence'):
        for path, expected in (receipt.get(kind) or {}).items():
            try:
                if _file_hash(path) != expected:
                    changed.append((kind, path))
            except (OSError, ValueError, TypeError):
                missing.append((kind, path))
    for stem, expected in (receipt.get('dependencies') or {}).items():
        parent = (receipts or {}).get(stem)
        if not isinstance(parent, dict) or parent.get('phase') != stem:
            missing.append(('dependency', stem))
        elif not _binds(expected, parent) or receipt_status(parent, sid, receipts) not in ('completed', 'skipped'):
            changed.append(('dependency', stem))
    return {'changed': changed, 'missing': missing}


def refresh_receipt(receipt, sid, receipts, reason, input_paths=(), evidence_paths=()):
    """The receipt re-snapshotted for its delta, with the refresh appended to `refreshes`."""
    if not isinstance(receipt, dict):
        raise ValueError('no receipt to refresh')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('refresh needs a reason naming what the delta changed')
    if receipt.get('status') == 'blocked':
        raise ValueError('a blocked receipt is re-recorded, not refreshed')
    stem = receipt.get('phase')
    for dep in receipt.get('dependencies') or {}:
        parent = (receipts or {}).get(dep)
        if (not isinstance(parent, dict) or parent.get('phase') != dep
                or receipt_status(parent, sid, receipts) not in ('completed', 'skipped')):
            raise ValueError('dependency is not current: ' + dep)
    delta = receipt_delta(receipt, sid, receipts)
    changed = [path for kind, path in delta['changed'] if kind != 'dependency']
    moved = [name for kind, name in delta['changed'] if kind == 'dependency']
    snapshots = {}
    for key, extra in (('inputs', input_paths), ('evidence', evidence_paths)):
        paths = list(receipt.get(key) or {}) + [os.path.abspath(path) for path in extra]
        snapshots[key] = {path: _file_hash(path) for path in dict.fromkeys(paths)}
        changed += [path for path in snapshots[key] if path not in (receipt.get(key) or {})]
    changed = list(dict.fromkeys(changed))
    if not changed and not moved:
        raise ValueError('nothing changed since the receipt')
    refreshed = dict(receipt, **snapshots)
    refreshed['dependencies'] = {dep: _conclusion_hash(receipts[dep]) for dep in receipt.get('dependencies') or {}}
    refreshed['refreshes'] = list(receipt.get('refreshes') or []) + [dict(
        reason=reason.strip(), at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        changed=changed + moved)]
    if receipt_status(refreshed, sid, dict(receipts, **{stem: refreshed})) not in ('completed', 'skipped'):
        raise ValueError('refreshed receipt does not read current')
    return refreshed


def delta_text(delta, repo):
    def show(path):
        try:
            under = os.path.relpath(path, repo)
        except ValueError:
            return path
        return path if under.startswith('..') else under.replace(os.sep, '/')
    parts = []
    for label in ('changed', 'missing'):
        kinds = []
        for kind in ('inputs', 'evidence', 'dependency'):
            names = [path if kind == 'dependency' else show(path) for k, path in delta[label] if k == kind]
            if names:
                kinds.append(kind + ' ' + ', '.join(names))
        if kinds:
            parts.append(label + ': ' + '; '.join(kinds))
    return '; '.join(parts)


def gated_skip_invalid(receipt):
    """A skipped gated receipt that stores no predicate, or a true or null one with no in-body reason."""
    predicate = receipt.get('predicate')
    return _predicate_problem(receipt.get('phase'), 'skipped', receipt.get('reason') or '', predicate) is not None


def current_predicates(stems, repo, sid, transcript):
    """The gated stems' predicates evaluated now, as `close_predicates.py` writes them."""
    live = sid == os.environ.get('CLAUDE_CODE_SESSION_ID')
    return close_predicates.evaluate(stems, close_predicates.Ctx(repo, sid, transcript, live))


def stale_skips(receipts, stems, repo, sid, transcript):
    """The stems whose false-predicate skip no longer holds: the predicate is now true or null, or unreadable."""
    rechecked = sorted(stem for stem in stems if stem in GATED and isinstance(receipts.get(stem), dict)
                       and receipts[stem].get('status') == 'skipped'
                       and isinstance(receipts[stem].get('predicate'), dict)
                       and receipts[stem]['predicate'].get('value') is False)
    if not rechecked:
        return set()
    try:
        now = current_predicates(rechecked, repo, sid, transcript)
    except Exception:  # noqa: BLE001 - an evaluator failure is a null predicate, so the phase runs
        return set(rechecked)
    return {stem for stem in rechecked
            if not isinstance(now, dict) or not isinstance(now.get(stem), dict) or now[stem].get('value') is not False}


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


def land_prefix():
    """`adaptation.json` `land_prefix`, empty when the project has no land runner."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import adaptation
    finally:
        sys.path.pop(0)
    prefix = adaptation.load(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).get('land_prefix')
    return prefix if isinstance(prefix, str) else ''


def unpushed_commits(repo):
    """Commits on HEAD that its upstream and every remote land branch (`land_prefix`) lack: an int, or None
    when the count cannot be read (no repo, no upstream). Phase 7 runs /commit_push, and pushing is part of
    it; a land push hands the commits to the runner before the upstream moves. A commit whose patch a ref
    carries under another sha (a land replayed from a scratch worktree) counts as published."""
    land = land_prefix()

    def git(*a):
        return git_run(a, cwd=repo, timeout=60)
    try:
        refs = ['@{u}']
        if land:
            r = git('for-each-ref', '--format=%(refname)', 'refs/remotes/*/' + land + '*')
            if r.returncode != 0:
                return None
            refs += r.text.split()
        unpublished = None
        for ref in refs:
            r = git('cherry', ref, 'HEAD')
            if r.returncode != 0:
                return None
            plus = {line[2:] for line in r.text.splitlines() if line.startswith('+ ')}
            unpublished = plus if unpublished is None else unpublished & plus
    except (OSError, subprocess.SubprocessError):
        return None
    return len(unpublished)


def unpublished_peer_sessions(repo, transcript):
    """Claude-Session URLs on HEAD's unpushed commits other than this session's own: a push would publish
    them, the case `/session_end` Phase 7a defers to the last session out. None when the range or this
    session's own trailer cannot be read, so the skip fails closed."""
    own = {line.split(':', 1)[1].strip() for line in close_predicates.session_trailers(transcript)}
    if not own:
        return None
    try:
        r = git_run(['log', '--format=%(trailers:key=Claude-Session,valueonly)', '@{u}..HEAD'],
                    cwd=repo, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {line.strip() for line in r.text.splitlines() if line.strip()} - own


def unresolved_pull(repo):
    """Paths an earlier `baseline_sync.py pull` wrote whose proof is still red or pending and that no commit has
    taken (the pull journal, `hooks/_pull_proof.py`): rows left in the tree that nothing else reports at close.
    Empty when the project keeps no journal."""
    hooks = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hooks')
    sys.path.insert(0, hooks)
    try:
        import _pull_proof
    except ImportError:
        return []
    finally:
        sys.path.remove(hooks)
    state, rows = _pull_proof.record_state(repo)
    if state == _pull_proof.UNREADABLE:
        # Unknown rows stay named: the record itself is the open row.
        return [_pull_proof.RECORD]
    return _pull_proof.open_paths(repo, rows)


def phase_mandatory(stem, mandatory, project_dir=None):
    """Whether `stem` must end completed or skipped: its PHASES flag, unless it runs a command the project lacks."""
    if not mandatory or stem not in PROJECT_COMMAND_PHASES:
        return mandatory
    return os.path.isfile(os.path.join(project_dir or _PROJECT_DIR, '.claude', 'commands', stem + '.md'))


# Shared-checkout artifacts a peer's next run reads, each with the path whose change in this session makes it the
# session's own (None: always listed, since a staged leftover is no commit). `lock rows` is judged by the findings
# `baseline_sync.py check --json` prints when it is not clean; the other checks are their tool's exit code.
SHARED_ARTIFACTS = (
    ('index', None),
    ('lock rows', re.compile(r'\.claude/baseline\.lock\.json')),
    ('model registry', re.compile(r'\.claude/reference/external_models\.json')),
    ('composed settings', re.compile(r'\.claude/settings(?!\.local\.json)(?:\.[A-Za-z0-9_-]+)?\.json')),
)
LOCK_FINDING_KEYS = ('layer_closure', 'layer_drift', 'unimported_layer_files')
# `baseline_sync.py check --json` took 26 s on this repo, the other checks under a second.
CHECK_TIMEOUT_SECONDS = 180


def _run_check(argv, repo):
    """(exit code, stdout) of one artifact check run in `repo`; OSError and a timeout propagate."""
    r = subprocess.run(argv, cwd=repo, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       timeout=CHECK_TIMEOUT_SECONDS)
    return r.returncode, r.stdout


def artifact_changes(repo, sid, transcript):
    """(the repo-relative paths this session's commits and named dirty files touch, None) or (None, reason)."""
    try:
        return close_predicates.Ctx(repo, session=sid, transcript=transcript, live=True).git_changes()
    except Exception as error:  # an unreadable scope runs every check; it never reads as "nothing changed"
        return None, str(error)


def _artifact_verdict(name, repo, runner):
    """(state, detail) for one artifact: ok, FAIL, or unknown when the check could not run or be read."""
    tools = [sys.executable, os.path.join(_TOOLS_DIR, 'baseline_sync.py')]
    try:
        if name == 'index':
            rc, out = runner(['git', 'diff', '--cached', '--name-only'], repo)
            if rc != 0:
                return 'unknown', 'git diff --cached exit %d' % rc
            staged = [line for line in out.splitlines() if line.strip()]
            return ('FAIL', '%d path(s) staged: %s' % (len(staged), ', '.join(staged[:5]))) if staged \
                else ('ok', 'nothing staged')
        if name == 'lock rows':
            rc, out = runner(tools + ['check', '--json'], repo)
            try:
                payload = json.loads(out)
            except ValueError:
                return 'unknown', 'check --json exit %d printed no JSON' % rc
            if rc != 0 or not isinstance(payload, dict):
                return 'unknown', 'check --json exit %d' % rc
            found = [key for key in LOCK_FINDING_KEYS if payload.get(key)]
            return ('FAIL', 'check --json reports ' + ', '.join(found)) if found else ('ok', 'check --json: no finding')
        argv = ([sys.executable, os.path.join(_TOOLS_DIR, 'model_registry.py'), '--check'] if name == 'model registry'
                else tools + ['compose', '--check'])
        rc, out = runner(argv, repo)
        first = next((line.strip() for line in out.splitlines() if line.strip()), '')
        label = ' '.join(os.path.basename(a) for a in argv[1:])
        return ('ok', first or label + ' exit 0') if rc == 0 else ('FAIL', '%s exit %d: %s' % (label, rc, first))
    except (OSError, subprocess.SubprocessError) as error:
        return 'unknown', 'check could not run: %s' % error


def shared_artifact_lines(repo, changed, reason=None, runner=None):
    """One line per shared-checkout artifact the session changed (`SHARED_ARTIFACTS`), each with its check's result.
    `changed` None (its `reason` names why) means the session's scope is unknown, so every artifact is checked."""
    runner = runner or _run_check
    lines = ['shared-checkout artifacts this session changed:' if changed is not None else
             'shared-checkout artifacts: this session\'s changes are unreadable (%s), so every check ran:' % reason]
    for name, trigger in SHARED_ARTIFACTS:
        if changed is not None and trigger is not None and not any(trigger.fullmatch(p) for p in changed):
            continue
        state, detail = _artifact_verdict(name, repo, runner)
        lines.append('  [%s] %s — %s' % (state, name, detail))
    return lines


def worklog_session_lines(sid, repo):
    """The open worklog items `sid` added (`worklog.session_additions`): a count line, then one line per item
    with its Context. A worklog that cannot be read reads unknown and names why."""
    try:
        text = worklog.read_text(str(worklog.vault_todo(repo) / worklog.WORKLOG_NAME))
    except (OSError, ValueError) as error:
        return ['worklog items this session added: unknown (%s)' % error]
    items = worklog.session_additions(text, sid)
    return ['worklog items this session added (open): %d' % len(items)] + \
        ['  - %s — %s' % (title, context) for title, context in items]


def render(path, sid, receipts=None, stage='precommit', repo=None):
    """Print observation and receipt status separately; every phase needs a disposition."""
    observations, receipts = phase_observations(path), receipts or {}
    print('session_end evidence — ' + sid)
    print('transcript: ' + os.path.basename(path))
    print('Completion is author-recorded; only declared inputs, evidence and dependencies are freshness-checked.')
    missing, resume_label = [], None
    stale = stale_skips(receipts, required_phases(stage), repo, sid, path)
    for label, stem, mandatory in PHASES:
        if stem not in required_phases(stage):
            continue
        mandatory = phase_mandatory(stem, mandatory, repo)
        receipt = receipts.get(stem)
        status = receipt_status(receipt, sid, receipts)
        if stem in GATED:
            if (status in ('skipped', 'unknown') and isinstance(receipt, dict)
                    and receipt.get('status') == 'skipped' and gated_skip_invalid(receipt)):
                status = 'invalid-skip'
            elif status == 'skipped' and stem in stale:
                status = 'stale-skip'
        elif status == 'skipped' and mandatory and not (
                stem == 'commit_push' and repo and unpublished_peer_sessions(repo, path)):
            status = 'invalid-skip'
        # A commit receipt is complete only when the push landed; an unreadable count is not a push.
        if stem == 'commit_push' and status == 'completed' and repo and unpushed_commits(repo) != 0:
            status = 'unpushed'
        seen = observations[stem]
        note = delta_text(receipt_delta(receipt, sid, receipts), repo or harness_root()) if status == 'stale' else ''
        print(f'  [{status}] {label} ({stem}) — accessed={seen["accessed"]}, invoked={seen["invoked"]}'
              + (' — ' + note if note else ''))
        # A non-mandatory phase (Phase 8) may end blocked without failing the close.
        if status not in ('completed', 'skipped') and not (status == 'blocked' and not mandatory):
            missing.append(label.strip())
            resume_label = resume_label or label
    return missing, resume_label


def _write_receipt(target, receipt):
    spec = importlib.util.spec_from_file_location('_close_state',
        os.path.join(_PROJECT_DIR, '.claude', 'hooks', '_hook_state.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not module.write_json_atomic(target, receipt):
        raise ValueError('receipt write failed')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID") or None,
                    help="exact session id or unique prefix (default: active session; no newest fallback)")
    ap.add_argument("--repo", default=os.environ.get("CLAUDE_PROJECT_DIR") or harness_root())
    ap.add_argument("--transcript", help="path to a transcript JSONL (proofs only; skips discovery)")
    ap.add_argument("--resume", action="store_true",
                     help="print the table + RESUME AT line; always exits 0")
    ap.add_argument('--stage', choices=('precommit', 'final'), default='precommit')
    ap.add_argument('--receipts', help='session receipt directory')
    ap.add_argument('--record', choices=[stem for _, stem, _ in PHASES])
    ap.add_argument('--refresh', choices=[stem for _, stem, _ in PHASES],
                    help='re-snapshot a stale receipt for its delta; needs --reason naming what the delta changed')
    ap.add_argument('--status', choices=('completed', 'skipped', 'blocked'))
    ap.add_argument('--input', action='append', default=[])
    ap.add_argument('--evidence', action='append', default=[])
    ap.add_argument('--depends', action='append', default=[])
    ap.add_argument('--reason', default='')
    ap.add_argument('--predicate', metavar='FILE',
                    help='gated phases: the close_predicates.py output naming this stem; required to skip one')
    ap.add_argument('--current-inputs', metavar='PHASE',
                    help='retired: use review_ledger.py uncovered (session_end.md Phase 1)')
    a = ap.parse_args()

    if a.current_inputs:
        print('retired: use review_ledger.py uncovered (session_end.md Phase 1)')
        return 2

    path = a.transcript or transcript_for(a.session, a.repo)
    if not path:
        print("No transcript found — phase invocation is UNKNOWN, never 'all ran'.")
        return 2

    sid = session_id_of(path)
    if a.transcript and ((a.session is not None and a.session != sid) or ((a.record or a.refresh) and not a.session)):
        print('REFUSED: --transcript requires matching exact session identity when supplied or recording.')
        return 2
    directory = a.receipts or os.path.join(a.repo, '.claude', 'scratch', 'session_end', sid)
    receipts = load_receipts(directory, sid)
    if a.refresh:
        try:
            if a.record:
                raise ValueError('--refresh and --record are separate operations')
            receipt = refresh_receipt(receipts.get(a.refresh), sid, receipts, a.reason, a.input, a.evidence)
            _write_receipt(os.path.join(directory, a.refresh + '.json'), receipt)
        except (OSError, ValueError, KeyError) as error:
            print('REFUSED refresh: ' + str(error))
            return 2
        print('Refreshed ' + a.refresh + ': ' + str(receipt['status']))
        return 0
    if a.record:
        try:
            dependencies = {stem: receipts[stem] for stem in a.depends}
            predicate = None
            if a.predicate:
                with open(a.predicate, encoding='utf-8') as stream:
                    named = json.load(stream)
                predicate = named.get(a.record) if isinstance(named, dict) else None
                if predicate is None:
                    raise ValueError('predicate file does not name ' + a.record)
            receipt = make_receipt(sid, a.record, a.status, a.input, a.evidence, a.reason, dependencies, receipts,
                                   predicate)
            target = os.path.join(directory, a.record + '.json')
            if os.path.exists(target) and a.record not in receipts:
                raise ValueError('existing receipt has unknown or foreign ownership')
            _write_receipt(target, receipt)
        except (OSError, ValueError, KeyError) as error:
            print('REFUSED receipt: ' + str(error))
            return 2
        print('Recorded ' + a.record + ': ' + a.status)
        return 0
    missing, resume_label = render(path, sid, receipts, a.stage, a.repo)
    stuck = unresolved_pull(a.repo)
    if stuck:
        print('unresolved baseline pull: ' + ', '.join(stuck))
        print('  an earlier pull wrote these paths and no commit has taken them: their proof is red or pending, '
              'or the pull record is unreadable')

    if a.stage == 'final' and not a.resume:
        changed, why = artifact_changes(a.repo, sid, path)
        print()
        print('\n'.join(shared_artifact_lines(a.repo, changed, why)))
        print('\n'.join(worklog_session_lines(sid, a.repo)))

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
    if stuck:
        return 1
    print("All checked phases have current receipts; verify their stated scope before claiming full closeout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
