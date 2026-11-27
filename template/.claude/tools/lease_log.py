"""Lease history: who held a machine lane, a checkout lock or a runtime slot, and how long each taker waited.

Usage:
  lease_log.py waits [--since T] [--until T] [--min-wait S] [--json] [--lanes PATH] [--runtime PATH]
      T is an ISO time (UTC when it names no offset) or a span back from now (90m, 6h, 2d); the window defaults to
      the last 6 h. Lists each wait of at least S seconds (default 5) that overlaps the window, with the leases that
      held what the waiter asked for while it waited, then the totals per holder.

Two files, one writer each:
  <machine pool>/leases.jsonl                            lanes: machine_slots.take
  <temp>/harness-activity/runtime-slots/leases.jsonl     checkout locks and runtime slots: RuntimeSlots.ps1 Add-LeaseRow
A lease writes a `take` row when it holds and an `end` row when it lets go (`ended`: released, deadline, timeout:<stage>,
error, held-refused); a take that gives up writes only the `end` row, and a killed holder writes none. Each row carries
`start` (the wait began), `at` (this event), `wait` (seconds per stage, in order: checkout, lane, runtime for a run;
lane for a lane), `ask` and `got`. A file past CAP_BYTES keeps its newest KEEP_ROWS rows before the next row is added.

Attribution: a run holds its checkout lock from the end of its checkout stage to its end row, a lane or runtime slot
from its take row to its end row. A lease with no end row ends where the next taker of the same lane, slot or lock
begins, else at the window's end, and is marked open. A whole-machine lane waiter counts against a lane wait while it
queues too, since it goes first. A run's lane stage is listed from the lane file only (its broker wrote it).
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hooks'))
from _hook_state import append_jsonl_rotating  # noqa: E402

# RuntimeSlots.ps1 $script:LeaseLogCapBytes and $script:LeaseLogKeepRows repeat these for the runtime file; test_RuntimeSlots.py
# holds the two equal.
CAP_BYTES = 4_000_000
KEEP_ROWS = 6000
STAGES = {'runtime': ('checkout', 'lane', 'runtime'), 'lane': ('lane',)}


def append(path, row, cap=CAP_BYTES, keep=KEEP_ROWS):
    """One row, best-effort (`append_jsonl_rotating`): a row that cannot take the file's lock is dropped, so the history
    never holds a lease."""
    append_jsonl_rotating(path, [row], max_bytes=cap, keep_lines=keep)


def read(path):
    """(rows, count of unparseable lines); a missing file reads as none."""
    rows, bad = [], 0
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()
    except OSError:
        return rows, bad
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if isinstance(row, dict) and row.get('id') and parse_time(row.get('at')) is not None:
            rows.append(row)
        else:
            bad += 1
    return rows, bad


def stamp(t=None):
    """An epoch time (now when omitted) as the rows' ISO UTC form."""
    when = dt.datetime.fromtimestamp(time.time() if t is None else t, dt.timezone.utc)
    return when.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def parse_time(value, now=None):
    """Epoch seconds for an ISO time or a span back from `now` ('90m', '6h', '2d'); None when it is neither."""
    if not isinstance(value, str) or not value:
        return None
    span = re.fullmatch(r'(\d+(?:\.\d+)?)([smhd])', value)
    if span:
        return (time.time() if now is None else now) - float(span.group(1)) * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[span.group(2)]
    try:
        stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.timestamp()


def leases(rows):
    """Rows paired by id into leases: start, held (take time or None), end (end time or None) and the row fields."""
    out = {}
    for row in rows:
        lease = out.setdefault(row['id'], dict(row, held=None, end=None, ended=None))
        when = parse_time(row['at'])
        if row.get('event') == 'take':
            lease['held'] = when
        elif row.get('event') == 'end':
            lease['end'] = when
            lease['ended'] = row.get('ended')
            if lease['held'] is None:
                lease.update({k: v for k, v in row.items() if k not in ('event', 'at')})
    for lease in out.values():
        lease['start_t'] = parse_time(lease.get('start')) or lease['held'] or lease['end']
    return list(out.values())


