"""Required inputs need captured, successfully paired reads, not mentions or adjacent results.

Non-reads and not-found attempts are VOID; genuine reads and retries survive unrelated errors.
The captured blocks and their provenance live in test_review_result.
"""
import json
import os
import sys
import tempfile
from unittest.mock import patch

# Resolve tools/ from THIS file, not from an absolute path: a committed test pinned to one
# machine's checkout passes only there, which is the same class of defect it is testing for.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'tools'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import void_check

TMP = tempfile.mkdtemp(prefix='void_check_engagement_')

from test_review_result import captured_events, captured_mention

ROOT = os.path.join(TMP, 't1-luna-max').replace('\\', '/')
REQ = ['.claude/stress-test/T1-addon-survey.md']
BASE = 'T1-addon-survey.md'
TARGET = f'{ROOT}/{REQ[0]}'


def w(name, records):
    p = os.path.join(TMP, name)
    with open(p, 'w', encoding='utf-8') as fh:
        for r in records:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return p


def call_result(tool='Read', *, error=False, cid='s0'):
    given = {'file_path': TARGET} if tool == 'Read' else {
        'command': f"{'cat' if tool == 'Bash' else 'Get-Content'} '{TARGET}'"}
    return captured_events(tool, given, error=error, cid=cid)


def nearby_error():
    return captured_events('Read', {'file_path': f'{ROOT}/other.md'}, error=True, cid='unrelated')


CASES = []
for label, tool, given in (
        ('Write is not a read', 'Write', {'file_path': TARGET, 'content': 'x'}),
        ('Edit is not a read', 'Edit', {'file_path': TARGET, 'old_string': 'a', 'new_string': 'b'}),
        ('shell redirect is not a read', 'Bash', {'command': f"echo x > '{TARGET}'"}),
        ('printf path mention is not a read', 'Bash', {'command': f"printf '%s' '{TARGET}'"}),
        ('test -f is not a read', 'Bash', {'command': f"test -f '{TARGET}'"}),
        ('unknown shell form is not a read', 'Bash', {'command': f"unknown-reader '{TARGET}'"})):
    CASES.append((label, 'VOID', w(f'{len(CASES)}.jsonl', captured_events(tool, given)), 'no successful read'))
CASES.append(('prose-only mention', 'VOID',
              w('prose.jsonl', [captured_mention(TARGET)]), 'no parseable call record'))
CASES.append(('no parseable call record', 'VOID',
              w('flat.txt', [f'{ROOT} Read {TARGET} -> source']), 'no parseable call record'))
CASES.append(('never referenced', 'VOID',
              w('never.jsonl', [captured_mention(f'continuing in {ROOT}')]), 'no parseable call record'))
for tool in ('Read', 'Bash', 'PowerShell'):
    CASES.append((f'{tool}: every read not-found', 'VOID',
                  w(f'failed-{tool}.jsonl', call_result(tool, error=True) + [captured_mention(TARGET)]),
                  'no successful read'))
    failed = call_result(tool, error=True, cid='failed')
    ok = call_result(tool, cid='ok')
    other = nearby_error()
    # Unrelated failed result is adjacent to the successful call, not paired to it.
    for label, events in ((f'{tool}: successful read', [other[0]] + ok + [other[1]]),
                          (f'{tool}: failed then retry', failed + [other[0]] + ok + [other[1]])):
        CASES.append((label, 'VALID', w(f'{len(CASES)}.jsonl', events), 'successful reads'))
# Naming a same-basename file elsewhere must not satisfy the full required path.
CASES.append(('wrong same-basename path', 'VOID',
              w('wrong.jsonl', captured_events('Read', {'file_path': f'{ROOT}/other/{BASE}'})),
              'no successful read'))

def cli_call(tool, given, status='completed', cid='c0'):
    """One event of the free CLI seat's stream: the call, its input and its result in one `tool_use` event."""
    state = {'status': status, 'input': given}
    state['error' if status == 'error' else 'output'] = 'x' if status == 'error' else 'text'
    return {'type': 'tool_use', 'part': {'type': 'tool', 'tool': tool, 'callID': cid, 'state': state}}


