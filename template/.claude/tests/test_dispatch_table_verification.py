"""Slice 6: Stop evidence audit. --drift compares both frozen explore fixtures with HEAD.

Must-allow fixtures: fresh-worktree merge, merge close/--continue, ignored-input-free
commit, and a consumer citing an upstream-deleted file. Stop never gates those commands.
The audit ignores VERIFIED -> non-VERIFIED file drift, not missing run-time verdicts.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / '.claude/tools'))
sys.path.insert(0, str(HERE))
import verify_claims


def drift():
    fixture = HERE / 'fixtures/evidence'
    rev = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    tracked = subprocess.check_output(['git', '-C', str(REPO), 'ls-tree', '-r', '--name-only', rev], text=True).splitlines()
    totals = {}
    with tempfile.TemporaryDirectory(prefix='stop_drift_') as tmp:
        tree = Path(tmp)
        for name in ('r1', 'r2'):
            source = fixture / name
            claims = json.loads((source / 'claims.json').read_text(encoding='utf-8'))
            old_rows, new_rows = [], []
            cited = set()
            for entry in claims:
                old = verify_claims.items_of(entry['claim'], root=source / 'tree',
                                            tracked=(source / 'tracked.txt').read_text().splitlines())
                new = verify_claims.items_of(entry['claim'], root=tree, tracked=tracked)
                old_rows.append(dict(id=entry['id'], evidence=old, polarity=entry['claim'].get('polarity')))
                new_rows.append(dict(id=entry['id'], evidence=new, polarity=entry['claim'].get('polarity')))
                cited.update(verify_claims.citation_path(i['file'])[0] for i in new if i.get('file'))
            unreadable = []
            for rel in cited:
                path = Path(rel)
                if path.is_absolute():
                    unreadable.append(rel)
                    continue
                run = subprocess.run(['git', '-C', str(REPO), 'show', f'{rev}:{rel}'], capture_output=True)
                if run.returncode == 0:
                    target = tree / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(run.stdout)
            old = verify_claims.check_claims(old_rows, root=source / 'tree')
            new = verify_claims.check_claims(new_rows, root=tree)
            flipped = [key for key, row in old['verdicts'].items()
                       if row['verdict'] == 'VERIFIED' and new['verdicts'][key]['verdict'] != 'VERIFIED']
            totals[name] = dict(total=len(claims), before=old['counts'], after=new['counts'],
                                verifiedFlips=flipped,
                                flipReasons={key: [dict(kind=i['kind'], reason=i['reason'], file=i.get('file'))
                                                   for i in new['verdicts'][key]['items'] if i['verdict'] != 'VERIFIED']
                                             for key in flipped},
                                absolutePathsUnattempted=unreadable)
    print(json.dumps(dict(head=rev, fixtureRev=(fixture / 'REV.txt').read_text().strip(), rounds=totals), indent=2))


from test_dispatch_table import Env, SID, workflow_payload, stop_payload, bash_payload
from _must_allow_cases import must_allow_cases
from unittest.mock import patch
import time


def plant(verification=None, *, quote='a real quote', applied='VERIFIED', name='explore_fanout', extra=None):
    e = Env()
    evidence = Path(e.root) / 'source.txt'
    evidence.write_text('a real quote\n', encoding='utf-8')
    row = dict(id='C1', lens='seat', index=0, subject='source', polarity='exists', claim='source holds the quote',
               bearing='context', evidence=[dict(file=str(evidence), quote=quote)],
               verdict=applied, confidence='verified' if applied == 'VERIFIED' else 'unverified')
    run_id = 'audit-run'
    e.plant_run(run_id, 'completed', [])
    host = Path(e.session) / 'workflows' / (run_id + '.json')
    value = dict(claims=[row])
    if verification is not None:
        value['verification'] = verification
        checked = verify_claims.check_claims([dict(row, id='seat#0')])
        report = Path(e.root) / 'in-run.json'
        report.write_text(json.dumps(checked), encoding='utf-8')
        verification['report'] = str(report)
    if extra:
        value.update(extra)
    record = json.loads(host.read_text())
    record.update(workflowName=name, result=value)
    host.write_text(json.dumps(record), encoding='utf-8')
    journal = Path(e.session) / 'subagents/workflows' / run_id / 'journal.jsonl'
    original = dict(row)
    original.pop('verdict')
    original.pop('confidence')
    journal.write_text(explore_journal(original))
    e.run(workflow_payload(dict(scriptPath=f'.claude/workflows/{name}.js', args={}), run_id), '--launch')
    return e, row, journal


def explore_journal(*claims):
    """One explore lens's journal as the host writes it: keyed started/result rows with a checked block."""
    checked = dict(toolsUsed=['Read'], stoppedAt='exhausted-leads', basis='fixture')
    claims = [{k: v for k, v in claim.items() if k not in ('id', 'lens', 'index')} for claim in claims]  # the engine stamps these
    return (json.dumps(dict(type='started', agentId='a1', key='k1', label='explore:seat', phase='Explore')) + '\n'
            + json.dumps(dict(type='result', agentId='a1', key='k1', result=dict(claims=list(claims), checked=checked))) + '\n')