def _stages(lease):
    """[(stage, begin, end)] in the order the lease waited for them."""
    begin, out = lease['start_t'], []
    for stage in STAGES.get(lease.get('pool'), ()):
        secs = float((lease.get('wait') or {}).get(stage) or 0)
        out.append((stage, begin, begin + secs))
        begin += secs
    return out


def _units(lease):
    """The lanes, slots and lock a lease held, as (resource, unit) pairs."""
    got = lease.get('got') or {}
    if lease.get('pool') == 'lane':
        return [('lane', 'lane:%s' % n) for n in got.get('lanes') or []]
    units = [('runtime', 'slot:%s' % n) for n in got.get('slots') or []]
    if lease.get('checkoutLock'):
        units.append(('checkout', 'lock:%s' % lease['checkoutLock']))
    return units


def _holds(all_leases, until):
    """{resource: [(lease, unit, begin, end, open)]}: what each lease held, open ones closed at the next taker."""
    takes = {}
    for lease in all_leases:
        for _, unit in _units(lease):
            begin = _hold_begin(lease, unit)
            if begin is not None:
                takes.setdefault(unit, []).append(begin)
    holds = {}
    for lease in all_leases:
        for resource, unit in _units(lease):
            begin = _hold_begin(lease, unit)
            if begin is None:
                continue
            end, open_ = lease['end'], lease['end'] is None
            if open_:
                later = [t for t in takes[unit] if t > begin]
                end = min(later) if later else until
            holds.setdefault(resource, []).append((lease, unit, begin, end, open_))
    return holds


def _hold_begin(lease, unit):
    if unit.startswith('lock:'):
        stages = _stages(lease)
        return stages[0][2] if stages and (lease.get('ask') or {}).get('checkout') else None
    return lease['held']


def attribute(lane_rows, runtime_rows, since, until, min_wait=5.0):
    """Each wait stage of at least `min_wait` s overlapping [since, until], with the leases holding what it waited for."""
    all_leases = leases(lane_rows) + leases(runtime_rows)
    holds = _holds(all_leases, until)
    queued = [(l, s[1], s[2]) for l in all_leases if l.get('pool') == 'lane' and (l.get('ask') or {}).get('lanes') == 'all'
              for s in _stages(l)]
    result = []
    for lease in all_leases:
        for stage, begin, end in _stages(lease):
            if stage == 'lane' and lease.get('pool') != 'lane':
                continue
            if end - begin < min_wait or end <= since or begin >= until:
                continue
            per = {}
            for holder, unit, h0, h1, open_ in holds.get(stage, ()):
                if holder is lease or (stage == 'checkout' and unit != 'lock:%s' % lease.get('checkoutLock')):
                    continue
                _add(per, holder, h0, h1, begin, end, open_)
            if stage == 'lane':
                for holder, q0, q1 in queued:
                    if holder is not lease:
                        _add(per, holder, q0, q1, begin, end, False)
            covered = _length([span for h in per.values() for span in h['spans']])
            holders = sorted(per.values(), key=lambda h: -h['overlap'])
            result.append({'id': lease['id'], 'stage': stage, 'begin': begin, 'end': end, 'wait': end - begin,
                           'label': lease.get('label'), 'pid': lease.get('pid'), 'checkout': lease.get('checkout'),
                           'ask': lease.get('ask'), 'ended': lease.get('ended'), 'holders': holders,
                           'unheld': round(end - begin - covered, 1)})
    return sorted(result, key=lambda w: w['begin'])


def _add(per, holder, h0, h1, begin, end, open_):
    lo, hi = max(h0, begin), min(h1, end)
    if hi <= lo:
        return
    h = per.setdefault(holder['id'], {'id': holder['id'], 'label': holder.get('label'), 'pid': holder.get('pid'),
                                       'checkout': holder.get('checkout'), 'command': holder.get('command'),
                                       'overlap': 0.0, 'open': False, 'spans': []})
    h['spans'].append((lo, hi))
    h['overlap'] = round(_length(h['spans']), 1)
    h['open'] = h['open'] or open_


