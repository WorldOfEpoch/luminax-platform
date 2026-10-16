param(
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot '_local_runtime\pc-brain')
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-stop-local-brain.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-TrackedProcess {
    param([string]$Name, [string]$PidPath)
    [pscustomobject]@{ name = $Name; status = (Stop-PublicRecordedProcess -PidPath $PidPath) }
}

$order = @(
    'desktop-shell.pid',
    'olympus-edge-bridge.pid',
    'luminax-companion-ui.pid',
    'voice-agent.pid',
    'langgraph-orchestrator.pid',
    'mcp-router.pid',
    'luminax-brain.pid',
    'affect-controller.pid',
    'private-control-tunnel.pid',
    'ollama.pid'
)

$pidsRoot = Join-Path $RuntimeRoot 'pids'
$results = foreach ($pidFile in $order) {
    $name = [System.IO.Path]::GetFileNameWithoutExtension($pidFile)
    Stop-TrackedProcess -Name $name -PidPath (Join-Path $pidsRoot $pidFile)
}

# Ports identify listeners, not ownership; never terminate a process based only on a port.
$results | Format-Table -AutoSize