def block(verdict='VERIFIED', status='pass', key='seat#0'):
    return dict(status=status, counts={v: int(v == verdict) for v in ('VERIFIED', 'UNVERIFIED', 'UNCHECKED')},
                verdicts={key: verdict}, report='unused-in-run-report.json')


def reports(e):
    return list(Path(e.root).rglob('verification_audit.json')) + list(Path(e.root).rglob('*.verification_audit.json'))


def test_absent_block_defers_stop_once_and_records_outcome():
    e, _, _ = plant()
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'absent verification must block on Stop reason channel'
    assert 'VERIFIED' in e.body['reason'] and 'read the source' in e.body['reason'].lower()
    assert reports(e) and str(reports(e)[0]) in e.body['reason']
    assert e.pending()[0]['verification_audit']['outcome'] == 'block'
    e.run(stop_payload(), '--complete')
    assert not e.body and not e.pending(), 'second Stop must be silent and prune the finished entry'


def test_bounded_verification_uses_complete_report_and_checks_applied_preview():
    e, row, _ = plant(block())
    host = Path(e.session) / 'workflows/audit-run.json'
    value = json.loads(host.read_text())
    verification = value['result']['verification']
    verification.pop('verdicts')
    verification['nonVerified'] = []
    value['result']['claims'] = []
    value['result']['delivery'] = dict(payloadComplete=False, resultMode='bounded')
    host.write_text(json.dumps(value))
    e.run(stop_payload(), '--complete')
    assert not e.body and reports(e), e.body


def test_bounded_report_spelled_by_git_bash_is_read():
    """A verifier agent returns its report path in its own shell's spelling (`/c/Users/...`)."""
    if os.name != 'nt':
        return
    e, row, _ = plant(block())
    host = Path(e.session) / 'workflows/audit-run.json'
    value = json.loads(host.read_text())
    verification = value['result']['verification']
    native = verification['report'].replace('\\', '/')
    verification['report'] = '/' + native[0].lower() + native[2:]
    verification.pop('verdicts')
    verification['nonVerified'] = []
    value['result']['claims'] = []
    value['result']['delivery'] = dict(payloadComplete=False, resultMode='bounded')
    host.write_text(json.dumps(value))
    e.run(stop_payload(), '--complete')
    assert not e.body and reports(e), e.body


def bounded_nonverified_preview(ids, shed):
    e, _, _ = plant(block('UNVERIFIED', 'fail'), quote='absent quote', applied='UNVERIFIED')
    host = Path(e.session) / 'workflows/audit-run.json'
    value = json.loads(host.read_text())
    verification = value['result']['verification']
    verification.pop('verdicts')
    verification['nonVerified'] = ids
    value['result']['claims'] = []
    value['result']['delivery'] = dict(payloadComplete=False, resultMode='bounded',
                                     processedState=dict(nonVerifiedInline=not shed))
    host.write_text(json.dumps(value))
    e.run(stop_payload(), '--complete')
    return e


def test_bounded_shed_nonverified_prefix_is_silent():
    e = bounded_nonverified_preview([], True)
    assert not e.body and reports(e), e.body


def test_bounded_shed_nonverified_foreign_id_blocks():
    e = bounded_nonverified_preview(['foreign#0'], True)
    assert e.body.get('decision') == 'block'


def test_bounded_unmarked_short_nonverified_blocks():
    e = bounded_nonverified_preview([], False)
    assert e.body.get('decision') == 'block'


def test_bounded_missing_report_blocks():
    e, _, _ = plant(block())
    host = Path(e.session) / 'workflows/audit-run.json'
    value = json.loads(host.read_text())
    verification = value['result']['verification']
    Path(verification['report']).unlink()
    verification.pop('verdicts')
    verification['nonVerified'] = []
    host.write_text(json.dumps(value))
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block'


def test_match_writes_report_and_is_silent():
    e, _, _ = plant(block())
    e.run(stop_payload(), '--complete')
    assert not e.body, e.body
    assert reports(e), 'matching dispatch still needs a recomputed report'
    report = json.loads(reports(e)[0].read_text())
    assert report['counts'] == dict(VERIFIED=1, UNVERIFIED=0, UNCHECKED=0)
    assert report['outcome'] == 'match'


