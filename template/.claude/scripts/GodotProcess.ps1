#requires -Version 7
<#
.SYNOPSIS
  Single home for "what is this Godot process, and whose checkout is it".

.OWNS
  Godot process IDENTITY (role classification) and CHECKOUT ATTRIBUTION, plus the worktree
  attribution engine every caller shares (Get-WorktreeId / Get-ProcSnapshot / Test-UnderRoot /
  Get-ProcVerdict, relocated here from run_test_suite.ps1). No caller re-derives any of this;
  run_test_suite.ps1 and regression_gate.ps1 dot-source this file.

.KILL POLICY -- two axes, BOTH required before anything dies.
  ROLE:      only TestRunner and Orphan are ever killable. Editor, Playtest and Unknown are
             never killed. Unknown is SPARED, not reaped -- a surviving orphan costs one retry
             that Wait-PipeDrained already absorbs; a killed editor costs unsaved work.
  OWNERSHIP: a live TestRunner on this checkout may belong to a CONCURRENT SESSION sharing the
             checkout. Preflight (before we launch anything) reaps Orphan ONLY -- a peer's live
             runner has a live parent, so it is not an Orphan and is spared. Only once we own a
             `dotnet test` root may TestRunners under that root (-OwnRootPid) be reaped.

.WHY NOT MainWindowTitle
  The retired idiom (`MainWindowTitle -notlike '*Godot Engine*'`) identifies by NEGATION, so every
  process the harness has no model of defaults into the kill set. Measured on 4.7.1 mono: an editor
  in its first seconds of startup has an EMPTY title, and a playtest window is titled with the
  PROJECT name -- both were killed. Positive identification (command line, parent chain, window
  handle) is unambiguous and is what this file implements.

.NOTES
  Dot-sourced flat .ps1 (house pattern -- no .psm1 anywhere in this harness). Windows-only
  (Win32_Process). `. (Join-Path $PSScriptRoot 'GodotProcess.ps1')`
#>

# --- Worktree attribution --------------------------------------------------------------
# Every worktree builds its OWN testhost.exe under <worktree>\.godot\mono\temp\bin\, so
# testhost/vstest carry the worktree root in ExecutablePath/CommandLine. The headless Godot
# does NOT (shared machine-global GODOT_BIN, launched with a relative `--path .`), so it is
# attributed by walking its PARENT chain up to the owning testhost.
function Get-WorktreeId {
    param([string] $Path)
    $norm  = $Path.TrimEnd('\', '/').ToLowerInvariant()
    $bytes = [System.Security.Cryptography.SHA1]::HashData([System.Text.Encoding]::UTF8.GetBytes($norm))
    return [System.Convert]::ToHexString($bytes).ToLowerInvariant().Substring(0, 8)
}

function Get-ProcSnapshot {
    $map = @{}
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) { $map[[int] $p.ProcessId] = $p }
    return $map
}

