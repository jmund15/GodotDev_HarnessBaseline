"""A1 proof: target checkout resolution, branch map/pins, and target propagation. No engine."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / '.claude/scripts'
failures = []
os.environ['HARNESS_WIDE_RUN_ADMITTED'] = '1'  # fixture runs start no test; a delegate running this proof must not meet WideRunGuard.ps1


def fork_marker():
    """The runner fork marker the project's `*.pin.ps1` module declares; '' when it declares none."""
    code = (". '" + str(SCRIPTS / 'RuntimeSlots.ps1').replace("'", "''") + "'; "
            "if (Get-Command Get-GdUnitForkMarker -ErrorAction SilentlyContinue) { Get-GdUnitForkMarker }")
    result = subprocess.run(['pwsh', '-NoProfile', '-Command', code], capture_output=True, text=True,
                            encoding='utf-8', timeout=60)
    return result.stdout.strip() if result.returncode == 0 else ''


FORK = fork_marker()
LOW_PIN = ('5.1.0-rc4%s.1' % FORK) if FORK else '5.1.0'
OK_PIN = ('5.1.0-rc4%s.2' % FORK) if FORK else '5.1.0'


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' — ' + detail[:400]) if not ok else ''))
    if not ok:
        failures.append(name)


def ps(code, cwd=None):
    return subprocess.run(['pwsh', '-NoProfile', '-Command', code], cwd=cwd or ROOT,
                          capture_output=True, text=True, encoding='utf-8', timeout=30)


def q(path):
    return "'" + str(path).replace("'", "''") + "'"


