param(
    [ValidateSet('ensure','status','stop')]
    [string]$Action = 'status',
    [string]$HostName = '',
    [string]$UserName = '',
    [string]$KeyPath = '',
    [int]$SshPort = 22,
    [string]$LocalHost = '127.0.0.1',
    [int]$LocalPort = 18040,
    [string]$TargetHost = '127.0.0.1',
    [int]$TargetPort = 8040,
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot '_local_runtime\pc-brain')
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-private-control-tunnel.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


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
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($key) { $map[$key] = $value }
    }
    return $map
}

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        $null = New-Item -ItemType Directory -Path $Path -Force
    }
}

function Test-PrivateControlHealth {
    param(
        [string]$LocalHostName,
        [int]$LocalListenPort,
        [int]$TimeoutSec = 4
    )
    $url = "http://${LocalHostName}:${LocalListenPort}/routes/catalog"
    try {
        $resp = Invoke-RestMethod -MaximumRedirection 0 -Uri $url -Method Get -TimeoutSec $TimeoutSec
        return @{
            ok = $true
            url = $url
            payload = $resp
            error = $null
        }
    } catch {
        return @{
            ok = $false
            url = $url
            payload = $null
            error = $_.Exception.Message
        }
    }
}

function Get-TrackedPid {
    param([string]$PidPath)
    if (-not (Test-Path $PidPath)) { return $null }
    $rawPid = (Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $rawPid -or -not ($rawPid -as [int])) { return $null }
    return [int]$rawPid
}

function Stop-TrackedTunnel {
    param([string]$PidPath, [int]$Port)
    $status = Stop-PublicRecordedProcess -PidPath $PidPath
    if ($status -eq 'stopped') { return @(Get-TrackedPid -PidPath $PidPath) }
    if ($status -like 'unverified*') { Write-Warning 'Unverified prior tunnel was preserved; no process is stopped by port number.' }
    return @()
}

$launcherEnv = Read-LauncherEnv
if (-not $HostName -and $launcherEnv.ContainsKey('LUMINAX_HOSTNAME')) { $HostName = [string]$launcherEnv['LUMINAX_HOSTNAME'] }
if (-not $UserName -and $launcherEnv.ContainsKey('LUMINAX_USERNAME')) { $UserName = [string]$launcherEnv['LUMINAX_USERNAME'] }
if (-not $KeyPath -and $launcherEnv.ContainsKey('LUMINAX_KEY_PATH')) { $KeyPath = [string]$launcherEnv['LUMINAX_KEY_PATH'] }
if (($SshPort -eq 22) -and $launcherEnv.ContainsKey('LUMINAX_SSH_PORT')) {
    try { $SshPort = [int]$launcherEnv['LUMINAX_SSH_PORT'] } catch {}
}
if (($LocalHost -eq '127.0.0.1') -and $launcherEnv.ContainsKey('LUMINAX_TUNNEL_LOCAL_HOST')) { $LocalHost = [string]$launcherEnv['LUMINAX_TUNNEL_LOCAL_HOST'] }
if (($LocalPort -eq 18040) -and $launcherEnv.ContainsKey('LUMINAX_TUNNEL_LOCAL_PORT')) {
    try { $LocalPort = [int]$launcherEnv['LUMINAX_TUNNEL_LOCAL_PORT'] } catch {}
}
if (($TargetHost -eq '127.0.0.1') -and $launcherEnv.ContainsKey('LUMINAX_TUNNEL_TARGET_HOST')) { $TargetHost = [string]$launcherEnv['LUMINAX_TUNNEL_TARGET_HOST'] }
if (($TargetPort -eq 8040) -and $launcherEnv.ContainsKey('LUMINAX_TUNNEL_TARGET_PORT')) {
    try { $TargetPort = [int]$launcherEnv['LUMINAX_TUNNEL_TARGET_PORT'] } catch {}
}

$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$logsDir = Join-Path $RuntimeRoot 'logs'
$pidsDir = Join-Path $RuntimeRoot 'pids'
Ensure-Directory $RuntimeRoot
Ensure-Directory $logsDir
Ensure-Directory $pidsDir

$pidPath = Join-Path $pidsDir 'private-control-tunnel.pid'
$stdoutPath = Join-Path $logsDir 'private-control-tunnel.stdout.log'
$stderrPath = Join-Path $logsDir 'private-control-tunnel.stderr.log'
$health = Test-PrivateControlHealth -LocalHostName $LocalHost -LocalListenPort $LocalPort

if ($Action -eq 'status') {
    $status = [ordered]@{
        action = $Action
        healthy = $health.ok
        url = $health.url
        error = $health.error
        pid = Get-TrackedPid -PidPath $pidPath
        host = $HostName
        user = $UserName
        ssh_port = $SshPort
        local_host = $LocalHost
        local_port = $LocalPort
        target_host = $TargetHost
        target_port = $TargetPort
    }
    $status | ConvertTo-Json -Depth 8
    exit 0
}

if ($Action -eq 'stop') {
    $stopped = Stop-TrackedTunnel -PidPath $pidPath -Port $LocalPort
    @{
        action = $Action
        stopped = @($stopped)
        local_port = $LocalPort
    } | ConvertTo-Json -Depth 6
    exit 0
}

if ($health.ok) {
    @{
        action = $Action
        status = 'already_healthy'
        url = $health.url
        pid = Get-TrackedPid -PidPath $pidPath
        local_port = $LocalPort
    } | ConvertTo-Json -Depth 6
    exit 0
}

if (-not $HostName) { throw 'HostName is required for the private control tunnel.' }
if (-not $UserName) { throw 'UserName is required for the private control tunnel.' }
if (-not $KeyPath -or -not (Test-Path $KeyPath)) { throw 'A valid SSH key path is required for the private control tunnel.' }

$null = Stop-TrackedTunnel -PidPath $pidPath -Port $LocalPort
$ssh = Get-Command ssh -ErrorAction SilentlyContinue
if (-not $ssh) { throw 'ssh.exe is required for the private control tunnel.' }

$forwardSpec = "${LocalHost}:${LocalPort}:${TargetHost}:${TargetPort}"
$sshArgs = @(
    '-o', 'BatchMode=yes',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3',
    '-o', 'StrictHostKeyChecking=yes',
    '-p', "$SshPort",
    '-i', $KeyPath,
    '-N',
    '-L', $forwardSpec,
    "$UserName@$HostName"
)

$proc = Start-Process -FilePath $ssh.Source `
    -ArgumentList $sshArgs `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

Write-PublicProcessReceipt -PidPath $pidPath -Process $proc
$deadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 750
    $health = Test-PrivateControlHealth -LocalHostName $LocalHost -LocalListenPort $LocalPort
    if ($health.ok) {
        @{
            action = $Action
            status = 'started'
            pid = $proc.Id
            url = $health.url
            local_port = $LocalPort
            target = "${TargetHost}:$TargetPort"
        } | ConvertTo-Json -Depth 6
        exit 0
    }
    $running = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    if (-not $running) {
        break
    }
} while ((Get-Date) -lt $deadline)

$stderrTail = ''
if (Test-Path $stderrPath) {
    $stderrTail = ((Get-Content -Path $stderrPath -Tail 20 -ErrorAction SilentlyContinue) -join "`n").Trim()
}

throw ("Private control tunnel failed to become healthy on {0}:{1}. {2}" -f $LocalHost, $LocalPort, ($stderrTail | Select-Object -First 1))
