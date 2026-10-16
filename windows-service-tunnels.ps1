param(
    [ValidateSet('ensure','status','stop')]
    [string]$Action = 'status',
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot '_local_runtime\pc-brain')
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-service-tunnels.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


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

function Get-TrackedPid {
    param([string]$PidPath)
    if (-not (Test-Path $PidPath)) { return $null }
    $rawPid = (Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $rawPid -or -not ($rawPid -as [int])) { return $null }
    return [int]$rawPid
}

function Stop-TunnelByPort {
    param([string]$PidPath, [int]$Port)
    $status = Stop-PublicRecordedProcess -PidPath $PidPath
    if ($status -eq 'stopped') { return @(Get-TrackedPid -PidPath $PidPath) }
    if ($status -like 'unverified*') { Write-Warning 'Unverified prior tunnel was preserved; no process is stopped by port number.' }
    return @()
}

function Test-TunnelHealth {
    param(
        [string]$Url,
        [int]$TimeoutSec = 4
    )

    if (-not $Url) {
        return @{ ok = $true; error = $null }
    }

    try {
        $response = Invoke-WebRequest -MaximumRedirection 0 -Uri $Url -Method Get -UseBasicParsing -TimeoutSec $TimeoutSec
        return @{
            ok = ($response.StatusCode -lt 400)
            error = $null
            status_code = $response.StatusCode
        }
    } catch {
        return @{
            ok = $false
            error = $_.Exception.Message
            status_code = $null
        }
    }
}

function Invoke-RemoteSsh {
    param(
        [string]$HostName,
        [string]$UserName,
        [string]$KeyPath,
        [int]$SshPort,
        [string]$RemoteCommand
    )

    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if (-not $ssh) { throw 'ssh.exe is required for service tunnels.' }
    $result = & $ssh.Source `
        -o BatchMode=yes `
        -o StrictHostKeyChecking=yes `
        -p $SshPort `
        -i $KeyPath `
        "$UserName@$HostName" `
        $RemoteCommand 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($result | Out-String).Trim()
    }
    return ($result | Out-String).Trim()
}

function Resolve-ContainerIp {
    param(
        [string]$HostName,
        [string]$UserName,
        [string]$KeyPath,
        [int]$SshPort,
        [string]$ContainerName
    )

    $remoteCommand = "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $ContainerName"
    $ip = Invoke-RemoteSsh -HostName $HostName -UserName $UserName -KeyPath $KeyPath -SshPort $SshPort -RemoteCommand $remoteCommand
    if (-not $ip) {
        throw "Container $ContainerName does not have a routable IP."
    }
    return $ip
}

$serviceCatalog = @(
    @{ name = 'assistant-core'; local_port = 18000; container = 'luminax-assistant-core-1'; target_port = 8000; health = 'http://127.0.0.1:18000/api/ping' },
    @{ name = 'realtime-gateway'; local_port = 18050; container = 'luminax-realtime-gateway-1'; target_port = 8050; health = 'http://127.0.0.1:18050/session/info' },
    @{ name = 'browser-agent'; local_port = 18010; container = 'luminax-browser-agent-1'; target_port = 8010; health = 'http://127.0.0.1:18010/health' },
    @{ name = 'research-worker'; local_port = 18060; container = 'luminax-research-worker-1'; target_port = 8060; health = 'http://127.0.0.1:18060/health' },
    @{ name = 'site-manager'; local_port = 18070; container = 'luminax-site-manager-1'; target_port = 8070; health = 'http://127.0.0.1:18070/health' },
    @{ name = 'account-worker'; local_port = 18085; container = 'luminax-account-worker-1'; target_port = 8085; health = 'http://127.0.0.1:18085/health' },
    @{ name = 'network-manager'; local_port = 18090; container = 'luminax-network-manager-1'; target_port = 8090; health = 'http://127.0.0.1:18090/health' },
    @{ name = 'hive-dreamer'; local_port = 18101; container = 'luminax-lumi-hive-dreamer-1'; target_port = 8101; health = 'http://127.0.0.1:18101/health' },
    @{ name = 'hive-engineer'; local_port = 18102; container = 'luminax-lumi-hive-engineer-1'; target_port = 8102; health = 'http://127.0.0.1:18102/health' },
    @{ name = 'hive-critic'; local_port = 18103; container = 'luminax-lumi-hive-critic-1'; target_port = 8103; health = 'http://127.0.0.1:18103/health' },
    @{ name = 'three-d-engineer'; local_port = 18110; container = 'luminax-lumi-3d-engineer-1'; target_port = 8110; health = 'http://127.0.0.1:18110/health' },
    @{ name = 'finance-swarm'; local_port = 18120; container = 'luminax-lumi-finance-swarm-1'; target_port = 8120; health = 'http://127.0.0.1:18120/health' }
)