with tempfile.TemporaryDirectory(prefix='target_tools_') as temp:
    target = Path(temp) / 'old branch'
    (target / '.claude/scripts').mkdir(parents=True)
    (target / 'Tests/Logic/BranchOnly').mkdir(parents=True)
    (target / 'Tests/Logic/BranchOnly/Proof.cs').write_text('namespace Tests.Logic.BranchOnly;', encoding='utf-8')
    (target / '.claude/scripts/gate_domain_map.json').write_text(json.dumps({
        'domains': {'BranchOnly': {'logic_partition': 'BranchOnly', 'integration_segments': []}}, 'merges': {}}))
    (target / '{{PROJECT_NAME}}.csproj').write_text('<Project><ItemGroup><PackageReference Include="gdUnit4.api" '
                                               'Version="%s" /></ItemGroup></Project>' % LOW_PIN)
    (target / 'project.godot').write_text('[application]\nconfig/name="Planted"\n')
    subprocess.run(['git', 'init', '-q', str(target)], check=True)
    lib = '. ' + q(SCRIPTS / 'GodotProcess.ps1') + '; '
    r = ps(lib + 'Resolve-TestRepo -Repo ' + q(target) + ' -ScriptsRoot ' + q(SCRIPTS))
    check('explicit -Repo resolves a spaced target', r.returncode == 0 and Path(r.stdout.strip()) == target, r.stderr)
    r = ps(lib + 'Resolve-TestRepo -ScriptsRoot ' + q(SCRIPTS), cwd=target / 'Tests')
    check('default resolves cwd git toplevel, not main', r.returncode == 0 and Path(r.stdout.strip()) == target, r.stderr)
    foreign = Path(temp) / 'non-godot'
    foreign.mkdir()
    subprocess.run(['git', 'init', '-q', str(foreign)], check=True)
    r = ps(lib + 'Resolve-TestRepo -ScriptsRoot ' + q(SCRIPTS), cwd=foreign)
    check('non-Godot git cwd falls back to the script checkout',
          r.returncode == 0 and Path(r.stdout.strip()) == ROOT, r.stdout + r.stderr)
    r = ps(lib + 'Resolve-TestRepo -ScriptsRoot ' + q(SCRIPTS), cwd=temp)
    check('non-git cwd falls back to the script checkout', r.returncode == 0 and Path(r.stdout.strip()) == ROOT, r.stderr)
    r = subprocess.run(['pwsh', '-NoProfile', '-File', str(SCRIPTS / 'verify.ps1'), '-Repo', str(target), '-SelfTest'],
                       capture_output=True, text=True, timeout=30)
    check('verify reads the target map and target test folders', r.returncode == 0 and "domain 'BranchOnly'" in r.stdout,
          r.stdout + r.stderr)
    if FORK:
        r = subprocess.run(['pwsh', '-NoProfile', '-File', str(SCRIPTS / 'regression_gate.ps1'), '-Repo', str(target)],
                           capture_output=True, text=True, timeout=30)
        check('gate refuses the low pin before locks/build with merge main first',
              r.returncode == 4 and 'merge main first' in r.stdout, r.stdout + r.stderr)
    else:
        print('ok   (skipped) the gate pin floor: no fork marker is declared in this project')
    routes = {'run_test_suite': ['-IgnoreEditor', '-Filter', 'X'], 'verify': [], 'regression_gate': ['-IgnoreEditor'], 'run_integration_batched': []}
    for name, extra in routes.items():
        r = ps('$a=[System.Management.Automation.Language.Parser]::ParseFile(' + q(SCRIPTS / (name + '.ps1')) +
               ',[ref]$null,[ref]$null); $a.ParamBlock.Parameters.Name.VariablePath.UserPath -join ","')
        check(name + ' admits -Repo', 'Repo' in r.stdout.strip().split(','), r.stdout + r.stderr)
    fork_target = Path(temp) / 'fork target'
    shutil.copytree(target, fork_target, ignore=shutil.ignore_patterns('.git'))
    subprocess.run(['git', 'init', '-q', str(fork_target)], check=True)
    (fork_target / '{{PROJECT_NAME}}.csproj').write_text('<Project><PackageReference Include="gdUnit4.api" Version="%s" /></Project>' % OK_PIN)
    for name, extra in routes.items():
        for label, repo_dir in (('low', target), ('fork', fork_target)):
            if name == 'regression_gate' and label == 'low':
                continue
            r = subprocess.run(['pwsh', '-NoProfile', '-File', str(SCRIPTS / (name + '.ps1')), '-Repo', str(repo_dir), '-RouteOnly'] + extra,
                               capture_output=True, text=True, timeout=60)
            got = [l[6:].strip() for l in r.stdout.splitlines() if l.startswith('ROUTE=')]
            check(name + ' routes a ' + label + ' -Repo target from the script root, not main',
                  r.returncode == 0 and len(got) == 1 and Path(got[0]) == repo_dir, r.stdout + r.stderr)
    r = ps(lib + '. ' + q(SCRIPTS / 'RunPool.ps1') + '; '
           '$u = [pscustomobject]@{ label = "L"; filter = "F"; timeoutMs = 5 }; '
           '(Get-SuiteChildArgv -Unit $u -ResultsRoot "R" -Held) -join "|"', cwd=target)
    argv = r.stdout.strip().split('|')
    check('pool forwards its target to main wrapper', '-Repo' in argv and argv[argv.index('-Repo') + 1] == str(target), r.stdout + r.stderr)
    r = ps(lib + '(Get-RunnerArgv -Script "x.ps1" -Repo ' + q(target) + ' -ScriptArgs @("-Label","L")) -join "|"')
    argv = r.stdout.strip().split('|')
    check('verify forwards target to both children', argv[:5] == ['-NoProfile', '-File', 'x.ps1', '-Repo', str(target)] and argv[5:] == ['-Label', 'L'],
          r.stdout + r.stderr)
    r = ps(lib + '(Get-GateArgs -Repo ' + q(target) + ' -StaticOnly -NoReuse) -join "|"')
    check('detached gate forwards target', r.stdout.strip() == '-Repo|' + str(target) + '|-StaticOnly|-NoReuse', r.stdout + r.stderr)
    # Main's fixed-root static guards must inspect target content, not silently inspect main again.
    guard = Path(temp) / 'main/.claude/hooks/planted_guard.py'
    guard.parent.mkdir(parents=True)
    guard.write_text('import sys\nfrom pathlib import Path\n'
                     'REPO=Path(sys.argv[sys.argv.index("--repo")+1]) if "--repo" in sys.argv else Path(__file__).resolve().parents[2]\nTESTS_ROOT=REPO/"Tests"\n'
                     'def main():\n print("GUARD_REPO="+str(REPO)); print("GUARD_TESTS="+str(TESTS_ROOT)); return 0\n')
    runner_file = Path(temp) / 'guard_runner.py'
    r = ps('. ' + q(SCRIPTS / 'StaticGuards.ps1') + '; [IO.File]::WriteAllText(' + q(runner_file) + ', $script:GuardRunner)')
    runner = r.returncode == 0 and runner_file.exists()
    if runner:
        r = subprocess.run([sys.executable, '-c', runner_file.read_text(), str(guard), str(target)],
                           cwd=target, capture_output=True, text=True, timeout=30)
        check('main fixed-root guard runs against target files', r.returncode == 0 and
              'GUARD_REPO=' + str(target) in r.stdout and 'GUARD_TESTS=' + str(target / 'Tests') in r.stdout,
              r.stdout + r.stderr)
    else:
        check('main fixed-root guard runs against target files', False, 'fixed-root guard uses script checkout')
    (target / '{{PROJECT_NAME}}.csproj').write_text('<Project><PackageReference Include="gdUnit4.api" '
                                               'Version="%s" /></Project>' % OK_PIN)
    r = ps(lib + 'Assert-GateTargetCompatible -Repo ' + q(target))
    check('the fork floor pin meets the held-gate floor', r.returncode == 0, r.stderr)
    def locked(logon):
        shadow = ("function Get-Process { param($Id, $Name, $ErrorAction) if ($Name -eq 'LogonUI') { "
                  + logon + " } else { [pscustomobject]@{ SessionId = 1 } } }; Test-InteractiveSessionLocked")
        return ps(lib + shadow).stdout.strip()
    check('a LogonUI in the gate session reads as locked', locked("@([pscustomobject]@{ SessionId = 1 })") == 'True')
    check('a LogonUI in another session is not a lock', locked("@([pscustomobject]@{ SessionId = 2 })") == 'False')
    check('no LogonUI reads as unlocked', locked("@()") == 'False')

print(f'{len(failures)} failure(s)')
sys.exit(bool(failures))