def test_runtime_unverified_but_now_true_is_a_mismatch():
    e, _, _ = plant(block('UNVERIFIED', 'fail'), applied='UNVERIFIED')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'run-time non-VERIFIED disagreement must block'


def test_verified_file_drift_does_not_block():
    e, row, _ = plant(block())
    Path(row['evidence'][0]['file']).write_text('changed after dispatch\n')
    e.run(stop_payload(), '--complete')
    assert not e.body, e.body
    assert reports(e), 'drift must still be recorded'
    report = json.loads(reports(e)[0].read_text())
    assert report['counts']['UNVERIFIED'] == 1 and report['drift'] == ['seat#0']


def test_verified_copy_over_unchanged_absent_quote_blocks():
    e, _, _ = plant(block(), quote='fabricated quote')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'unchanged bytes are not drift'
    assert json.loads(reports(e)[0].read_text())['drift'] == []


def test_verified_legacy_label_without_fingerprint_blocks():
    e, row, journal = plant(block())
    row['evidence'] = [dict(file=None, quote='a real quote', legacy=True, why='unresolved-label')]
    host = Path(e.session) / 'workflows/audit-run.json'
    value = json.loads(host.read_text())
    value['result']['claims'] = [row]
    host.write_text(json.dumps(value))
    journal.write_text(explore_journal({k: v for k, v in row.items() if k not in ('verdict', 'confidence')}))
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'unresolved labels without changed cited bytes are not drift'
    assert json.loads(reports(e)[0].read_text())['drift'] == []


def test_applied_verdict_and_confidence_must_reach_consumer():
    e, _, _ = plant(block(), applied='UNCHECKED')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'correct block with unapplied item must block'


def test_unavailable_blocks_even_when_quotes_match():
    e, _, _ = plant(block('UNCHECKED', 'unavailable'), applied='UNCHECKED')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block'


def test_unavailable_verifier_is_named_as_the_cause_not_as_mismatches():
    cause = "run label 'verify-x' matched 0 live workflow journals; expected one"
    e, _, _ = plant(block('UNCHECKED', 'unavailable'), applied='UNCHECKED')
    (Path(e.root) / 'in-run.json').write_text(json.dumps(dict(status='unavailable', errors=[cause])), encoding='utf-8')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'a missing verifier is still a block'
    reason = e.body['reason']
    assert 'did not run' in reason and cause in reason, reason
    assert 'post_check.py claims --workflow-dir' in reason and 'Step 1.5' in reason, reason
    assert 'mismatch' not in reason and 'applied' not in reason, 'no verdict was applied, so none can mismatch: ' + reason
    audit = json.loads(reports(e)[0].read_text())
    assert audit['mismatches'] == [] and audit['outcome'] == 'block', audit
    assert audit['verifier']['status'] == 'unavailable' and cause in audit['verifier']['errors'], audit


def test_absent_verification_block_names_the_missing_verifier():
    e, _, _ = plant()
    e.run(stop_payload(), '--complete')
    reason = e.body['reason']
    assert 'did not run' in reason and 'no verification block' in reason, reason
    assert 'mismatch' not in reason, reason
    assert json.loads(reports(e)[0].read_text())['mismatches'] == []


def test_unavailable_verifier_with_unreadable_report_names_the_path():
    e, _, _ = plant(block('UNCHECKED', 'unavailable'), applied='UNCHECKED')
    host = Path(e.session) / 'workflows/audit-run.json'
    record = json.loads(host.read_text())
    record['result']['verification']['report'] = 'gone/post_check-verify-x.json'
    host.write_text(json.dumps(record), encoding='utf-8')
    e.run(stop_payload(), '--complete')
    assert 'gone/post_check-verify-x.json' in e.body['reason'] and 'unreadable' in e.body['reason'], e.body['reason']


def test_bad_counts_and_missing_source_keys_block():
    bad = block()
    bad['counts']['VERIFIED'] = 9
    e, _, _ = plant(bad)
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block'
    e, _, _ = plant(block(key='wrong#1'))
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block'


def test_generic_dispatch_without_claims_is_allowed():
    e = Env()
    e.plant_run('plain', 'completed', [])
    e.run(workflow_payload(dict(scriptPath='.claude/workflows/dispatch.js', args={}), 'plain'), '--launch')
    e.run(stop_payload(), '--complete')
    assert not e.body and not reports(e)


def test_exempt_engine_with_claims_is_not_exempt_from_audit():
    e, _, _ = plant(name='idea_fanout')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block'


