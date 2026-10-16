param(
    [string]$EnvPath = '',
    [string]$BootstrapScript = (Join-Path $PSScriptRoot '../windows-node-monitor.ps1'),
    [string]$LogPath = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax/companion/monitor.log'),
    [int]$HeartbeatSeconds = 30,
    [int]$PollSeconds = 10,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot '../tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'payload/windows-companion-launcher.ps1' -Effects 'Run the configured companion monitor in this session; no model installer or scheduled task is invoked.' -Apply:$Apply -DryRun:$DryRun)) { return }
$ErrorActionPreference = 'Stop'

function Import-DotEnv([string]$Path) {
    if (-not $Path) { return }
    if (-not (Test-Path -LiteralPath $Path)) { throw 'The explicitly selected environment file was not found.' }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith('#')) { continue }
        $parts = $line.Split('=', 2)
        if ($parts.Count -ne 2) { continue }
        $name = $parts[0].Trim()
        if ($name -notmatch '^(CONTROL_TOKEN|ADMIN_API_URL|EDGE_NODE_KEY|LUMINAX_CONTROL_TOKEN|LUMINAX_SERVER_BASE_URL|LUMINAX_NODE_KEY)$') { continue }
        [Environment]::SetEnvironmentVariable($name, $parts[1].Trim().Trim('"').Trim("'"), 'Process')
    }
}
Import-DotEnv -Path $EnvPath
if (-not (Test-Path -LiteralPath $BootstrapScript)) { throw 'The shared Windows monitor script is missing.' }
$controlToken = if ($env:LUMINAX_CONTROL_TOKEN) { $env:LUMINAX_CONTROL_TOKEN } else { $env:CONTROL_TOKEN }
$serverBaseUrl = if ($env:LUMINAX_SERVER_BASE_URL) { $env:LUMINAX_SERVER_BASE_URL } else { $env:ADMIN_API_URL }
$nodeKey = if ($env:LUMINAX_NODE_KEY) { $env:LUMINAX_NODE_KEY } else { $env:EDGE_NODE_KEY }
if (-not $controlToken -or $controlToken.Length -lt 32) { throw 'Configure a control token of at least 32 characters.' }
if (-not $serverBaseUrl -or -not $nodeKey) { throw 'Configure the server URL and an explicit non-personal node key.' }
$target = [Uri]$serverBaseUrl
if ($target.UserInfo -or ($target.Scheme -ne 'https' -and -not ($target.Scheme -eq 'http' -and $target.IsLoopback))) { throw 'Use HTTPS or loopback HTTP without URL credentials.' }
# The original wrapper passed monitor arguments into a model installer. Use the
# existing monitor owner and its supported parameters; do not create another loop.
& $BootstrapScript -Apply -ServerBaseUrl $serverBaseUrl -ControlToken $controlToken -NodeKey $nodeKey -IntervalSec ([Math]::Max(10, $HeartbeatSeconds)) -LogPath $LogPath