$envWindows = Read-LauncherEnv
$hostName = [string]($envWindows['LUMINAX_HOSTNAME'])
$userName = [string]($envWindows['LUMINAX_USERNAME'])
$keyPath = [string]($envWindows['LUMINAX_KEY_PATH'])
$sshPort = 22
if ($envWindows.ContainsKey('LUMINAX_SSH_PORT')) {
    try { $sshPort = [int]$envWindows['LUMINAX_SSH_PORT'] } catch {}
}

if (-not $hostName) { throw 'LUMINAX_HOSTNAME is required in .env.windows.' }
if (-not $userName) { throw 'LUMINAX_USERNAME is required in .env.windows.' }
if (-not $keyPath -or -not (Test-Path $keyPath)) { throw 'A valid LUMINAX_KEY_PATH is required in .env.windows.' }

$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$logsDir = Join-Path $RuntimeRoot 'logs'
$pidsDir = Join-Path $RuntimeRoot 'pids'
Ensure-Directory $RuntimeRoot
Ensure-Directory $logsDir
Ensure-Directory $pidsDir

if ($Action -eq 'stop') {
    $rows = foreach ($svc in $serviceCatalog) {
        $pidPath = Join-Path $pidsDir ("service-tunnel-{0}.pid" -f $svc.name)
        $stopped = Stop-TunnelByPort -PidPath $pidPath -Port ([int]$svc.local_port)
        [pscustomobject]@{
            name = $svc.name
            local_port = $svc.local_port
            stopped = @($stopped)
        }
    }
    $rows | ConvertTo-Json -Depth 6
    exit 0
}

$rows = foreach ($svc in $serviceCatalog) {
    $pidPath = Join-Path $pidsDir ("service-tunnel-{0}.pid" -f $svc.name)
    $stdoutPath = Join-Path $logsDir ("service-tunnel-{0}.stdout.log" -f $svc.name)
    $stderrPath = Join-Path $logsDir ("service-tunnel-{0}.stderr.log" -f $svc.name)
    $health = Test-TunnelHealth -Url ([string]$svc.health)

    if ($Action -eq 'status') {
        [pscustomobject]@{
            name = $svc.name
            local_port = $svc.local_port
            pid = Get-TrackedPid -PidPath $pidPath
            healthy = $health.ok
            health_detail = if ($health.ok) { 'ok' } else { $health.error }
        }
        continue
    }

    if ($health.ok) {
        [pscustomobject]@{
            name = $svc.name
            local_port = $svc.local_port
            pid = Get-TrackedPid -PidPath $pidPath
            target = "$($svc.container):$($svc.target_port)"
            status = 'already_healthy'
        }
        continue
    }

    $null = Stop-TunnelByPort -PidPath $pidPath -Port ([int]$svc.local_port)
    $targetHost = Resolve-ContainerIp -HostName $hostName -UserName $userName -KeyPath $keyPath -SshPort $sshPort -ContainerName ([string]$svc.container)

    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if (-not $ssh) { throw 'ssh.exe is required for service tunnels.' }

    $forwardSpec = "127.0.0.1:{0}:{1}:{2}" -f $svc.local_port, $targetHost, $svc.target_port
    $sshArgs = @(
        '-o', 'BatchMode=yes',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3',
        '-o', 'StrictHostKeyChecking=yes',
        '-p', "$sshPort",
        '-i', $keyPath,
        '-N',
        '-L', $forwardSpec,
        "$userName@$hostName"
    )

    $proc = Start-Process -FilePath $ssh.Source `
        -ArgumentList $sshArgs `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    Write-PublicProcessReceipt -PidPath $pidPath -Process $proc

    $deadline = (Get-Date).AddSeconds(6)
    $serviceHealthy = $false
    do {
        Start-Sleep -Milliseconds 750
        $serviceHealthy = Test-TunnelHealth -Url ([string]$svc.health)
        if ($serviceHealthy.ok) {
            break
        }
        $running = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if (-not $running) {
            break
        }
    } while ((Get-Date) -lt $deadline)

    $running = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    $status = if ($serviceHealthy.ok) {
        'started'
    } elseif ($running) {
        'started_unverified'
    } else {
        'failed'
    }

    [pscustomobject]@{
        name = $svc.name
        local_port = $svc.local_port
        pid = $proc.Id
        target = "${targetHost}:$($svc.target_port)"
        status = $status
        health_detail = if ($serviceHealthy.ok) { 'ok' } elseif ($running) { 'listener started; upstream health not yet confirmed' } else { $serviceHealthy.error }
    }
}

$rows | ConvertTo-Json -Depth 6
exit 0
