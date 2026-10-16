param(
    [string[]]$Services = @(),
    [switch]$Build
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'restart-ubuntu-luminax.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-LauncherEnv {
    $envPath = Join-Path $PSScriptRoot '.env.windows'
    $map = @{}
    if (-not (Test-Path $envPath)) { return $map }
    foreach ($line in Get-Content -Path $envPath) {
        $trim = [string]$line
        if (-not $trim) { continue }
        $trim = $trim.Trim()
        if (-not $trim -or $trim.StartsWith('#')) { continue }
        $parts = $trim -split '=', 2
        if ($parts.Count -ne 2) { continue }
        $map[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $map
}

function Quote-ShellArg {
    param([string]$Value)
    if ($null -eq $Value) { return "''" }
    $replacement = "'" + '"' + "'" + '"' + "'"
    return "'" + ($Value.Replace("'", $replacement)) + "'"
}

$launcherEnv = Read-LauncherEnv
$hostName = [string]($launcherEnv['LUMINAX_HOSTNAME'])
$userName = [string]($launcherEnv['LUMINAX_USERNAME'])
$keyPath = [string]($launcherEnv['LUMINAX_KEY_PATH'])
$password = [string]($launcherEnv['LUMINAX_SSH_PASSWORD'])
$remoteBaseDir = [string]($launcherEnv['LUMINAX_REMOTE_BASE_DIR'])

if (-not $hostName) { throw 'LUMINAX_HOSTNAME missing in .env.windows' }
if (-not $userName) { throw 'LUMINAX_USERNAME missing in .env.windows' }
if (-not $remoteBaseDir) { $remoteBaseDir = '/opt/luminax' }

$cmdParts = @('cd', (Quote-ShellArg $remoteBaseDir), '&&', 'docker', 'compose', 'up', '-d')
if ($Build) { $cmdParts += '--build' }
if ($Services.Count -gt 0) { $cmdParts += $Services }
$remoteCommand = $cmdParts -join ' '

if ($password) {
    if (-not (Get-Command plink -ErrorAction SilentlyContinue)) {
        throw 'Password auth requires plink (PuTTY).'
    }
    & plink -P 22 -pw $password "$userName@$hostName" ("printf '%s\n' " + (Quote-ShellArg $password) + " | sudo -S -p '' bash -lc " + (Quote-ShellArg $remoteCommand))
    if ($LASTEXITCODE -ne 0) { throw 'Remote restart failed.' }
} else {
    if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
        throw 'OpenSSH client not found.'
    }
    $sshArgs = @('-p', '22', '-o', 'StrictHostKeyChecking=yes')
    if ($keyPath) { $sshArgs += @('-i', $keyPath) }
    $sshArgs += "$userName@$hostName"
    $sshArgs += "sudo bash -lc " + (Quote-ShellArg $remoteCommand)
    & ssh @sshArgs
    if ($LASTEXITCODE -ne 0) { throw 'Remote restart failed.' }
}

Write-Host 'Ubuntu Luminax restart command completed.'
