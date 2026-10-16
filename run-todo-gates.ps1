param([switch]$Apply,[switch]$DryRun)

. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'run-todo-gates.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'tools/tests/Test-PublicWindowsTools.ps1')
$python = Get-Command python -ErrorAction Stop
& $python.Source -m unittest discover -s (Join-Path $PSScriptRoot 'tools/tests') -p 'test_*.py'
if ($LASTEXITCODE -ne 0) { throw 'Offline Python tool checks failed.' }
Write-Host 'Offline source gates passed. Services, remote installation, models, and Unity were not tested.'
