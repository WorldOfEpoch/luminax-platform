param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [string]$UnityProjectRoot,
    [string]$LuminaxRoot = ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../')))
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot '../../../tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'tools\CodenameDarius\scripts\Install-CodenameDariusLinks.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$UnityProjectRoot = [IO.Path]::GetFullPath($UnityProjectRoot)
if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $UnityProjectRoot 'Assets') -PathType Container)) {
    throw 'Choose existing project and Unity project directories.'
}
$links = @(
    @{ destination = Join-Path $UnityProjectRoot 'Assets/Darius'; source = Join-Path $LuminaxRoot 'tools/CodenameDarius/unity/Assets/Darius'; kind = 'Junction'; root = $UnityProjectRoot },
    @{ destination = Join-Path $ProjectRoot 'tools/darius_agent_cli.py'; source = Join-Path $LuminaxRoot 'tools/CodenameDarius/python/darius_agent_cli.py'; kind = 'HardLink'; root = $ProjectRoot },
    @{ destination = Join-Path $ProjectRoot 'tools/meshy_cli.py'; source = Join-Path $LuminaxRoot 'tools/CodenameDarius/python/meshy_cli.py'; kind = 'HardLink'; root = $ProjectRoot }
)
# Preflight the complete operation before creating any links. Existing source wins.
foreach ($link in $links) {
    $link.destination = Assert-PublicChildPath -Root $link.root -Path $link.destination
    if (-not (Test-Path -LiteralPath $link.source)) { throw 'A required shared tool source is missing.' }
    if (Test-Path -LiteralPath $link.destination) {
        $item = Get-Item -LiteralPath $link.destination -Force
        $targets = @($item.Target | ForEach-Object { [IO.Path]::GetFullPath($_) })
        if ($item.LinkType -ne $link.kind -or $targets -notcontains [IO.Path]::GetFullPath($link.source)) {
            throw 'An existing project item occupies a requested link path. Preserve it and reconcile it manually; this installer never deletes project source.'
        }
    }
}
foreach ($link in $links) {
    if (Test-Path -LiteralPath $link.destination) { continue }
    $parent = Split-Path -Parent $link.destination
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    New-Item -ItemType $link.kind -Path $link.destination -Target $link.source | Out-Null
}
Write-Host 'CodenameDarius source links ready. Configure project environment explicitly; no existing environment file was read or changed.'
