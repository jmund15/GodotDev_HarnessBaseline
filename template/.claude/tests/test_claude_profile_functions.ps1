#!/usr/bin/env pwsh
# Proof for scripts/claude_profile_functions.ps1: each function runs `claude` under its login's
# CLAUDE_CONFIG_DIR with the caller's arguments and puts the variable back afterwards. The seat's
# login comes from the shipped seats.json.
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '..\scripts\claude_profile_functions.ps1')

$script:Failed = 0
function Assert-True {
    param([bool]$Condition, [string]$Name, [string]$Detail = '')
    if ($Condition) { Write-Host "ok   $Name"; return }
    $script:Failed++
    Write-Host "FAIL $Name$(if ($Detail) { ": $Detail" })"
}

$script:Seen = $null
function claude { $script:Seen = "$env:CLAUDE_CONFIG_DIR|$($args -join ' ')" }

$env:CLAUDE_CONFIG_DIR = 'outer'
claude-primary --resume abc
Assert-True ($script:Seen -eq "$HOME\.claude|--resume abc") 'claude-primary runs the default login with its arguments' $script:Seen
Assert-True ($env:CLAUDE_CONFIG_DIR -eq 'outer') 'claude-primary restores CLAUDE_CONFIG_DIR'

claude-secondary -p hi
Assert-True ($script:Seen -eq "$HOME\.claude-secondary|-p hi") 'claude-secondary runs the login seats.json names for it' $script:Seen
Assert-True ($env:CLAUDE_CONFIG_DIR -eq 'outer') 'claude-secondary restores CLAUDE_CONFIG_DIR'

Assert-True ($null -eq (Get-SeatConfigDir 'antigravity')) 'a seat with an empty configDir has no login'
$script:Seen = $null
$refused = try { Invoke-ClaudeSeat 'antigravity'; '' } catch { $_.Exception.Message }
Assert-True (($null -eq $script:Seen) -and ($refused -match 'seats.json')) 'a seat with no login launches nothing and names seats.json' $refused

if ($script:Failed) { Write-Host "$script:Failed failed"; exit 1 }
Write-Host 'ok   claude_profile_functions'