function Test-UnderRoot {
    param([string] $Text, [string] $Root)
    if ([string]::IsNullOrEmpty($Text)) { return $false }
    if ($Text.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    # Nested-worktree guard: worktrees live UNDER the main repo root (<main>\.claude\worktrees\<x>),
    # so a peer worktree's path also CONTAINS the main root. A reference into the worktrees dir
    # belongs to that worktree, not to Root. (When Root itself is a worktree, this infix never
    # occurs in its own paths, so the guard is inert.)
    $nested = $Root.TrimEnd('\') + '\.claude\worktrees\'
    return $Text.IndexOf($nested, [System.StringComparison]::OrdinalIgnoreCase) -lt 0
}

# 'MINE' = attributable to $Root | 'PEER' = another worktree's live process (SPARE it)
# | 'ORPHAN' = image path AND command line unreadable, or the parent chain is dead/unresolvable
# (dead weight for every worktree -- safe to reap). $MaxHops 0 = direct attribution only.
function Get-ProcVerdict {
    param([object] $Proc, [hashtable] $Map, [string] $Root, [int] $MaxHops = 0)
    $cur = $Proc
    for ($hop = 0; $hop -le $MaxHops; $hop++) {
        if ((Test-UnderRoot $cur.ExecutablePath $Root) -or (Test-UnderRoot $cur.CommandLine $Root)) { return 'MINE' }
        # Wedged zombie: neither field readable (observed 2026-05-31 -- a 1.2GB headless Godot
        # + its testhost survived a CommandLine-only sweep and held the named pipe).
        if (-not $cur.ExecutablePath -and -not $cur.CommandLine) { return 'ORPHAN' }
        if ($hop -eq $MaxHops) { break }
        $parentId = [int] $cur.ParentProcessId
        $parent   = if ($parentId -gt 0) { $Map[$parentId] } else { $null }
        # PID-reuse guard: a "parent" younger than its child is an impostor -> real parent is gone.
        if (-not $parent) { return 'ORPHAN' }
        if ($parent.CreationDate -and $cur.CreationDate -and $parent.CreationDate -gt $cur.CreationDate) { return 'ORPHAN' }
        $cur = $parent
    }
    return 'PEER'
}

# --- Internals -------------------------------------------------------------------------

# A root string that can never appear in any real path, so Get-ProcVerdict's MINE branch can
# never fire and its return collapses to exactly its ORPHAN test. This is how the orphan question
# is asked WITHOUT a checkout to attribute against -- reusing the one engine rather than forking it.
$script:GodotNoSuchRoot = '\\?\pp-godotprocess-no-such-root\'

function Resolve-RootPath {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try { return ([System.IO.Path]::GetFullPath($Path)).TrimEnd('\', '/') }
    catch { return $Path.TrimEnd('\', '/') }
}

# Walk up to $MaxHops parents, yielding each parent CIM object. Stops at a missing parent or a
# PID-reuse impostor (parent younger than child), matching Get-ProcVerdict's guard.
function Get-ParentChain {
    param([object] $Proc, [hashtable] $Map, [int] $MaxHops = 3)
    $chain = @()
    $cur = $Proc
    for ($hop = 0; $hop -lt $MaxHops; $hop++) {
        $parentId = [int] $cur.ParentProcessId
        $parent   = if ($parentId -gt 0) { $Map[$parentId] } else { $null }
        if (-not $parent) { break }
        if ($parent.CreationDate -and $cur.CreationDate -and $parent.CreationDate -gt $cur.CreationDate) { break }
        $chain += $parent
        $cur = $parent
    }
    return $chain
}

function Test-IsTestHostProc {
    param([object] $Proc)
    if (-not $Proc) { return $false }
    $name = [string] $Proc.Name
    if ($name -match '^(testhost|vstest\.console)(\.exe)?$') { return $true }
    $cl = [string] $Proc.CommandLine
    # `dotnet ... test ...` -- the wrapper's own launch form.
    return ($name -match '^dotnet(\.exe)?$' -and $cl -match '\stest(\s|$)')
}

# The two SPARE signatures that also identify an editor when seen on a PARENT (rule 4's target).
function Test-IsEditorProc {
    param([object] $Proc)
    if (-not $Proc) { return $false }
    if ([string] $Proc.Name -notlike 'Godot*') { return $false }
    if (([string] $Proc.CommandLine) -match '(^|\s)(-e|--editor)(\s|$)') { return $true }
    return (Test-HasMainWindow -ProcessId ([int] $Proc.ProcessId))
}

# MainWindowHandle is a Process property, not a Win32_Process one. A process that vanished
# between the snapshot and here has no window as far as we are concerned.
function Test-HasMainWindow {
    param([int] $ProcessId)
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ($p.MainWindowHandle -ne [System.IntPtr]::Zero)
    }
    catch { return $false }
}

# --- Role classification ----------------------------------------------------------------
<#
  ORDERING INVARIANT: every SPARE rule precedes the ORPHAN test. The Orphan test includes
  "dead parent", and interactive processes routinely HAVE dead parents -- a project-manager
  relaunch (the PM exits once the editor launches), a terminal-launched editor whose shell
  closed, an editor surviving an Explorer restart. Evaluating Orphan before the window-handle
  spare would classify exactly those editors killable: the original bug in new clothes.
#>
function Get-GodotRole {
    param(
        [Parameter(Mandatory)] [object] $Proc,
        [hashtable] $Map
    )
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $cl = [string] $Proc.CommandLine

    # 1. Positive test-runner signature on our own command line.
    if ($cl -match 'GdUnit4TestRunnerScene' -or $cl -match '--pipe-name\s+gdunit4-') { return 'TestRunner' }

    $chain = Get-ParentChain -Proc $Proc -Map $Map -MaxHops 3

    # 2. Launched by the test host (the runner's `--path .` is unresolvable; its parent is not).
    foreach ($p in $chain) { if (Test-IsTestHostProc -Proc $p) { return 'TestRunner' } }

    # 3. Explicit editor token.
    if ($cl -match '(^|\s)(-e|--editor)(\s|$)') { return 'Editor' }

    # 4. Child of an editor -> a playtest / run-project window. Interactive: never killed.
    foreach ($p in $chain) { if (Test-IsEditorProc -Proc $p) { return 'Playtest' } }

    # 5. Has a main window -> interactive, so Editor. The window handle is used ONLY to SPARE,
    #    never to kill -- the inverse of the retired title filter. Covers editors launched
    #    without -e (double-clicked project.godot, project-manager relaunch) and playtests whose
    #    owning editor already exited.
    if (Test-HasMainWindow -ProcessId ([int] $Proc.ProcessId)) { return 'Editor' }

    # 6. The existing engine's ORPHAN test, as an OR (both fields unreadable at hop 0, OR a
    #    dead / PID-reuse-impostor DIRECT parent). Narrowing this to an AND would spare a
    #    dead-parent headless Godot -- always ExecutablePath-readable -- leaving it holding the
    #    machine-global pipe, which costs every subsequent run, not one retry.
    #    MaxHops is 1, NOT 3: a zombie's dead link is its DIRECT parent (its testhost died),
    #    while an interactive process's dead ancestor sits deeper -- explorer.exe's own parent
    #    (userinit) is ALWAYS dead, so a 3-hop walk classifies every explorer-launched process
    #    Orphan, including a booting double-clicked editor before its window exists.
    if ((Get-ProcVerdict $Proc $Map $script:GodotNoSuchRoot 1) -eq 'ORPHAN') { return 'Orphan' }

    # 7. A booting editor with no window yet and no -e token lands here: spared from killing,
    #    invisible to Get-LiveEditor for those seconds. The queued path's continuous re-check
    #    converts that miss into an abort-and-requeue once the window appears.
    return 'Unknown'
}

# --- Checkout attribution ---------------------------------------------------------------
function Resolve-GodotCheckout {
    param(
        [Parameter(Mandatory)] [object] $Proc,
        [hashtable] $Map,
        # Roots to test parent-chain / .godot-mtime attribution against. Neither of those paths
        # can INVENT a root, so a caller that cares must supply the checkouts it knows about.
        [string[]] $CandidateRoots = @()
    )
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $cl = [string] $Proc.CommandLine

    # 1. Absolute `--path <dir>` token (present on editors and playtests launched with an
    #    explicit project path). A relative `--path .` -- what the test runner uses -- is
    #    unresolvable from here and falls through to step 2.
    $m = [regex]::Match($cl, '--path\s+(?:"([^"]+)"|(\S+))')
    if ($m.Success) {
        $raw = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
        if ([System.IO.Path]::IsPathRooted($raw)) { return (Resolve-RootPath $raw) }
    }

    $norm = @($CandidateRoots | ForEach-Object { Resolve-RootPath $_ } | Where-Object { $_ })

    # 2. TestRunners: attribute via the parent chain to the owning testhost/vstest, whose
    #    ExecutablePath/CommandLine carries the worktree root (existing Test-UnderRoot logic,
    #    nested-worktree guard included).
    foreach ($root in $norm) {
        if ((Get-ProcVerdict $Proc $Map $root 3) -eq 'MINE') { return $root }
    }

    # 3. Editor with no resolvable --path (project-manager or shortcut launch): the checkout whose
    #    .godot/ was touched within this process's lifetime is the one it is actually attached to.
    $start = $Proc.CreationDate
    if ($start) {
        foreach ($root in $norm) {
            $dotGodot = Join-Path $root '.godot'
            try {
                $item = Get-Item -LiteralPath $dotGodot -ErrorAction Stop
                if ($item.LastWriteTime -ge $start) { return $root }
            }
            catch { }
        }
    }

    # 4. Unresolved. Callers handle this conservatively in BOTH directions: never killed, and
    #    treated as MATCHING for the queue decision. Both errors then favour the user.
    return $null
}

# --- Kill / liveness queries -------------------------------------------------------------

function Get-GodotCandidate {
    param([hashtable] $Map)
    $out = @()
    foreach ($p in @(Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue)) {
        $ci = $Map[$p.Id]
        if ($ci) { $out += $ci }
    }
    return $out
}

function Test-ChainReachesPid {
    param([object] $Proc, [hashtable] $Map, [int] $RootPid, [int] $MaxHops = 6)
    if ([int] $Proc.ProcessId -eq $RootPid) { return $true }
    foreach ($p in (Get-ParentChain -Proc $Proc -Map $Map -MaxHops $MaxHops)) {
        if ([int] $p.ProcessId -eq $RootPid) { return $true }
    }
    return $false
}

<#
  Processes safe to kill, per the two-axis policy in the file header.
    WITHOUT -OwnRootPid (PREFLIGHT scope): role Orphan only, attributed to $Checkout or
      unattributable. A peer session's live runner has a live parent -> not Orphan -> spared.
    WITH -OwnRootPid: additionally any TestRunner whose parent chain reaches that PID -- the
      `dotnet test` root THIS invocation launched, so it is ours by construction.
  Never returns Editor, Playtest or Unknown.
#>
function Get-ReapableGodot {
    param(
        [Parameter(Mandatory)] [string] $Checkout,
        [hashtable] $Map,
        [int] $OwnRootPid = 0,
        [string[]] $CandidateRoots
    )
    if (-not $Map) { $Map = Get-ProcSnapshot }
    if (-not $CandidateRoots) { $CandidateRoots = @($Checkout) }
    $target = Resolve-RootPath $Checkout

    $out = @()
    foreach ($ci in (Get-GodotCandidate -Map $Map)) {
        $role = Get-GodotRole -Proc $ci -Map $Map
        if ($role -eq 'Orphan') {
            $own = Resolve-GodotCheckout -Proc $ci -Map $Map -CandidateRoots $CandidateRoots
            if ($null -eq $own -or $own -ieq $target) { $out += $ci }
            continue
        }
        if ($role -eq 'TestRunner') {
            if ($OwnRootPid -gt 0 -and (Test-ChainReachesPid -Proc $ci -Map $Map -RootPid $OwnRootPid)) {
                $out += $ci
                continue
            }
            # Zombie runner: a readable runner signature wins rule 1 before the Orphan test, so
            # role alone never flags a runner whose owning test host is GONE -- yet that is
            # exactly the wedged-pipe holder the orphan sweep exists for. Reapable at every
            # scope: a live peer's runner has a live parent and never matches.
            if ((Get-ProcVerdict $ci $Map $script:GodotNoSuchRoot 1) -eq 'ORPHAN') { $out += $ci }
        }
    }
    return $out
}

<#
  Interactive Godot processes (Editor or Playtest) on $Checkout -- the readiness clause the gate
  queue and the suite wrapper's entry guard both test. A process whose checkout resolves to $null
  IS included: unknown checkout is treated as matching, so the ambiguity blocks a run rather than
  risking the user's editor.
#>
function Get-LiveEditor {
    param(
        [string] $Checkout,
        [hashtable] $Map,
        [string[]] $CandidateRoots
    )
    if (-not $Map) { $Map = Get-ProcSnapshot }
    if (-not $CandidateRoots -and $Checkout) { $CandidateRoots = @($Checkout) }
    $target = if ($Checkout) { Resolve-RootPath $Checkout } else { $null }

    $out = @()
    foreach ($ci in (Get-GodotCandidate -Map $Map)) {
        $role = Get-GodotRole -Proc $ci -Map $Map
        if ($role -ne 'Editor' -and $role -ne 'Playtest') { continue }
        if (-not $target) { $out += $ci; continue }
        $own = Resolve-GodotCheckout -Proc $ci -Map $Map -CandidateRoots $CandidateRoots
        if ($null -eq $own -or $own -ieq $target) { $out += $ci }
    }
    return $out
}
