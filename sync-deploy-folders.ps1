param(
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'sync-deploy-folders.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$deployRoot = Join-Path $root 'deploy'
$winDir = Join-Path $deployRoot 'windows'
$ubuntuDir = Join-Path $deployRoot 'ubuntu'
$ubuntuToolsDir = Join-Path $ubuntuDir 'tools'

New-Item -ItemType Directory -Path $winDir -Force | Out-Null
New-Item -ItemType Directory -Path $ubuntuToolsDir -Force | Out-Null

$windowsFiles = @(
    'windows-program.ps1',
    'windows-controller.ps1',
    'windows-oneclick-install.ps1',
    'windows-bootstrap.ps1',
    'install-windows-node-monitor.ps1',
    'windows-node-monitor.ps1',
    'windows-model-bootstrap.ps1',
    'move-existing-models-to-d.ps1',
    'map-ubuntu-luminax-drive.ps1',
    'push-ubuntu-deploy.ps1',
    'restart-ubuntu-luminax.ps1'
)

$ubuntuFiles = @(
    'luminax.sh',
    'ubuntu-one-shot-install.sh'
)

foreach ($name in $windowsFiles) {
    Copy-Item -Path (Join-Path $root $name) -Destination (Join-Path $winDir $name) -Force
}

foreach ($name in $ubuntuFiles) {
    Copy-Item -Path (Join-Path $root $name) -Destination (Join-Path $ubuntuDir $name) -Force
}

Copy-Item -Path (Join-Path $root 'tools\self-upgrade-smoke.sh') -Destination (Join-Path $ubuntuToolsDir 'self-upgrade-smoke.sh') -Force

Write-Host "Deployment folders synced:"
Write-Host "  $winDir"
Write-Host "  $ubuntuDir"