def test_review_uses_finding_ids_and_host_count():
    e, row, _ = plant(block(key='F1'), name='review_fanout')
    host = Path(e.session) / 'workflows/audit-run.json'
    record = json.loads(host.read_text())
    row.update(id='F1')
    record['result'] = dict(findings=[row], counts=dict(total=2), verification=block(key='F1'))
    host.write_text(json.dumps(record))
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'host count must expose unrouted findings'
    assert reports(e)


def test_review_merging_findings_is_not_a_missing_source():
    """`counts.total` counts consolidated views, fewer than the sources when findings merge;
    `counts.raw` is the host's source count, which the recovered sources must match."""
    e, row, _ = plant(block(key='F1'), name='review_fanout')
    host = Path(e.session) / 'workflows/audit-run.json'
    record = json.loads(host.read_text())
    row.update(id='F1')
    record['result'] = dict(findings=[row], counts=dict(raw=1, total=0), verification=block(key='F1'))
    host.write_text(json.dumps(record))
    e.run(stop_payload(), '--complete')
    audit = json.loads(next(iter(reports(e))).read_text()) if reports(e) else {'errors': []}
    assert not any('differs from' in error for error in audit['errors']), audit['errors']


def plant_review_run(name, run_id, *, args, result, report):
    e = Env()
    e.plant_run(run_id, 'completed', [])
    report_path = Path(e.root) / 'in-run.json'
    report_path.write_text(json.dumps(report), encoding='utf-8')
    result = dict(result, verification=dict(result['verification'], report=str(report_path)))
    host = Path(e.session) / 'workflows' / (run_id + '.json')
    record = json.loads(host.read_text())
    # The host names a run by the engine's meta.name, which hyphenates the script's basename.
    record.update(workflowName=name.replace('_', '-'), result=result)
    host.write_text(json.dumps(record), encoding='utf-8')
    payload = workflow_payload(dict(scriptPath=f'.claude/workflows/{name}.js', args=args), run_id)
    payload['tool_response']['workflowName'] = name.replace('_', '-')
    e.run(payload, '--launch')
    return e, Path(e.session) / 'subagents/workflows' / run_id


def test_foldless_review_reads_the_source_its_close_check_read():
    fixture = HERE / 'fixtures/stop_audit/review_foldless'
    e, run_dir = plant_review_run(
        'review_fanout', 'wf_review-foldless', args={'spillDir': str(Path(tempfile.mkdtemp()) / 'spill')},
        result=json.loads((fixture / 'result.json').read_text(encoding='utf-8')),
        report=json.loads((fixture / 'in_run_report.json').read_text(encoding='utf-8')))
    (run_dir / 'journal.jsonl').write_bytes((fixture / 'journal.jsonl').read_bytes())
    e.run(stop_payload(), '--complete')
    audit = json.loads((run_dir / 'verification_audit.json').read_text())
    assert not e.body and audit['outcome'] == 'match', audit['mismatches']
    assert sorted(audit['verdicts']) == ['F1', 'F2', 'F3', 'F4', 'F5']


def _rejected_review_audit(counts):
    """The live run wf_281d107b-f78 trimmed: five valid findings plus two the engine rejected, all seven in the journal."""
    fixture = HERE / 'fixtures/stop_audit/review_foldless'
    e, run_dir = plant_review_run(
        'review_fanout', 'wf_review-rejected', args={'spillDir': str(Path(tempfile.mkdtemp()) / 'spill')},
        result=dict(json.loads((fixture / 'result.json').read_text(encoding='utf-8')), counts=counts),
        report=json.loads((fixture / 'in_run_report.json').read_text(encoding='utf-8')))
    (run_dir / 'journal.jsonl').write_bytes((HERE / 'fixtures/evidence/review_rejected/journal.jsonl').read_bytes())
    e.run(stop_payload(), '--complete')
    return json.loads((run_dir / 'verification_audit.json').read_text())['errors']


def test_rejected_findings_are_not_a_source_count_gap():
    if not REJECTED_JOURNAL.exists():
        return
    errors = _rejected_review_audit(dict(raw=5, rejected=2, merged=5, total=5))
    assert not any('differs from' in error for error in errors), errors


def test_a_source_missing_beyond_the_rejected_still_errors():
    if not REJECTED_JOURNAL.exists():
        return
    errors = _rejected_review_audit(dict(raw=6, rejected=2, merged=6, total=6))
    assert any('host source count 8 differs from 7' in error for error in errors), errors


