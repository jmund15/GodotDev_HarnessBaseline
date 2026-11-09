"""A2 proof: planted process/log clock drives shipped watchdog without Godot or a build."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / '.claude/scripts/GodotProcess.ps1'
SUITE = ROOT / '.claude/scripts/run_test_suite.ps1'
failures = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' — ' + detail[:600]) if not ok else ''))
    if not ok:
        failures.append(name)


with tempfile.TemporaryDirectory(prefix='watchdog_') as temp:
    target = Path(temp)
    (target / 'Tests').mkdir()
    (target / 'Tests/integration_batch_durations.json').write_text('{"units":{"AI":{"seconds":100},"Art":{"seconds":1}}}')
    (target / 'Tests/regression_baseline.json').write_text('{"suites":{"Logic":{"duration_sec":200}}}')
    (target / 'Tests/Integration/AI/Steering').mkdir(parents=True)
    (target / 'Tests/Integration/AI/Steering/SeekTests.cs').write_text('')
    (target / 'Tests/Logic/Math').mkdir(parents=True)
    (target / 'Tests/Logic/Math/CurveTests.cs').write_text('')
    source = LIB.read_text()
    env = dict(os.environ, PROOF_LIB=str(LIB), PROOF_ROOT=temp)
    script = r'''
$ErrorActionPreference='Stop'
. $env:PROOF_LIB
$r=$env:PROOF_ROOT
function Need($x,$s) { if (!$x) { throw $s } }
Need ((Get-SuiteRunBounds -TimeoutMs 150000).LockWaitMs -eq 3000000) 'short run waits for the fixed-unit floor at the progress ceiling (2*480000*3+120000)'
Need ((Get-SuiteRunBounds -TimeoutMs 900000).LockWaitMs -eq 5520000) 'dynamic cap widens lock wait above the floor at the progress ceiling (2*900000*3+120000)'
$log=Join-Path $r 'godot_test.log'
'Vulkan ready' | Set-Content $log
$p=[pscustomobject]@{}
$p | Add-Member ScriptMethod WaitForExit { param($ms) Start-Sleep -Milliseconds $ms; return $false }
$res=Wait-SuiteAttempt -Process $p -TimeoutMs 3000 -GodotLog $log -BootTimeoutMs 20 -PollMs 10
Need ($res -eq 'boot-hang') 'pre-connect wedge ends before whole cap'
'GodotGdUnit4RestApi:: Waiting for client connecting' | Add-Content $log
$res=Wait-SuiteAttempt -Process $p -TimeoutMs 60 -GodotLog $log -BootTimeoutMs 20 -PollMs 10
Need ($res -eq 'cap-hang') 'connected long test ignores boot cap but keeps run cap'
$clock=[Diagnostics.Stopwatch]::StartNew()
$res=Wait-SuiteAttempt -Process $p -TimeoutMs 200 -GodotLog $log -BootTimeoutMs 20 -PollMs 10 -StallMs 100
Need ($res -eq 'stall-hang' -and $clock.ElapsedMilliseconds -lt 2000) "a connected run whose log stopped growing ends at the stall bound, not the run cap (took $($clock.ElapsedMilliseconds) ms)"
$clock=[Diagnostics.Stopwatch]::StartNew()
$res=Wait-SuiteAttempt -Process $p -TimeoutMs 3000 -GodotLog $log -BootTimeoutMs 20 -PollMs 10 -StallMs 100
Need ($res -eq 'stall-hang' -and $clock.ElapsedMilliseconds -lt 2000) "a silent runtime log ends at the stall bound while the run cap is far away (took $($clock.ElapsedMilliseconds) ms)"
$w=Join-Path $r 'wrapper.log'; 'native output' | Set-Content $w
$clock=[Diagnostics.Stopwatch]::StartNew()
$res=Wait-SuiteAttempt -Process $p -TimeoutMs 3000 -WrapperLog $w -NoGodotRuntime -BootTimeoutMs 20 -PollMs 10 -StallMs 100
Need ($res -eq 'stall-hang' -and $clock.ElapsedMilliseconds -lt 2000) "a silent -NoGodotRuntime wrapper log ends at the stall bound (took $($clock.ElapsedMilliseconds) ms)"
$absent=Join-Path $r 'absent.log'
$clock=[Diagnostics.Stopwatch]::StartNew()
$res=Wait-SuiteAttempt -Process $p -TimeoutMs 3000 -GodotLog $absent -WrapperLog $w -BootTimeoutMs 20 -PollMs 10 -StallMs 100
Need ($res -eq 'stall-hang' -and $clock.ElapsedMilliseconds -lt 2000) "before the Godot log exists, a silent wrapper log is the progress file (took $($clock.ElapsedMilliseconds) ms)"
$grow=[pscustomobject]@{ Log=$log }
$grow | Add-Member ScriptMethod WaitForExit { param($ms) Start-Sleep -Milliseconds $ms; 'progress' | Add-Content $this.Log; return $false }
$clock=[Diagnostics.Stopwatch]::StartNew()
$res=Wait-SuiteAttempt -Process $grow -TimeoutMs 200 -GodotLog $log -BootTimeoutMs 20 -PollMs 10 -StallMs 100
Need ($res -eq 'cap-hang' -and $clock.ElapsedMilliseconds -ge 550) "a run still writing its log outlives the run cap up to the progress ceiling (took $($clock.ElapsedMilliseconds) ms)"
$wg=[pscustomobject]@{ Log=$w }
$wg | Add-Member ScriptMethod WaitForExit { param($ms) Start-Sleep -Milliseconds $ms; 'build' | Add-Content $this.Log; return $false }
$res=Wait-SuiteAttempt -Process $wg -TimeoutMs 200 -WrapperLog $w -NoGodotRuntime -BootTimeoutMs 20 -PollMs 10 -StallMs 100
Need ($res -eq 'cap-hang') 'a -NoGodotRuntime run whose wrapper log keeps growing outlives the run cap up to the progress ceiling'
Remove-Item $log
$res=Wait-SuiteAttempt -Process $p -TimeoutMs 60 -GodotLog $log -BootTimeoutMs 20 -PollMs 10
Need ($res -eq 'cap-hang') 'no runtime log cannot time a runtime boot'
'Vulkan ready' | Set-Content $log
Save-HungGodotLog -GodotLog $log -Attempt 1
'other attempt' | Set-Content $log
Save-HungGodotLog -GodotLog $log -Attempt 2
Need ((Get-Content (Join-Path $r 'godot_test.hang1.log')) -eq 'Vulkan ready') 'first hang log retained'
Need ((Get-Content (Join-Path $r 'godot_test.hang2.log')) -eq 'other attempt') 'second hang log retained'
$p | Add-Member ScriptMethod WaitForExit { param($ms) return $true } -Force
Need ((Wait-SuiteAttempt -Process $p -TimeoutMs 100 -GodotLog $log -BootTimeoutMs 20) -eq 'exited') 'healthy exited attempt'
Need ((Get-SuiteDefaultCeilingMs -Repo $r) -eq 301000) 'the default ceiling sums every recorded unit and tier duration (AI 100 + Art 1 + Logic 200 s), with no filter or last-green input'
'WATCHDOG=PASS'
'''
    p = subprocess.run(['pwsh', '-NoProfile', '-Command', script], env=env, capture_output=True, text=True, timeout=60)
    homes = r'''
$ErrorActionPreference='Stop'
. $env:PROOF_LIB
$r=$env:PROOF_ROOT
function Need($x,$s) { if (!$x) { throw $s } }
$script:SuiteLockWaitFloorMs=1000
Need ((Get-SuiteRunBounds -TimeoutMs 1000).LockWaitMs -eq (2*1000*3+$script:SuiteLockWaitExtraMs)) 'one lock-wait floor governs the lock wait, scaled by the progress ceiling'
'HOMES=PASS'
'''
    h = subprocess.run(['pwsh', '-NoProfile', '-Command', homes], env=env, capture_output=True, text=True, timeout=30)
    check('the default ceiling and the lock-wait floor each have one governing home',
          h.returncode == 0 and 'HOMES=PASS' in h.stdout, h.stdout + h.stderr)
    check('watchdog, duration caps and hang evidence execute against planted runtime', p.returncode == 0 and
          'WATCHDOG=PASS' in p.stdout, p.stdout + p.stderr)
    attempt_loop = r'''
$ErrorActionPreference='Stop'
. $env:PROOF_LIB
$script:SuiteBootTimeoutMs=10
$repo=$env:PROOF_ROOT; $runDir=$repo; $godotLog=Join-Path $repo 'godot_test.log'
$log=Join-Path $repo 'native.log'; $errLog=Join-Path $repo 'native.err'
'native output' | Set-Content $log
$Label='planted'; $Filter='FullyQualifiedName~Tests.Logic.X'; $TimeoutMs=[int]$env:PROOF_TIMEOUT_MS
$HeldCheckout=$true; $ResultsRoot=$env:PROOF_RESULTS_ROOT; $NoGodotRuntime=$false
$iso=@{AppDataArg='TestRunParameters.Parameter(name="APPDATA", value="plant")'}
$noBuild=$true; $trxPath=''; $passTrx=Join-Path $repo 'plant.trx'; $safe='plant'; $lease=$null; $maxAttempts=2; $attempt=0; $global:launches=0
function Write-ActivityRecord {}
function Write-PhaseMs {}
function Clear-OwnTree {}
function Clear-RunDirGodot {}
function Get-PipeSettleMs { 0 }
function Set-RuntimeSlotRunner {}
function Stop-ProcessTree { param($ProcessId) }
function Start-IsolatedNativeProcess {
    param($FilePath, $Arguments, $OutPath, $ErrPath)
    $global:launches++
    ($Arguments -join ' ') | Add-Content (Join-Path $repo 'launch.args')
    if ($env:PROOF_LOG_MODE -ne 'none') {
        "Vulkan attempt $global:launches" | Set-Content $godotLog
        if ($env:PROOF_LOG_MODE -eq 'connected') { 'GodotGdUnit4RestApi:: Waiting for client connecting' | Add-Content $godotLog }
    }
    $p=[pscustomobject]@{Id=1234;Handle=1}
    $p | Add-Member ScriptMethod WaitForExit { param($ms) [Threading.Thread]::Sleep(1); return $false }
    $p
}
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:PROOF_SUITE,[ref]$null,[ref]$null)
$loop=$ast.Find({param($a) $a -is [System.Management.Automation.Language.WhileStatementAst] -and
    $a.Extent.Text -match 'RETRY=HANG'},$true)
. ([scriptblock]::Create($loop.Extent.Text))
'''
    env['PROOF_SUITE'] = str(SUITE)

    def run_loop(name, log_mode, timeout_ms, results_root=None, stale=False):
        root = target / name
        root.mkdir()
        if stale:
            (root / 'godot_test.log').write_text('GodotGdUnit4RestApi:: Waiting for client connecting\nstale')
        run_env = dict(env, PROOF_ROOT=str(root), PROOF_LOG_MODE=log_mode, PROOF_TIMEOUT_MS=str(timeout_ms),
                       PROOF_RESULTS_ROOT=str(root) if results_root is None else results_root)
        proc = subprocess.run(['pwsh', '-NoProfile', '-Command', attempt_loop], env=run_env,
                              capture_output=True, text=True, timeout=30)
        return root, proc

    root, p = run_loop('boot', 'plain', 10000)
    check('shipped attempt loop retries one boot wedge, then exits HANG, never green',
          p.returncode == 124 and p.stdout.count('RETRY=HANG') == 1 and 'STATUS=HANG' in p.stdout and
          'reason=boot-hang' in p.stdout and 'STATUS=DONE' not in p.stdout, p.stdout + p.stderr)
    check('shipped retry loop keeps both attempts, not the overwritten runtime log',
          (root / 'godot_test.hang1.log').read_text().strip() == 'Vulkan attempt 1' and
          (root / 'godot_test.hang2.log').read_text().strip() == 'Vulkan attempt 2')
    root, p = run_loop('cap', 'connected', 50)
    check('a connected run that outlives its cap takes the same two-attempt HANG path',
          p.returncode == 124 and p.stdout.count('RETRY=HANG') == 1 and 'STATUS=HANG' in p.stdout and
          'reason=cap-hang' in p.stdout and 'reason=boot-hang' not in p.stdout and 'STATUS=DONE' not in p.stdout,
          p.stdout + p.stderr)
    root, p = run_loop('stale', 'none', 50, stale=True)
    check('every attempt removes its stale runtime log',
          (root / 'godot_test.hang1.log').read_text().strip() == 'No runtime Godot log was created by this attempt.' and
          (root / 'godot_test.hang2.log').read_text().strip() == 'No runtime Godot log was created by this attempt.',
          p.stdout + p.stderr)
    for tagged, results_root in (('tagged', None), ('non-tagged', '')):
        root, p = run_loop('args-' + tagged, 'plain', 50, results_root=results_root)
        launches = (root / 'launch.args').read_text().splitlines()
        want = '--log-file ' + str(root / 'godot_test.log').replace('\\', '/')
        check(f'{tagged} runs pass the runtime log path to the test host',
              len(launches) == 2 and all(want in line for line in launches), p.stdout + p.stderr + str(launches))
    derived = r'''
$ErrorActionPreference='Stop'
. $env:PROOF_LIB
$repo=$env:PROOF_ROOT
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:PROOF_SUITE,[ref]$null,[ref]$null)
$cap=$ast.Find({param($a) $a -is [System.Management.Automation.Language.IfStatementAst] -and
    $a.Extent.Text -match 'ContainsKey\(.TimeoutMs.\)'},$true)
$block=[scriptblock]::Create($cap.Extent.Text.Replace('$PSBoundParameters','$bound'))
function Resolve-Cap([hashtable] $bound, [int] $given) { $TimeoutMs = $given; . $block; $TimeoutMs }
if ((Resolve-Cap @{} 0) -ne 301000) { throw 'an unbound cap is the sum of the recorded durations' }
if ((Resolve-Cap @{ TimeoutMs = 777 } 777) -ne 777) { throw 'an explicit cap stays an override' }
'CAP=PASS'
'''
    p = subprocess.run(['pwsh', '-NoProfile', '-Command', derived], env=dict(env, PROOF_SUITE=str(SUITE)),
                       capture_output=True, text=True, timeout=30)
    check('default cap is derived; explicit cap stays an override', p.returncode == 0 and 'CAP=PASS' in p.stdout,
          p.stdout + p.stderr)
    manifest = r'''
$ErrorActionPreference='Stop'
. $env:PROOF_LIB
try { $v = Get-SuiteDefaultCeilingMs -Repo $env:PROOF_ROOT; Write-Host "VALUE=$v" }
catch { Write-Host "THREW=$($_.Exception.Message)" }
'''
    for label, broken in (
        ('malformed duration manifest', 'Tests/integration_batch_durations.json'),
        ('malformed regression baseline', 'Tests/regression_baseline.json'),
    ):
        root = target / ('bad_' + broken.replace('/', '_').replace('.', '_'))
        (root / 'Tests').mkdir(parents=True)
        (root / 'Tests/integration_batch_durations.json').write_text('{"units":{"AI":{"seconds":100}}}')
        (root / 'Tests/regression_baseline.json').write_text('{"suites":{"Logic":{"duration_sec":200}}}')
        (root / broken).write_text('{"units":')
        case_env = dict(env, PROOF_ROOT=str(root))
        m = subprocess.run(['pwsh', '-NoProfile', '-Command', manifest], env=case_env, capture_output=True, text=True, timeout=30)
        check(f'{label} throws and names the file, never a silent floor',
              'THREW=' in m.stdout and broken.split('/')[-1] in m.stdout and 'VALUE=' not in m.stdout, m.stdout + m.stderr)
    cold = r'''
$ErrorActionPreference='Stop'
. $env:PROOF_LIB
. (Join-Path $env:PROOF_SCRIPTS 'RuntimeSlots.ps1')
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:PROOF_SUITE,[ref]$null,[ref]$null)
$cold=$ast.Find({param($a) $a -is [System.Management.Automation.Language.IfStatementAst] -and
    $a.Extent.Text -match 'SuiteColdRecompileExtraMs'},$true)
$block=[scriptblock]::Create($cold.Extent.Text.Replace('$PSBoundParameters','$bound'))
function Resolve-Cold([hashtable] $bound, [int] $given, [bool] $held) {
    $TimeoutMs = $given; $repo = $env:PROOF_ROOT; $HeldCheckout = $held; $ReapReport = $false
    . $block; $TimeoutMs
}
if ((Resolve-Cold @{} 240000 $false) -ne 490000) { throw 'a cold default cap carries the recompile allowance' }
if ((Resolve-Cold @{ TimeoutMs = 777 } 777 $false) -ne 777) { throw 'an explicit cap gets no recompile allowance' }
if ((Resolve-Cold @{} 240000 $true) -ne 240000) { throw 'a held child gets no allowance; its holder owns the recompile' }
'COLD=PASS'
'''
    cold_env = dict(env, PROOF_ROOT=str(target), PROOF_SCRIPTS=str(SUITE.parent), PROOF_SUITE=str(SUITE))
    c = subprocess.run(['pwsh', '-NoProfile', '-Command', cold], env=cold_env, capture_output=True, text=True, timeout=60)
    check('a cold checkout default cap carries the recompile allowance; explicit and held caps do not',
          c.returncode == 0 and 'COLD=PASS' in c.stdout, c.stdout + c.stderr)
    suite = SUITE.read_text()
    # The one-shot whole-cap wait and the retry loop's RETRY=HANG/STATUS=HANG markers are exercised above; no
    # behavior distinguishes the retired wrapper wording, so the loop's anchor text stays a pin.
    check('the attempt loop anchor the proofs extract stays in the wrapper', 'RETRY=HANG' in suite and 'STATUS=HANG' in suite)

print(f'{len(failures)} failure(s)')
sys.exit(bool(failures))
