# Claude Code shell integration — canonical home for the `claude-*` PowerShell functions.
#
# The user's $PROFILE dot-sources this file rather than copying the bodies, so these
# functions are version-controlled and edits here reach the shell with no sync step:
#
#     . "<repo>\.claude\scripts\claude_profile_functions.ps1"
#
# Provisioned/verified by /workstation_setup Phase 4.
#
# `claude-primary` runs the default login. A seat's login is its `configDir` in .claude/seats.json,
# so a seat with a login gets its function by one line: `function claude-<name> { Invoke-ClaudeSeat '<name>' @args }`.

# No param block: `claude -p` must reach claude, and a declared parameter would bind `-p` as an abbreviation of itself.
function Invoke-ClaudeInLogin {
    $configDir, $rest = $args
    $old = $env:CLAUDE_CONFIG_DIR
    $env:CLAUDE_CONFIG_DIR = $configDir
    try {
        claude @rest
    } finally {
        $env:CLAUDE_CONFIG_DIR = $old
    }
}

function Get-SeatConfigDir {
    param([Parameter(Mandatory)][string]$Seat)
    $seats = Join-Path $PSScriptRoot '..\seats.json'
    $row = (Get-Content $seats -Raw | ConvertFrom-Json).seats.$Seat
    if (-not $row -or -not $row.configDir) { return $null }
    if ($row.configDir -match '^~') { Join-Path $HOME ($row.configDir -replace '^~[\/]*', '') } else { $row.configDir }
}

function Invoke-ClaudeSeat {
    $seat, $rest = $args
    $dir = Get-SeatConfigDir $seat
    if (-not $dir) { Write-Error "seat '$seat' has no configDir in .claude/seats.json"; return }
    Invoke-ClaudeInLogin $dir @rest
}

function claude-primary { Invoke-ClaudeInLogin "$HOME\.claude" @args }

function claude-secondary { Invoke-ClaudeSeat 'claude-secondary' @args }
