# Adds the Defender folder exclusions /workstation_setup names that this machine lacks, then writes
# {added, excluded: {path: bool}} (or {error}) to -Result. Run elevated: Defender lets only an
# administrator read or change its exclusions.
param(
    [Parameter(Mandatory)][string]$Result,
    [Parameter(Mandatory)][string]$Paths
)
$wanted = @($Paths -split ';' | Where-Object { $_ })
try {
    $have = @((Get-MpPreference).ExclusionPath)
    $missing = @($wanted | Where-Object { $have -notcontains $_ })
    if ($missing.Count) { Add-MpPreference -ExclusionPath $missing }
    $have = @((Get-MpPreference).ExclusionPath)
    $excluded = [ordered]@{}
    foreach ($path in $wanted) { $excluded[$path] = [bool]($have -contains $path) }
    $report = [ordered]@{ added = $missing; excluded = $excluded }
} catch {
    $report = [ordered]@{ error = "$_" }
}
$report | ConvertTo-Json -Depth 4 | Set-Content -Path $Result -Encoding utf8
