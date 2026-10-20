#!/usr/bin/env python3
"""Publish checked sidecar deliverables before completion; never rewrite raw captures.

Direct launches write <record>.post_check.json and <record>.claims.json. Fan-outs
use the same source stamping and verdict application after one aggregate check.
A missing, unreadable or incomplete report marks every row UNCHECKED.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch_result
import post_check

VERDICTS = ('VERIFIED', 'UNVERIFIED', 'UNCHECKED')
REPO = Path(__file__).resolve().parents[2]


def source_rows(label, value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    if isinstance(value, dict) and not any(key in value for key in post_check.CLAIM_MEMBERS):
        return [], []
    try:
        rows, errors = post_check.result_claims(label, value, preserve=True, source_indices=True)
    except post_check.Unusable as exc:
        return [], [str(exc)]
    for row in rows:
        index = row.pop('__sourceIndex')
        key = f'{label}#{index}'
        row.update(sourceKey=key, __key=key, lens=label, index=index)
    return rows, errors


def checker_rows(rows):
    return [dict(row, id=row['sourceKey']) for row in rows]


def publish(rows, report_path, output_path, *, result=None, errors=()):
    """Apply one complete report by source key. Fail closed on any report-shape loss."""
    errors = list(errors)
    keys = [row.get('sourceKey') or row.get('id') for row in rows]
    try:
        report = json.loads(Path(report_path).read_text(encoding='utf-8'))
        verdicts = report['verdicts']
        if (report['status'] not in ('pass', 'fail') or len(set(keys)) != len(keys)
                or set(verdicts) != set(keys)
                or any(value.get('verdict') not in VERDICTS for value in verdicts.values())):
            raise ValueError('incomplete verdict report')
        counts = {v: sum(item['verdict'] == v for item in verdicts.values()) for v in VERDICTS}
        if report['counts'] != counts:
            raise ValueError('verdict counts differ')
        if report['status'] == 'pass' and (counts['UNVERIFIED'] or counts['UNCHECKED'] or report.get('errors')):
            raise ValueError('pass contains unchecked, unverified or incomplete sources')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(f'report unavailable: {exc}')
        report = {'status': 'unavailable', 'counts': {v: len(rows) if v == 'UNCHECKED' else 0 for v in VERDICTS},
                  'verdicts': {key: {'verdict': 'UNCHECKED', 'items': []} for key in keys}, 'errors': errors}
        post_check._write(report_path, report)
    if errors and report['status'] == 'pass':
        report['status'] = 'fail'
        report['errors'].extend(errors)
        post_check._write(report_path, report)
    applied = []
    for key, row in zip(keys, rows):
        verdict = report['verdicts'][key]['verdict']
        applied.append(dict(row, verdict=verdict, confidence='verified' if verdict == 'VERIFIED' else 'unverified'))
    verification = {key: report[key] for key in ('status', 'counts')}
    verification.update(verdicts={key: value['verdict'] for key, value in report['verdicts'].items()},
                        report=str(report_path))
    payload = {'claims': applied, 'verification': verification}
    if result is not None:
        payload['result'] = result
    post_check._write(output_path, payload)
    print('sidecar check: ' + report['status'] + ' ' + ' '.join(f'{v}={report["counts"][v]}' for v in VERDICTS)
          + f' -> {output_path}')
    return payload


def complete_record(record, *, root=REPO, capture=None):
    record = Path(record)
    text = dispatch_result.final_message_of(capture or (str(record) + '.out'))
    rows, errors = source_rows(record.stem, text)
    report_path, output = str(record) + '.post_check.json', str(record) + '.claims.json'
    for row in rows:
        row['root'] = str(Path(root).resolve())
    if not rows and not errors and text is not None and not post_check._BLOCK.search(text):
        post_check._write(report_path, {'status': 'pass', 'counts': {v: 0 for v in VERDICTS},
                                       'verdicts': {}, 'errors': []})
    else:
        if text is None:
            errors.append('producing job has no final result')
        source = str(record) + '.claims.input.json'
        post_check._write(source, {'claims': checker_rows(rows)})
        post_check.main(['claims', '--result', source, '--root', str(root), '--out', report_path])
    checked = publish(rows, report_path, output, result=text, errors=errors)
    return 0 if checked['verification']['status'] == 'pass' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', required=True)
    parser.add_argument('--root', default=str(REPO))
    parser.add_argument('--capture', help='host capture before the durable raw output lands')
    args = parser.parse_args()
    return complete_record(args.record, root=args.root, capture=args.capture)


if __name__ == '__main__':
    sys.exit(main())