def test_worklog_checked_item_is_one_applied_item_not_a_container():
    if not (HERE / 'fixtures/stop_audit/worklog_relevance/result.json').is_file():
        return
    fixture = HERE / 'fixtures/stop_audit/worklog_relevance'
    e, run_dir = plant_review_run(
        'worklog_relevance', 'wf_worklog', args={},
        result=json.loads((fixture / 'result.json').read_text(encoding='utf-8')),
        report=json.loads((fixture / 'in_run_report.json').read_text(encoding='utf-8')))
    (run_dir / 'journal.jsonl').write_bytes((fixture / 'journal.jsonl').read_bytes())
    report = Path(e.root) / 'in-run.json'
    report.write_text(json.dumps(dict(json.loads(report.read_text()), source=['--workflow-dir', str(run_dir)])))
    e.run(stop_payload(), '--complete')
    audit = json.loads((run_dir / 'verification_audit.json').read_text())
    assert not e.body and audit['outcome'] == 'match', (audit['mismatches'], audit['errors'])
    assert sorted(audit['verdicts']) == ['worklog:relevance#1', 'worklog:relevance#2', 'worklog:relevance#checked']


def test_engine_that_refused_its_args_before_any_agent_is_not_audited():
    e, run_dir = plant_review_run('review_fanout', 'wf_refused', args={},
                                  result={'verification': {}}, report={})
    host = Path(e.session) / 'workflows/wf_refused.json'
    record = json.loads(host.read_text())
    record['result'] = {'error': 'review-fanout: args.ledger.subject must be session:<id>'}
    host.write_text(json.dumps(record), encoding='utf-8')
    e.run(stop_payload(), '--complete')
    assert not e.body, e.body



def test_a_refusal_that_names_its_bad_jobs_is_not_audited():
    # dispatch.js refuses bad jobs with {error, badJobs}; a launched-only journal holds no agent result.
    e, run_dir = plant_review_run('review_fanout', 'wf_badjobs', args={},
                                  result={'verification': {}}, report={})
    host = Path(e.session) / 'workflows/wf_badjobs.json'
    record = json.loads(host.read_text())
    record['result'] = {'error': 'Every job needs {label, promptPath, model, effort, agentType}', 'badJobs': ['a', 'b']}
    host.write_text(json.dumps(record), encoding='utf-8')
    (run_dir / 'journal.jsonl').write_text(json.dumps({'type': 'launched'}) + '\n', encoding='utf-8')
    e.run(stop_payload(), '--complete')
    assert not e.body, e.body

def test_errored_engine_with_agent_results_is_still_audited():
    e, run_dir = plant_review_run('review_fanout', 'wf_crashed', args={},
                                  result={'verification': {}}, report={})
    host = Path(e.session) / 'workflows/wf_crashed.json'
    record = json.loads(host.read_text())
    record['result'] = {'error': 'crashed after the seats returned'}
    host.write_text(json.dumps(record), encoding='utf-8')
    (run_dir / 'journal.jsonl').write_text(
        json.dumps(dict(type='started', agentId='a1', key='k1', label='review:seat', phase='Review')) + '\n'
        + json.dumps(dict(type='result', agentId='a1', key='k1', result=dict(findings=[dict(
            agent='seat', action='FIX', category='bug', description='d', rationale='r', file='x.md:1')]))) + '\n',
        encoding='utf-8')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'claims that returned without a verification block still block'


def fold_job_run(applied, *, source=None, quote='a real quote'):
    """A dispatch.js fold job: its digest holds no claims; its close check read the findings file."""
    root = Path(tempfile.mkdtemp(prefix='fold_job_'))
    (root / 'source.txt').write_text('a real quote\n', encoding='utf-8')
    findings = root / 'findings.json'
    findings.write_text(json.dumps([dict(id='F1', evidence=[dict(file=str(root / 'source.txt'), quote=quote)])]),
                        encoding='utf-8')
    counts = {v: int(v == applied) for v in ('VERIFIED', 'UNVERIFIED', 'UNCHECKED')}
    report = dict(status='pass' if applied == 'VERIFIED' else 'fail', counts=counts,
                  verdicts={'F1': dict(verdict=applied, items=[])},
                  mode='review', source=source or ['--findings', str(findings)])
    return plant_review_run(
        'dispatch', 'wf_fold-job', args={'root': str(root), 'jobs': [dict(key='fold', fold={'findings': str(findings)})]},
        result={'fold': json.dumps({'digest': {'applied': [dict(id='F1', change='one line')], 'deferred': [],
                                               'decisions': [dict(id='F1', taken=True, source='finding', why='x')]}}),
                'verification': dict(status=report['status'], counts=counts,
                                     nonVerified=[] if applied == 'VERIFIED' else ['F1'])},
        report=report)


