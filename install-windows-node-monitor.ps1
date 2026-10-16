param(
    [Parameter(Mandatory = $true)]
    [string]$ServerBaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$ControlToken,
    [string]$NodeKey = "",
    [string]$HostName = "",
    [string]$UserName = "",
    [string]$KeyPath = "",
    [int]$SshPort = 22,
    [string]$PrivateControlUrl = "",
    [string]$TunnelLocalHost = "127.0.0.1",
    [int]$TunnelLocalPort = 18040,
    [string]$TunnelTargetHost = "127.0.0.1",
    [int]$TunnelTargetPort = 8040,
    [int]$IntervalSec = 30,
    [string]$TaskName = "ProjectLightWindowsNodeMonitor",
    [string]$InstallRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax')
,
    [switch]$StartMonitor,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'install-windows-node-monitor.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($ControlToken.Length -lt 32) { throw 'A control token of at least 32 characters is required.' }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'A task with this name already exists. Inspect it before replacing anything.' }

$sourceScript = Join-Path -Path $PSScriptRoot -ChildPath "windows-node-monitor.ps1"
if (-not (Test-Path $sourceScript)) {
    throw "Source monitor script missing: $sourceScript"
}
$tunnelScript = Join-Path -Path $PSScriptRoot -ChildPath "windows-private-control-tunnel.ps1"
if (-not (Test-Path $tunnelScript)) {
    throw "Tunnel helper script missing: $tunnelScript"
}

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

$launcherEnv = Read-LauncherEnv
if (-not $HostName -and $launcherEnv.ContainsKey('LUMINAX_HOSTNAME')) { $HostName = [string]$launcherEnv['LUMINAX_HOSTNAME'] }
if (-not $UserName -and $launcherEnv.ContainsKey('LUMINAX_USERNAME')) { $UserName = [string]$launcherEnv['LUMINAX_USERNAME'] }
if (-not $KeyPath -and $launcherEnv.ContainsKey('LUMINAX_KEY_PATH')) { $KeyPath = [string]$launcherEnv['LUMINAX_KEY_PATH'] }
if (($SshPort -eq 22) -and $launcherEnv.ContainsKey('LUMINAX_SSH_PORT')) {
    try { $SshPort = [int]$launcherEnv['LUMINAX_SSH_PORT'] } catch {}
}
if (-not $PrivateControlUrl -and $launcherEnv.ContainsKey('LUMINAX_PRIVATE_CONTROL_URL')) { $PrivateControlUrl = [string]$launcherEnv['LUMINAX_PRIVATE_CONTROL_URL'] }
if (($TunnelLocalHost -eq '127.0.0.1') -and $launcherEnv.ContainsKey('LUMINAX_TUNNEL_LOCAL_HOST')) { $TunnelLocalHost = [string]$launcherEnv['LUMINAX_TUNNEL_LOCAL_HOST'] }
if (($TunnelLocalPort -eq 18040) -and $launcherEnv.ContainsKey('LUMINAX_TUNNEL_LOCAL_PORT')) {
    try { $TunnelLocalPort = [int]$launcherEnv['LUMINAX_TUNNEL_LOCAL_PORT'] } catch {}
}
if (($TunnelTargetHost -eq '127.0.0.1') -and $launcherEnv.ContainsKey('LUMINAX_TUNNEL_TARGET_HOST')) { $TunnelTargetHost = [string]$launcherEnv['LUMINAX_TUNNEL_TARGET_HOST'] }
if (($TunnelTargetPort -eq 8040) -and $launcherEnv.ContainsKey('LUMINAX_TUNNEL_TARGET_PORT')) {
    try { $TunnelTargetPort = [int]$launcherEnv['LUMINAX_TUNNEL_TARGET_PORT'] } catch {}
}
if (-not $PrivateControlUrl) {
    $PrivateControlUrl = "http://${TunnelLocalHost}:${TunnelLocalPort}"
}

$installDir = Join-Path -Path $InstallRoot -ChildPath "ProjectLight"
if (-not (Test-Path $installDir)) {
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
}

$targetScript = Join-Path -Path $installDir -ChildPath "windows-node-monitor.ps1"
$targetTunnelScript = Join-Path -Path $installDir -ChildPath "windows-private-control-tunnel.ps1"
Copy-Item -Path $sourceScript -Destination $targetScript -Force
Copy-Item -Path $tunnelScript -Destination $targetTunnelScript -Force
$targetTools = Join-Path $installDir 'tools'
New-Item -ItemType Directory -Path $targetTools -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1') -Destination (Join-Path $targetTools 'PublicToolSafety.ps1') -Force

if (-not $NodeKey) {
    if ($env:LUMINAX_NODE_KEY) {
        $NodeKey = $env:LUMINAX_NODE_KEY
    } else {
        $NodeKey = "olympus-edge-gpu"
    }
}

[Environment]::SetEnvironmentVariable("LUMINAX_CONTROL_TOKEN", $ControlToken, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_SERVER_BASE_URL", $ServerBaseUrl, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_NODE_KEY", $NodeKey, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_HOSTNAME", $HostName, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_USERNAME", $UserName, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_KEY_PATH", $KeyPath, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_SSH_PORT", [string]$SshPort, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_PRIVATE_CONTROL_URL", $PrivateControlUrl, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_TUNNEL_LOCAL_HOST", $TunnelLocalHost, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_TUNNEL_LOCAL_PORT", [string]$TunnelLocalPort, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_TUNNEL_TARGET_HOST", $TunnelTargetHost, "User")
[Environment]::SetEnvironmentVariable("LUMINAX_TUNNEL_TARGET_PORT", [string]$TunnelTargetPort, "User")

$argList = @(
    "-NoProfile",
    "-WindowStyle", "Hidden",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$targetScript`"",
    "-Apply",
    "-ServerBaseUrl", "`"$ServerBaseUrl`"",
    "-NodeKey", "`"$NodeKey`"",
    "-IntervalSec", "$IntervalSec"
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argList
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Project Light Windows node monitor + local tool broker" -Force | Out-Null
if ($StartMonitor) { Start-ScheduledTask -TaskName $TaskName }

Write-Host "Installed $TaskName; start_now=$StartMonitor. The task runs at next sign-in."
Write-Host "Monitor script: $targetScript"
Write-Host "Tunnel helper: $targetTunnelScript"