def _length(spans):
    """Seconds the spans cover, overlaps counted once."""
    total, reach = 0.0, None
    for a, b in sorted(spans):
        if reach is None or a > reach:
            total, reach = total + b - a, b
        elif b > reach:
            total, reach = total + b - reach, b
    return total


def _iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _name(h):
    label = h.get('label') or h.get('command') or '-'
    return '%s pid=%s%s' % (str(label)[:80], h.get('pid'), ' checkout=' + os.path.basename(str(h['checkout']).rstrip('/\\'))
                            if h.get('checkout') else '')


def _by_holder(holders):
    """[(name, seconds their spans cover, leases, any open)], one per holder name: a battery's leases share its name."""
    groups = {}
    for h in holders:
        g = groups.setdefault(_name(h), {'spans': [], 'leases': 0, 'open': False})
        g['spans'] += h['spans']
        g['leases'] += 1
        g['open'] = g['open'] or h['open']
    return sorted(((name, _length(g['spans']), g['leases'], g['open']) for name, g in groups.items()), key=lambda t: -t[1])


def render(waits, bad):
    lines, totals = [], {}
    for w in waits:
        lines.append('WAIT %s %s %.0fs %s ask=%s ended=%s' % (_iso(w['begin']), w['stage'], w['wait'], _name(w),
                                                            json.dumps(w['ask'], separators=(',', ':')), w['ended'] or 'held'))
        held = _by_holder(w['holders'])
        for name, secs, leases, open_ in held[:5]:
            lines.append('  HELD_BY %s overlap=%.0fs leases=%d%s' % (name, secs, leases, ' (no end row)' if open_ else ''))
        for name, secs, _, _ in held:
            totals[name] = totals.get(name, 0.0) + secs
        if len(held) > 5:
            lines.append('  +%d more holders' % (len(held) - 5))
        if w['unheld']:
            lines.append('  UNHELD %.0fs (no recorded lease held it)' % w['unheld'])
    lines.append('SUMMARY waits=%d wait_s=%.0f unheld_s=%.0f' % (len(waits), sum(w['wait'] for w in waits), sum(w['unheld'] for w in waits)))
    for key, secs in sorted(totals.items(), key=lambda kv: -kv[1])[:10]:
        lines.append('BY_HOLDER %s held_s=%.0f' % (key, secs))
    lines.append('UNPARSEABLE %d' % bad)
    return '\n'.join(lines)


def default_paths():
    import machine_slots
    return (os.path.join(machine_slots.slot_dir(), 'leases.jsonl'),
            os.path.join(tempfile.gettempdir(), 'harness-activity', 'runtime-slots', 'leases.jsonl'))


def main(argv=None):
    from cli_usage import usage
    parser = argparse.ArgumentParser(description=usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mode', choices=('waits',))
    parser.add_argument('--since', default='6h')
    parser.add_argument('--until')
    parser.add_argument('--min-wait', type=float, default=5.0)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--lanes')
    parser.add_argument('--runtime')
    args = parser.parse_args(argv)
    since, until = parse_time(args.since), parse_time(args.until) if args.until else time.time()
    if since is None or until is None:
        parser.error('--since and --until take an ISO time or a span such as 6h')
    lanes_path, runtime_path = default_paths() if not (args.lanes and args.runtime) else (None, None)
    lane_rows, lane_bad = read(args.lanes or lanes_path)
    runtime_rows, runtime_bad = read(args.runtime or runtime_path)
    waits = attribute(lane_rows, runtime_rows, since, until, args.min_wait)
    if args.json:
        print(json.dumps({'waits': waits, 'unparseable': lane_bad + runtime_bad}, indent=1))
    else:
        print(render(waits, lane_bad + runtime_bad))
    return 0


if __name__ == '__main__':
    sys.exit(main())