def test_fold_job_replays_its_recorded_review_source():
    e, run_dir = fold_job_run('VERIFIED')
    e.run(stop_payload(), '--complete')
    audit = json.loads((run_dir / 'verification_audit.json').read_text())
    assert not e.body and audit['outcome'] == 'match', audit['mismatches']
    assert audit['verdicts']['F1']['verdict'] == 'VERIFIED'


def test_fold_job_mis_copied_verdict_still_blocks():
    e, run_dir = fold_job_run('VERIFIED', quote='a fabricated quote')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'a replayed source must still expose a mis-copied verdict'


def test_fold_job_without_a_verification_block_blocks():
    e, run_dir = fold_job_run('VERIFIED')
    host = Path(e.session) / 'workflows/wf_fold-job.json'
    record = json.loads(host.read_text())
    record['result'].pop('verification')
    host.write_text(json.dumps(record), encoding='utf-8')
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'a job declaring fold or ledger owes a verification block'


def test_recorded_source_naming_another_run_blocks():
    e, run_dir = fold_job_run('VERIFIED', source=['--workflow-dir', str(Path(tempfile.mkdtemp()) / 'other_run')])
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block'
    audit = json.loads((run_dir / 'verification_audit.json').read_text())
    assert any('another run' in error for error in audit['errors']), audit['errors']


def test_launcher_reads_checked_claims_not_raw_result():
    e = Env()
    record = str(Path(e.root) / 'direct.record.json')
    e.run(bash_payload(f'bash .claude/scripts/anthropic_sidecar.sh -l direct -R "{record}"'), '--launch')
    Path(record).write_text('{}')
    Path(record + '.exit').write_text('0')
    row = dict(id='direct#1', evidence=[dict(file=str(Path(e.root) / 'missing'), quote='absent')],
               verdict='UNVERIFIED', confidence='unverified')
    Path(record + '.claims.json').write_text(json.dumps(dict(claims=[row], verification=block('UNVERIFIED', 'fail', 'direct#1'))))
    Path(record + '.out').write_text('raw bytes must not be read as the checked output')
    e.run(stop_payload(), '--complete')
    assert not e.body, 'a correctly applied sidecar downgrade must be silent'
    assert reports(e)
    e.run(stop_payload(), '--complete')
    assert not e.body


def test_foreground_finished_sidecar_is_kept_for_stop_audit():
    e = Env()
    record = str(Path(e.root) / 'foreground.record.json')
    Path(record).write_text('{}')
    Path(record + '.exit').write_text('0')
    Path(record + '.claims.json').write_text(json.dumps(dict(claims=[dict(id='x', evidence=[])])))
    e.run(bash_payload(f'bash .claude/scripts/anthropic_sidecar.sh -l foreground -R "{record}"'), '--launch')
    assert e.pending(), 'foreground completion must not bypass Stop audit'
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block'


def test_deleted_upstream_quote_is_allowed_as_drift():
    e, row, _ = plant(block())
    Path(row['evidence'][0]['file']).unlink()
    e.run(stop_payload(), '--complete')
    assert not e.body and reports(e)


def test_crash_is_advisory_and_other_entries_are_not_hidden():
    e, _, _ = plant(block())
    sys.path.insert(0, str(REPO / '.claude/hooks'))
    import dispatch_table as dt
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=e.state), patch.object(dt, '_verification_audit', side_effect=RuntimeError('planted')):
        text, reason = dt.complete(dict(stop_payload(), session_id=SID, transcript_path=e.transcript))
    assert reason is None, 'audit crash must not block'
    assert 'unreadable' in (text or '')


def test_deadline_shared_by_commands_and_pathspec_probes():
    sys.path.insert(0, str(REPO / '.claude/hooks'))
    import dispatch_table as dt
    clock = [10.0]
    timeouts = []
    def run(rest, root, timeout):
        timeouts.append(timeout)
        clock[0] += min(2, timeout)
        if timeout < 2:
            raise subprocess.TimeoutExpired('git', timeout)
        return 0, '' if rest[0] == 'grep' else 'tracked\n', ''
    rows = [dict(id='x', evidence=[dict(command='git grep absent -- tracked', output=''),
                                    dict(command='git status --short', output='')])]
    with patch.object(dt.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(verify_claims, '_run_git', side_effect=run):
        report = dt._audit_check(rows, REPO, SID, deadline=15)
    assert timeouts == [5, 3, 1], timeouts
    assert report['verdicts']['x']['items'][1]['reason'] == 'unattempted'


def test_expired_command_leaves_applied_verdict_and_is_silent_alone():
    e, row, journal = plant(block())
    row['evidence'] = [dict(command='git status --short', output='')]
    host = Path(e.session) / 'workflows/audit-run.json'
    value = json.loads(host.read_text())
    value['result']['claims'] = [row]
    host.write_text(json.dumps(value))
    journal.write_text(json.dumps(dict(type='started', agentId='a1', label='job')) + '\n' +
                       json.dumps(dict(type='result', agentId='a1', result=dict(claims=[dict(row, id='seat#0')]))) + '\n')
    import dispatch_table as dt
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=e.state), patch.object(dt, '_stop_deadline', return_value=time.monotonic() - 1):
        text, reason = dt.complete(dict(stop_payload(), session_id=SID, transcript_path=e.transcript))
    assert reason is None and reports(e)
    report = json.loads(reports(e)[0].read_text())
    assert report['verdicts']['seat#0']['verdict'] == 'VERIFIED'
    assert report['unattempted'] == ['seat#0']


