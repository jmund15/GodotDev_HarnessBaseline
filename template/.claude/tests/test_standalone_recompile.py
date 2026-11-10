"""A3: planted checkout/cache proves standalone editor-pass reuse, without Godot."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / '.claude/scripts/RuntimeSlots.ps1'
SUITE = ROOT / '.claude/scripts/run_test_suite.ps1'


def fork_marker():
    """The runner fork marker the project's `*.pin.ps1` module declares; '' when it declares none."""
    code = (". '" + str(LIB).replace("'", "''") + "'; "
            "if (Get-Command Get-GdUnitForkMarker -ErrorAction SilentlyContinue) { Get-GdUnitForkMarker }")
    result = subprocess.run(['pwsh', '-NoProfile', '-Command', code], capture_output=True, text=True,
                            encoding='utf-8', timeout=60)
    return result.stdout.strip() if result.returncode == 0 else ''


FORK = fork_marker()
if not FORK:
    # Every decision below turns on the project's skip-support pin, so without one this checkout carries
    # no subject for the proof.
    print('SKIP standalone recompile proof: the project declares no gdUnit4 fork pin')
    print('0 failure(s)')
    sys.exit(0)

SCRIPT = r'''
$ErrorActionPreference = 'Stop'
. $env:PROOF_LIB
$r = $env:PROOF_ROOT
function Need($ok, $name) {
    if (-not $ok) { throw "FAIL $name" }
    Write-Host "ok   $name"
}
function Put($path, $text) {
    $p = Join-Path $r $path
    New-Item -ItemType Directory -Force (Split-Path $p) | Out-Null
    [IO.File]::WriteAllText($p, $text)
}
function Decision { Get-RecompileDecision -Repo $r }
function Stamp {
    $before = Get-RecompileSnapshot -Repo $r
    Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'pass.trx')
}
Put '{{PROJECT_NAME}}.csproj' '<Project><ItemGroup><PackageReference Include="gdUnit4.api" Version="5.1.0-rc4@FORK@.2" /></ItemGroup></Project>'
Put 'Node.cs' "using Godot;`nnamespace Game;`n[GlobalClass]`npublic partial class Node : Resource { public int X => 1; }"
Put 'Plain.cs' 'public class Plain { public int X => 1; }'
Put 'world.tscn' '[gd_scene format=3]'
Put 'item.tres' '[gd_resource format=3]'
Put 'image.png.import' '[remap]'
Put 'actor.gd' "class_name Actor`nextends Node"
Put 'Jmodot/Core/Part.cs' 'public class Part {}'
Put 'pass.trx' '<TestRun><ResultSummary><Counters total="1" executed="1" /><Output><StdOut>Rebuilding Godot Project ... Rebuilding Godot Project ends with exit code: 0</StdOut></Output></ResultSummary></TestRun>'
$stampPath = Join-Path $r '.godot/gdunit4-recompiled.json'
Need (-not (Decision).Skip -and (Decision).Reason -eq 'stamp-missing') 'missing stamp runs'
Stamp
Need ((Decision).Skip -and (Decision).Reason -eq 'unchanged') 'unchanged checkout skips'
Need (Test-Path $stampPath) 'completed pass persists inside checkout cache'
Put 'Plain.cs' 'public class Plain { public int X => 2; }'
Need ((Decision).Skip) 'ordinary C# body edit skips'
Put 'Node.cs' "using Godot;`nnamespace Game;`n[GlobalClass]`npublic partial class Node : Resource { public int X => 2; }"
Need ((Decision).Skip) 'GlobalClass body edit skips'
Put 'actor.gd' "class_name Actor`nextends Node`nfunc act(): pass"
Need ((Decision).Skip) 'GDScript body edit skips'
Put '.godot/generated.cs' '[GlobalClass] public class Generated {}'
Put '.claude/worktrees/peer/Peer.cs' 'public class Peer {}'
Need ((Decision).Skip) 'generated cache and nested harness worktrees do not invalidate'
Put 'Added.cs' 'public class Added {}'
Need (-not (Decision).Skip -and (Decision).Reason -eq 'cs-paths-changed') 'C# add runs'
Stamp
Remove-Item (Join-Path $r 'Added.cs')
Need (-not (Decision).Skip) 'C# remove runs'
Stamp
Rename-Item (Join-Path $r 'Plain.cs') 'Renamed.cs'
Need (-not (Decision).Skip) 'C# rename runs'
Stamp
Put 'Jmodot/Core/New.cs' 'public class New {}'
Need (-not (Decision).Skip) 'submodule C# add runs'
Stamp
Put 'Node.cs' 'using Godot; namespace Game; public partial class Node : Resource {}'
Need (-not (Decision).Skip -and (Decision).Reason -eq 'globalclass-declarations-changed') 'GlobalClass removal runs'
Stamp
Put 'Node.cs' 'using Godot; namespace Game; [GlobalClass] public partial class Node : Resource {}'
Need (-not (Decision).Skip) 'GlobalClass addition runs'
Stamp
Put 'Node.cs' 'using Godot; namespace Game; [GlobalClass] public partial class RenamedNode : Resource {}'
Need (-not (Decision).Skip) 'GlobalClass class rename runs'
Stamp
Put 'Node.cs' 'using Godot; namespace Other; [GlobalClass] public partial class RenamedNode : Resource {}'
Need (-not (Decision).Skip) 'GlobalClass namespace edit runs'
Stamp
Put 'actor.gd' "class_name OtherActor`nextends Node"
Need (-not (Decision).Skip -and (Decision).Reason -eq 'class-name-declarations-changed') 'class_name edit runs'
Stamp
foreach ($path in @('world.tscn', 'item.tres', 'image.png.import')) {
    Put $path "changed $path"
    Need (-not (Decision).Skip -and (Decision).Reason -eq 'resources-changed') "$path content edit runs"
    Stamp
}
Put 'new.tres' '[gd_resource format=3]'
Need (-not (Decision).Skip) 'resource add runs'
Stamp
Remove-Item (Join-Path $r 'new.tres')
Need (-not (Decision).Skip) 'resource remove runs'
Stamp
Need (-not (Get-RecompileDecision -Repo $r -Force).Skip -and (Get-RecompileDecision -Repo $r -Force).Reason -eq 'forced') 'force switch runs'
Put '{{PROJECT_NAME}}.csproj' '<Project><ItemGroup><PackageReference Include="gdUnit4.api" Version="5.1.0-rc4@FORK@.1" /></ItemGroup></Project>'
Need (-not (Decision).Skip -and (Decision).Reason -eq 'pin-without-skip-support') '@FORK@.1 always runs'
Put '{{PROJECT_NAME}}.csproj' '<Project><ItemGroup><PackageReference Include="gdUnit4.api" Version="5.1.0-rc4@FORK@.2" /></ItemGroup></Project>'
$valid = [IO.File]::ReadAllText($stampPath)
$foreign = $valid | ConvertFrom-Json
$foreign.checkout = Join-Path $r 'another-checkout'
$foreign | ConvertTo-Json -Depth 5 | Set-Content $stampPath
Need (-not (Decision).Skip -and (Decision).Reason -eq 'stamp-foreign') 'foreign checkout stamp runs'
[IO.File]::WriteAllText($stampPath, 'not json')
Need (-not (Decision).Skip -and (Decision).Reason -eq 'stamp-unreadable') 'unreadable stamp runs'
[IO.File]::WriteAllText($stampPath, '{"schema":1}')
Need (-not (Decision).Skip) 'incomplete stamp runs'
[IO.File]::WriteAllText($stampPath, $valid)
$before = Get-RecompileSnapshot -Repo $r
Remove-Item $stampPath
Put 'Plain.cs' 'public class NewlyAdded {}'
Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'pass.trx')
Need (-not (Test-Path $stampPath)) 'source mutation during pass cannot bless post-pass inputs'
Remove-Item (Join-Path $r 'Plain.cs')
$before = Get-RecompileSnapshot -Repo $r
Put 'failed.trx' '<TestRun><ResultSummary><Counters total="1" executed="1" /><Output><StdOut>Rebuilding Godot Project fails with: error</StdOut></Output></ResultSummary></TestRun>'
Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'failed.trx')
Need (-not (Test-Path $stampPath)) 'failed editor pass never stamps'
Put 'incomplete.trx' '<TestRun><ResultSummary><Counters total="1" executed="1" /><Output><StdOut>Rebuilding Godot Project ...</StdOut></Output></ResultSummary></TestRun>'
Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'incomplete.trx')
Need (-not (Test-Path $stampPath)) 'started pass without successful exit evidence never stamps'
Put 'exit-red.trx' '<TestRun><ResultSummary><Counters total="1" executed="1" /><Output><StdOut>Rebuilding Godot Project ends with exit code: 1</StdOut></Output></ResultSummary></TestRun>'
Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'exit-red.trx')
Need (-not (Test-Path $stampPath)) 'nonzero editor exit never stamps'
Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'absent.trx')
Need (-not (Test-Path $stampPath)) 'missing completion evidence never stamps'
Put 'skipped.trx' '<TestRun><ResultSummary><Counters total="1" executed="1" /><Output><StdOut>Skipping Godot project recompile (GDUNIT4_SKIP_RECOMPILE=1)</StdOut></Output></ResultSummary></TestRun>'
Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'skipped.trx')
Need (-not (Test-Path $stampPath)) 'skipped pass never refreshes stamp'
Put 'zero.trx' '<TestRun><ResultSummary><Counters total="0" executed="0" /><Output><StdOut>Rebuilding Godot Project ... Rebuilding Godot Project ends with exit code: 0</StdOut></Output></ResultSummary></TestRun>'
Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'zero.trx')
Need (-not (Test-Path $stampPath)) 'zero-test run does not prove pass completion'
Put 'red.trx' '<TestRun><ResultSummary outcome="Failed"><Counters total="1" executed="1" failed="1" /><Output><StdOut>Rebuilding Godot Project ... Rebuilding Godot Project ends with exit code: 0</StdOut></Output></ResultSummary></TestRun>'
Set-RecompileStamp -Repo $r -Snapshot $before -Trx (Join-Path $r 'red.trx')
Need ((Decision).Skip) 'completed pass with failed tests still stamps'
$run = Resolve-RunRecompile -Repo $r
Need ($run.Skip -and $run.Line -ceq 'RECOMPILE=SKIP reason=unchanged') 'standalone prints one skip decision'
$run = Resolve-RunRecompile -Repo $r -Force
Need (-not $run.Skip -and $run.Line -ceq 'RECOMPILE=RUN reason=forced') 'standalone force switch prints run decision'
$run = Resolve-RunRecompile -Repo $r -Held -RecompilePass
Need (-not $run.Skip -and $run.Line -ceq 'RECOMPILE=RUN reason=gate-sanity') 'gate Sanity keeps its editor pass regardless of cache'
Need ($null -ne $run.Decision -and $null -ne $run.Decision.Snapshot) 'gate Sanity captures inputs for persistent stamp'
$run = Resolve-RunRecompile -Repo $r -Held -Force -HeldRecompiled
Need (-not $run.Skip -and $run.Line -ceq 'RECOMPILE=RUN reason=forced') 'forced held sibling runs its editor pass'
$run = Resolve-RunRecompile -Repo $r -Held -HeldRecompiled
Need ($run.Skip -and $run.Line -ceq 'RECOMPILE=SKIP reason=held-recompiled' -and $null -eq $run.Decision) 'held gate siblings keep their existing skip'
$run = Resolve-RunRecompile -Repo $r -Held
Need (-not $run.Skip -and $run.Line -ceq 'RECOMPILE=RUN reason=held-recompiled') 'held sibling with no recompiled record runs'
$pass = Resolve-RunRecompile -Repo $r -Held -RecompilePass
Remove-Item $stampPath
Save-RecompilePassStamp -Repo $r -Decision $pass.Decision -SkipRecompile:$pass.Skip -SilentSkip:$false -Trx (Join-Path $r 'pass.trx')
Need (Test-Path $stampPath) 'completed gate Sanity refreshes persistent checkout stamp'
Remove-Item $stampPath
Save-RecompilePassStamp -Repo $r -Decision $pass.Decision -SkipRecompile:$false -SilentSkip:$true -Trx (Join-Path $r 'pass.trx')
Need (-not (Test-Path $stampPath)) 'a silently skipped pass never stamps'
Save-RecompilePassStamp -Repo $r -Decision $pass.Decision -SkipRecompile:$true -SilentSkip:$false -Trx (Join-Path $r 'pass.trx')
Need (-not (Test-Path $stampPath)) 'a recompile-skipped pass never stamps'
Save-RecompilePassStamp -Repo $r -Decision $null -SkipRecompile:$false -SilentSkip:$false -Trx (Join-Path $r 'pass.trx')
Need (-not (Test-Path $stampPath)) 'no captured decision never stamps'
Set-RecompileStamp -Repo $r -Snapshot (Get-RecompileSnapshot -Repo $r) -Trx (Join-Path $r 'pass.trx')
$params = [Management.Automation.Language.Parser]::ParseFile($env:PROOF_SUITE, [ref]$null, [ref]$null).ParamBlock.Parameters.Name.VariablePath.UserPath
Need ($params -contains 'ForceRecompile') 'public force switch exists'
$source = [IO.File]::ReadAllText($env:PROOF_SUITE)
Need ($source.IndexOf('Resolve-RunRecompile') -gt $source.IndexOf('if (-not $lease.Acquired)')) 'decision is after successful lock acquisition'
Need ($source.Contains('"trx;LogFileName=$safe.trx"')) 'untagged run has its own completion TRX'
Need ($null -ne (Get-Command Test-GdUnitRecompileSkipSupport -ErrorAction SilentlyContinue)) 'gate and standalone share one gdUnit4 pin check'
foreach ($version in @('5.1.0-rc4@FORK@.1', '5.1.0-rc4@FORK@.2', '5.1.0-rc4@FORK@.12', '5.1.0', '5.1.0@FORK@.bad')) {
    Put '{{PROJECT_NAME}}.csproj' "<Project><PackageReference Include='gdUnit4.api' Version='$version' /></Project>"
    $expected = $version -in @('5.1.0-rc4@FORK@.2', '5.1.0-rc4@FORK@.12')
    Need ((Test-GdUnitRecompileSkipSupport -Repo $r) -eq $expected) "shared pin check: $version"
}
Put '{{PROJECT_NAME}}.csproj' '<Project />'
Need (-not (Test-GdUnitRecompileSkipSupport -Repo $r)) 'missing pin is unsupported'
Put '{{PROJECT_NAME}}.csproj' 'invalid XML'
Need (-not (Test-GdUnitRecompileSkipSupport -Repo $r)) 'unreadable pin is unsupported'
Remove-Item (Join-Path $r '{{PROJECT_NAME}}.csproj')
Need (-not (Test-GdUnitRecompileSkipSupport -Repo $r)) 'absent project is unsupported'
Need ((Get-Command Assert-GateTargetCompatible).Definition.Contains('Test-GdUnitRecompileSkipSupport')) 'gate uses shared pin check'
Need ((Get-Command Get-RecompileDecision).Definition.Contains('Test-GdUnitRecompileSkipSupport')) 'standalone uses shared pin check'
$cachePath = Join-Path $r '.godot/recompile_snapshot_cache.json'
function Key($snapshot) { $snapshot | ConvertTo-Json -Depth 8 -Compress }
Put 'item.tres' 'version-A'
$beforeA = Key (Get-RecompileSnapshot -Repo $r)
[IO.File]::WriteAllText((Join-Path $r 'item.tres'), 'version-B')
[IO.File]::SetLastWriteTimeUtc((Join-Path $r 'item.tres'), [DateTime]::UtcNow.AddMinutes(1))
$warmA = Key (Get-RecompileSnapshot -Repo $r)
Remove-Item $cachePath -ErrorAction SilentlyContinue
$coldA = Key (Get-RecompileSnapshot -Repo $r)
Need ($warmA -ceq $coldA -and $warmA -cne $beforeA) 'same-length resource edit is seen through the cache'
$nodeFile = Join-Path $r 'Node.cs'
[IO.File]::WriteAllText($nodeFile, "using Godot;`nnamespace Other;`n[GlobalClass]`npublic partial class RenamedNode : Resource {}")
$beforeG = Key (Get-RecompileSnapshot -Repo $r)
[IO.File]::WriteAllText($nodeFile, "using Godot;`nnamespace Other;`n[GlobalClass]`npublic partial class RenamedNodX : Resource {}")
[IO.File]::SetLastWriteTimeUtc($nodeFile, [DateTime]::UtcNow.AddMinutes(2))
$warmG = Key (Get-RecompileSnapshot -Repo $r)
Remove-Item $cachePath -ErrorAction SilentlyContinue
$coldG = Key (Get-RecompileSnapshot -Repo $r)
Need ($warmG -ceq $coldG -and $warmG -cne $beforeG) 'same-length GlobalClass rename is seen through the cache'
$good = Key (Get-RecompileSnapshot -Repo $r)
foreach ($junk in @('not json', '[1,2]', '{"files":5}', '{"schema":1,"files":{"Node.cs":{"length":"x","ticks":1,"entry":5}}}')) {
    [IO.File]::WriteAllText($cachePath, $junk)
    Need ((Key (Get-RecompileSnapshot -Repo $r)) -ceq $good) "corrupt cache ignored: $junk"
}
Remove-Item $cachePath -ErrorAction SilentlyContinue
$script:RecompileSnapshotReads = 0
$null = Get-RecompileSnapshot -Repo $r
$coldReads = $script:RecompileSnapshotReads
Need (Test-Path $cachePath) 'snapshot writes its cache under .godot'
$script:RecompileSnapshotReads = 0
$warmSnapshot = Key (Get-RecompileSnapshot -Repo $r)
Need ($coldReads -gt 0) 'read counter observes cold reads'
Need ($script:RecompileSnapshotReads -eq 0 -and $warmSnapshot -ceq $good) 'warm call with nothing changed reads no source file'
Write-Host 'RECOMPILE_PROOF=PASS'
'''

with tempfile.TemporaryDirectory(prefix='standalone_recompile_') as temp:
    script = Path(temp) / 'proof.ps1'
    script.write_text(SCRIPT.replace('@FORK@', FORK), encoding='utf-8', newline='\n')
    env = dict(os.environ, PROOF_LIB=str(LIB), PROOF_SUITE=str(SUITE), PROOF_ROOT=temp)
    result = subprocess.run(['pwsh', '-NoProfile', '-File', str(script)], env=env,
                            capture_output=True, text=True, timeout=90)
    print(result.stdout, end='')
    if result.returncode or 'RECOMPILE_PROOF=PASS' not in result.stdout:
        print('FAIL standalone recompile proof: ' + result.stderr[:1600])
        sys.exit(1)
print('0 failure(s)')
