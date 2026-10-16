param(
    [string]$DriveLetter = 'L',
    [switch]$RestartStack,
    [switch]$Build
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'push-ubuntu-deploy.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$sourceRoot = Join-Path $repoRoot 'deploy\ubuntu'
$targetRoot = ('{0}:\' -f $DriveLetter.TrimEnd(':'))

if (-not (Test-Path $sourceRoot)) {
    throw "Source deploy folder missing: $sourceRoot"
}
if (-not (Test-Path $targetRoot)) {
    throw "Target drive not mapped: $targetRoot"
}

$targetTools = Join-Path $targetRoot 'tools'
if (-not (Test-Path $targetTools)) {
    New-Item -ItemType Directory -Path $targetTools -Force | Out-Null
}

$copies = @(
    @{ Source = (Join-Path $sourceRoot 'luminax.sh'); Target = (Join-Path $targetRoot 'luminax.sh') },
    @{ Source = (Join-Path $sourceRoot 'ubuntu-one-shot-install.sh'); Target = (Join-Path $targetRoot 'ubuntu-one-shot-install.sh') },
    @{ Source = (Join-Path $sourceRoot 'tools\self-upgrade-smoke.sh'); Target = (Join-Path $targetTools 'self-upgrade-smoke.sh') }
)

foreach ($item in $copies) {
    Copy-Item -Path $item.Source -Destination $item.Target -Force
    Write-Host ("Copied {0} -> {1}" -f $item.Source, $item.Target)
}

if ($RestartStack) {
    $restartScript = Join-Path $repoRoot 'restart-ubuntu-luminax.ps1'
    if (-not (Test-Path $restartScript)) {
        throw "Missing restart script: $restartScript"
    }
    & $restartScript -Build:$Build
}

Write-Host 'Ubuntu deploy files pushed successfully.'