def test_must_allow_shared_merge_and_absent_ignored_inputs():
    cases, cleanup = must_allow_cases('dispatch_table')
    try:
        for _, payload, cwd, _ in cases:
            e = Env()
            payload = dict(payload, hook_event_name='Stop', transcript_path=e.transcript, session_id=SID)
            payload['cwd'] = cwd
            e.run(payload, '--complete')
            assert not e.body, e.body
        # The shared merge-close fixture is mid-merge; Stop also allows its continue invocation.
        payload = dict(cases[1][1], tool_input=dict(command='git merge --continue'))
        e = Env()
        e.run(payload, '--complete')
        assert not e.body
    finally:
        cleanup()


def test_docstring_names_process_class_and_owner_override():
    text = Path(REPO / '.claude/hooks/dispatch_table.py').read_text(encoding='utf-8')
    assert 'process guard' in text and "Shouldn't it be run automatically within the workflow itself?" in text
    assert 'I need you to give me full confidence and fix this comprehensively.' in text


def test_extra_applied_claim_cannot_hide_outside_verdict_map():
    e, row, _ = plant(block())
    host = Path(e.session) / 'workflows/audit-run.json'
    value = json.loads(host.read_text())
    value['result']['claims'].append(dict(row, id='C2', index=2))
    host.write_text(json.dumps(value))
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'an extra returned claim must be audited too'


def test_missing_checked_sidecar_with_stream_capture_blocks():
    e = Env()
    record = str(Path(e.root) / 'stream.record.json')
    e.run(bash_payload(f'bash .claude/scripts/anthropic_sidecar.sh -l stream -R "{record}"'), '--launch')
    Path(record).write_text('{}')
    Path(record + '.exit').write_text('0')
    Path(record + '.out').write_text(json.dumps(dict(type='result', result='```claims\n{"id":"x","evidence":[]}\n```')))
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') == 'block', 'stream-json claim presence needs a missing-deliverable block'
    assert reports(e)


def test_missing_checked_sidecar_waits_while_its_fanout_driver_publishes():
    # The driver writes <record>.claims.json after the seat's .exit; a Stop between the two is not a defect.
    import watch_records
    e = Env()
    record = str(Path(e.root) / 'seat.record.json')
    e.run(bash_payload(f'bash .claude/scripts/anthropic_sidecar.sh -l seat -R "{record}"'), '--launch')
    Path(record).write_text('{}')
    Path(record + '.exit').write_text('0')
    Path(record + '.out').write_text(json.dumps(dict(type='result', result='```claims\n{"id":"x","evidence":[]}\n```')))
    Path(e.root, 'fanout-live.inventory.json').write_text(json.dumps(
        dict(fanoutProcess=watch_records.process_line(), jobs=[dict(recordPath=record)])))
    e.run(stop_payload(), '--complete')
    assert e.body.get('decision') != 'block', 'a live fan-out driver still owes the claims file: %s' % e.body


def test_parallel_stop_cannot_prune_another_audits_claim():
    e, _, _ = plant(block())
    import dispatch_table as dt
    source = dt._audit_source
    def rival(entry, session, payload):
        result = source(entry, session, payload)
        def claim(state):
            state[dt.STATE_KEY][0]['verification_audit'] = dict(outcome='running')
        dt._hook_state.update_json_locked(e.state_path(), claim)
        return result
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=e.state), patch.object(dt, '_audit_source', side_effect=rival):
        _, reason = dt.complete(dict(stop_payload(), session_id=SID, transcript_path=e.transcript))
    assert reason is None and e.pending(), 'another Stop must not prune the claimed audit entry'
    assert e.pending()[0]['verification_audit']['outcome'] == 'running'


PLAN_REL = '.claude/plans/loop-construct-ports.md'
QUOTE = 'ranges in a type group share a common width'


