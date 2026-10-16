param(
    [string]$DriveLetter = 'L'
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'map-ubuntu-luminax-drive.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


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

$launcherEnv = Read-LauncherEnv
$hostName = [string]($launcherEnv['LUMINAX_HOSTNAME'])
$userName = [string]($launcherEnv['LUMINAX_USERNAME'])

if (-not $hostName) { throw 'LUMINAX_HOSTNAME missing in .env.windows' }
if (-not $userName) { throw 'LUMINAX_USERNAME missing in .env.windows' }

$drive = ('{0}:' -f $DriveLetter.TrimEnd(':'))
$unc = '\\sshfs.r\{0}@{1}!22\opt\luminax' -f $userName, $hostName

Write-Host "Mapping $drive to $unc"
cmd /c "net use $drive /delete /y" | Out-Null
cmd /c "net use $drive $unc /persistent:yes"

Write-Host "Mapped $drive to Ubuntu /opt/luminax"
