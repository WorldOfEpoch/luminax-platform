param(
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot '_local_runtime\pc-brain')
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-local-brain-status.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-HttpHealth {
    param(
        [string]$Url,
        [int]$TimeoutSec = 3
    )

    try {
        $response = Invoke-RestMethod -MaximumRedirection 0 -Uri $Url -Method Get -TimeoutSec $TimeoutSec
        return @{
            ok = $true
            payload = $response
        }
    } catch {
        return @{
            ok = $false
            error = $_.Exception.Message
        }
    }
}

$catalog = @(
    @{ name = 'ollama'; health = 'http://127.0.0.1:11434/api/tags'; pid = 'ollama.pid'; timeout = 3 },
    @{ name = 'private-control-tunnel'; health = 'http://127.0.0.1:18040/routes/catalog'; pid = 'private-control-tunnel.pid'; timeout = 5 },
    @{ name = 'affect-controller'; health = 'http://127.0.0.1:8502/health'; pid = 'affect-controller.pid'; timeout = 3 },
    @{ name = 'luminax-brain'; health = 'http://127.0.0.1:8501/health'; pid = 'luminax-brain.pid'; timeout = 3 },
    @{ name = 'mcp-router'; health = 'http://127.0.0.1:8503/health'; pid = 'mcp-router.pid'; timeout = 3 },
    @{ name = 'voice-agent'; health = 'http://127.0.0.1:8030/health'; pid = 'voice-agent.pid'; timeout = 3 },
    @{ name = 'luminax-companion-ui'; health = 'http://127.0.0.1:8095/health'; pid = 'luminax-companion-ui.pid'; timeout = 5 },
    @{ name = 'olympus-edge-bridge'; health = 'http://127.0.0.1:8150/health'; pid = 'olympus-edge-bridge.pid'; timeout = 15 },
    @{ name = 'desktop-shell'; health = ''; pid = 'desktop-shell.pid' }
)

$pidsRoot = Join-Path $RuntimeRoot 'pids'
$rows = foreach ($entry in $catalog) {
    $pidPath = Join-Path $pidsRoot $entry.pid
    $processId = $null
    $processState = 'absent'
    if (Test-Path $pidPath) {
        $rawPid = (Get-Content -Path $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($rawPid -and ($rawPid -as [int])) {
            $processId = [int]$rawPid
            $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($proc) {
                $processState = 'running'
            } else {
                $processState = 'stale_pid'
            }
        }
    }

    if ($entry.name -eq 'desktop-shell' -and $processState -ne 'running') {
        $shellProc = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match 'Luminax\.DesktopShell\.dll' } |
            Select-Object -First 1
        if ($shellProc) {
            $processId = [int]$shellProc.ProcessId
            $processState = 'running'
        }
    }

    $health = $null
    if ($entry.health) {
        $timeoutSec = if ($entry.timeout) { [int]$entry.timeout } else { 3 }
        $health = Test-HttpHealth -Url $entry.health -TimeoutSec $timeoutSec
        if ($health.ok -and $processState -ne 'running') {
            $processState = 'healthy_endpoint'
        }
    }

    [pscustomobject]@{
        name = $entry.name
        pid = $processId
        process_state = $processState
        health_url = $entry.health
        healthy = if ($null -eq $health) { $null } else { $health.ok }
        health_detail = if ($null -eq $health) { $null } elseif ($health.ok) { 'ok' } else { $health.error }
    }
}

$rows | Format-Table -AutoSize

$requiredServices = @(
    'ollama',
    'private-control-tunnel',
    'affect-controller',
    'luminax-brain',
    'mcp-router',
    'voice-agent',
    'luminax-companion-ui',
    'olympus-edge-bridge',
    'desktop-shell'
)
$unhealthy = @(
    $rows | Where-Object {
        $_.name -in $requiredServices -and (
            $_.process_state -notin @('running', 'healthy_endpoint') -or
            ($null -ne $_.healthy -and -not $_.healthy)
        )
    }
)

if (@($unhealthy).Count -gt 0) {
    Write-Error ("Required local brain services are unhealthy: " + ((@($unhealthy) | ForEach-Object { $_.name }) -join ', '))
    exit 1
}

exit 0