# The captured native-review journal names project content, so only a checkout that carries it replays it.
NATIVE_FOLD_JOURNAL = HERE / 'fixtures/stop_audit/review_fold_native/journal.jsonl'
REJECTED_JOURNAL = HERE / 'fixtures/evidence/review_rejected/journal.jsonl'


def native_fold_run(snapshot_text, live_text, *, relative=False):
    """The captured native review whose fold already edited its subject: seats read `snapshot_text`, disk holds `live_text`."""
    import fold_check
    fixture = HERE / 'fixtures/stop_audit/review_fold_native'
    root = Path(tempfile.mkdtemp(prefix='fold_native_'))
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    plan = root / PLAN_REL
    plan.parent.mkdir(parents=True)
    plan.write_text(snapshot_text, encoding='utf-8', newline='\n')
    manifest = root / 'snapshot.json'
    manifest.write_text(json.dumps(fold_check.snapshot_blobs(root, [PLAN_REL])), encoding='utf-8')
    plan.write_text(live_text, encoding='utf-8', newline='\n')
    subject = dict(root='.' if relative else str(root), snapshot='snapshot.json' if relative else str(manifest),
                   files=[PLAN_REL])
    e, run_dir = plant_review_run(
        'review_fanout', 'wf_fold-native', args={'spillDir': str(root / 'spill'), 'fold': dict(subjects=[subject])},
        result=json.loads((fixture / 'result.json').read_text(encoding='utf-8')),
        report=json.loads((fixture / 'in_run_report.json').read_text(encoding='utf-8')))
    (run_dir / 'journal.jsonl').write_bytes((fixture / 'journal.jsonl').read_bytes())
    report = Path(e.root) / 'in-run.json'
    report.write_text(json.dumps(dict(json.loads(report.read_text()), source=['--workflow-dir', str(run_dir)])))
    return e, run_dir, root


def stop_in(root):
    return dict(stop_payload(), cwd=str(root))


def test_native_fold_audit_replays_the_snapshot_bytes():
    if not NATIVE_FOLD_JOURNAL.exists():
        return
    e, run_dir, root = native_fold_run('before the fold: ' + QUOTE + '\n', 'after the fold: rewritten\n')
    e.run(stop_in(root), '--complete')
    audit = json.loads((run_dir / 'verification_audit.json').read_text())
    assert audit['verdicts']['F1']['verdict'] == 'VERIFIED', audit['verdicts']['F1']
    assert not e.body and audit['outcome'] == 'match', (audit['mismatches'], audit['errors'])


def test_native_fold_audit_resolves_relative_subject_against_the_payload_cwd():
    if not NATIVE_FOLD_JOURNAL.exists():
        return
    e, run_dir, root = native_fold_run('before the fold: ' + QUOTE + '\n', 'after the fold: rewritten\n', relative=True)
    e.run(stop_in(root), '--complete')
    audit = json.loads((run_dir / 'verification_audit.json').read_text())
    assert audit['verdicts']['F1']['verdict'] == 'VERIFIED', (audit['verdicts']['F1'], audit['errors'])


def test_native_fold_audit_quote_absent_from_the_snapshot_stays_unverified():
    if not NATIVE_FOLD_JOURNAL.exists():
        return
    e, run_dir, root = native_fold_run('before the fold: no such text\n', 'after the fold: ' + QUOTE + '\n')
    e.run(stop_in(root), '--complete')
    audit = json.loads((run_dir / 'verification_audit.json').read_text())
    assert audit['verdicts']['F1']['verdict'] == 'UNVERIFIED', audit['verdicts']['F1']


def measure():
    samples = []
    for _ in range(12):
        e, _, _ = plant(block())
        start = time.perf_counter()
        e.run(stop_payload(), '--complete')
        samples.append(time.perf_counter() - start)
        assert not e.body and reports(e)
    print(json.dumps(dict(samples=len(samples), maxSeconds=max(samples), meanSeconds=sum(samples) / len(samples),
                         reserveBasis='twice the maximum fixed-cost Stop, including process startup, source reads and report/state writes')))


def run_tests():
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print('PASS', name)
            except Exception as exc:
                failures += 1
                print('FAIL', name, '-', type(exc).__name__, str(exc)[:400])
    total = len([n for n, fn in globals().items() if n.startswith('test_') and callable(fn)])
    print(f'{total - failures}/{total} audit cases passed')
    return bool(failures)


if __name__ == '__main__':
    if '--drift' in sys.argv:
        drift()
    elif '--measure' in sys.argv:
        measure()
    else:
        sys.exit(run_tests())