# The free CLI seat names its tools in lower case and puts a Read's path under `filePath`.
CASES.append(('cli stream: successful read of the required input', 'VALID',
              w('cli-read.jsonl', [cli_call('read', {'filePath': TARGET}), captured_mention(ROOT)]), 'successful reads'))
CASES.append(('cli stream: successful bash cat of the required input', 'VALID',
              w('cli-bash.jsonl', [cli_call('bash', {'command': f"cat '{TARGET}'"}), captured_mention(ROOT)]), 'successful reads'))
CASES.append(('cli stream: the read was refused', 'VOID',
              w('cli-refused.jsonl', [cli_call('read', {'filePath': TARGET}, status='error'), captured_mention(ROOT)]),
              'no successful read'))
CASES.append(('cli stream: another file was read, not the required input', 'VOID',
              w('cli-other.jsonl', [cli_call('read', {'filePath': f'{ROOT}/other.md'}), captured_mention(ROOT)]),
              'no successful read'))

EXTERNAL = os.path.join(TMP, 'input.md').replace('\\', '/')
MSYS_EXTERNAL = '/' + EXTERNAL[0] + EXTERNAL[2:] if EXTERNAL[1:2] == ':' else EXTERNAL
for tool, given in (('Read', {'file_path': EXTERNAL}),
                    ('Bash', {'command': f"cat '{EXTERNAL}'"})):
    CASES.append((f'{tool}: external MSYS required path', 'VALID',
                  w(f'external-{tool}.jsonl', captured_events(tool, given) + [captured_mention(ROOT)]),
                  'successful reads', [MSYS_EXTERNAL]))
CASES.append(('cd && cat reads both operands', 'VALID',
              w('chain.jsonl', captured_events('Bash', {'command': f"cd '{ROOT}' && cat '{REQ[0]}' && cat other.md"})),
              'successful reads'))

CASES.append(('no required files: parsed shell call', 'VALID',
              w('no-required.jsonl', captured_events('Bash', {'command': f"cd '{ROOT}'; sed -n '1,30p' src/a.cs"})),
              'engaged its own root', []))
CASES.append(('no required files: prose is not a call', 'VOID',
              w('no-required-prose.jsonl', [captured_mention(ROOT)]), 'no parseable call record', []))

fails = 0
for case in CASES:
    label, want, path, detail = case[:4]
    got, reason = void_check.engagement(path, ROOT, required=case[4] if len(case) > 4 else REQ)
    ok = got == want and detail in reason
    fails += not ok
    print("%-38s want=%-5s got=%-5s %s  | %s"
          % (label, want, got, "OK " if ok else "FAIL", reason))

launcher = os.path.join(TMP, 'probe.sh')
count = os.path.join(TMP, 'count')
with open(launcher, 'w', encoding='utf-8', newline='\n') as fh:
    fh.write('n=$(( $(cat "$VC_COUNT" 2>/dev/null || printf 0) + 1 ))\n'
             'printf "%s" "$n" > "$VC_COUNT"\n'
             '[ "$n" = 1 ] && { echo "REFUSED memory headroom" >&2; exit 12; }\n'
             'while [ "$#" -gt 0 ]; do\n'
             '  if [ "$1" = -R ]; then shift; rec="$1"; fi\n'
             '  shift\n'
             'done\n'
             'printf \'{"exitCode":0,"servedModel":"probe-model"}\\n\' > "$rec"\n')
with patch.dict(os.environ, {'LAUNCH_MIN_COMMIT_GIB': '0', 'LAUNCH_MIN_PHYSICAL_GIB': '0',
                             'VC_COUNT': count}):
    got, reason = void_check.preflight(launcher.replace('\\', '/'), 'probe-model')
with open(count, encoding='utf-8') as fh:
    attempts = fh.read()
ok = got == 'VALID' and attempts == '2'
fails += not ok
print('memory refusal preflight retries: want=VALID/2 got=%s/%s %s | %s'
      % (got, attempts, 'OK' if ok else 'FAIL', reason))

print("\n%d/%d passed" % (len(CASES) + 1 - fails, len(CASES) + 1))
sys.exit(1 if fails else 0)
