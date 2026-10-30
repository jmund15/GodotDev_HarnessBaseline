"""A5c: a lingering test grandchild cannot hold the wrapper caller's pipe. No test engine."""
from pathlib import Path
import base64
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / '.claude/scripts/GodotProcess.ps1'
SUITE = ROOT / '.claude/scripts/run_test_suite.ps1'
failures = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' — ' + detail[:500]) if not ok else ''))
    if not ok:
        failures.append(name)


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


with tempfile.TemporaryDirectory(prefix='test_pipe_', ignore_cleanup_errors=True) as temp:
    child = Path(temp) / 'fake_native.ps1'
    encoded = base64.b64encode('Start-Sleep -Seconds 8; "grandchild done"'.encode('utf-16le')).decode()
    child.write_text('Start-Process pwsh -NoNewWindow -ArgumentList @("-NoProfile", "-EncodedCommand", "' + encoded +
                     '") | Out-Null\n"native done"\nexit 77\n', encoding='utf-8')
    out, err = Path(temp) / 'out.log', Path(temp) / 'err.log'
    cmd = '. ' + q(LIB) + '; $p=Start-IsolatedNativeProcess -FilePath pwsh -Arguments @(\'-NoProfile\', \'-File\', ' + q(child) + \
          ') -OutPath ' + q(out) + ' -ErrPath ' + q(err) + '; $null=$p.Handle; $p.WaitForExit(); "exit=$($p.ExitCode)"'
    before = time.monotonic()
    r = subprocess.run(['pwsh', '-NoProfile', '-Command', cmd], capture_output=True, text=True, timeout=30)
    elapsed = time.monotonic() - before
    check('native exit code survives the detached self-redirecting pwsh', 'exit=77' in r.stdout, r.stdout + r.stderr)
    check('caller regains its pipe while a grandchild still lives', elapsed < 6, str(elapsed))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (not out.exists() or 'grandchild done' not in out.read_text(errors='replace')):
        time.sleep(.2)
    check('the lingering grandchild wrote only to the attempt file', out.exists() and 'grandchild done' in out.read_text(errors='replace'))
    suite = SUITE.read_text()
    check('test runner redirects inside an isolated pwsh, as gate -Detach does',
          "Start-IsolatedNativeProcess -FilePath 'dotnet'" in suite and '-RedirectStandardOutput $log' not in suite)
    check('the runner caches the native root handle before waiting', '$null = $p.Handle' in suite)

print(f'{len(failures)} failure(s)')
sys.exit(bool(failures))
