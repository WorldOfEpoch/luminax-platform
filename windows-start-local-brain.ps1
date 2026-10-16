param(
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot '_local_runtime\pc-brain'),
    [string]$PythonExe = "",
    [switch]$RunBootstrapIfNeeded,
    [switch]$StartShell,
    [switch]$SkipShell
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-start-local-brain.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date).ToString('s'), $Message) -ForegroundColor Cyan
}

function Resolve-PythonPath {
    param([string]$RequestedPath)

    if ($RequestedPath -and (Test-Path $RequestedPath)) {
        return (Resolve-Path $RequestedPath).Path
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    $preferred = (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'windows-models\.venv-models\Scripts\python.exe')
    if (Test-Path $preferred) {
        return (Resolve-Path $preferred).Path
    }

    throw 'python.exe was not found. Run windows-model-bootstrap.ps1 first.'
}

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        $null = New-Item -ItemType Directory -Path $Path -Force
    }
}

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

function Wait-HttpHealth {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSec = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $probe = Test-HttpHealth -Url $Url -TimeoutSec 3
        if ($probe.ok) {
            return $probe.payload
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Name at $Url"
}

function ConvertTo-CompatHashtable {
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Collections.IDictionary]) {
        $table = @{}
        foreach ($key in $Value.Keys) {
            $table[[string]$key] = ConvertTo-CompatHashtable -Value $Value[$key]
        }
        return $table
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $table = @{}
        foreach ($prop in $Value.PSObject.Properties) {
            $table[$prop.Name] = ConvertTo-CompatHashtable -Value $prop.Value
        }
        return $table
    }
    if (($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string])) {
        $items = @()
        foreach ($item in $Value) {
            $items += ,(ConvertTo-CompatHashtable -Value $item)
        }
        return $items
    }
    return $Value
}

function ConvertFrom-JsonCompat {
    param([string]$Json)
    return ConvertTo-CompatHashtable -Value ($Json | ConvertFrom-Json)
}

function Read-EnvWindows {
    $envPath = Join-Path $PSScriptRoot '.env.windows'
    $map = @{}
    if (-not (Test-Path $envPath)) {
        return $map
    }
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

function Test-PythonModules {
    param(
        [string]$PythonPath,
        [string[]]$Modules
    )

    $quoted = ($Modules | ForEach-Object { "'$_'" }) -join ', '
    $script = @"
import importlib, sys
missing = []
for name in [$quoted]:
    try:
        importlib.import_module(name)
    except Exception:
        missing.append(name)
if missing:
    print(",".join(missing))
    sys.exit(1)
"@
    $temp = Join-Path $RuntimeRoot 'check_modules.py'
    $stdoutPath = Join-Path $RuntimeRoot 'check_modules.stdout.log'
    $stderrPath = Join-Path $RuntimeRoot 'check_modules.stderr.log'
    Set-Content -Path $temp -Value $script -Encoding UTF8
    try {
        $proc = Start-Process -FilePath $PythonPath `
            -ArgumentList @($temp) `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath
        return ($proc.ExitCode -eq 0)
    } finally {
        Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stdoutPath -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -ErrorAction SilentlyContinue
    }
}

function Build-EnvAssignments {
    param([hashtable]$EnvironmentMap)
    $parts = @()
    foreach ($key in $EnvironmentMap.Keys) {
        $value = [string]$EnvironmentMap[$key]
        $escaped = $value.Replace("'", "''")
        $parts += "`$env:$key = '$escaped'"
    }
    return ($parts -join '; ')
}

function Start-BackgroundService {
    param(
        [string]$Name,
        [string]$WorkingDirectory,
        [string]$PythonPath,
        [int]$Port,
        [string]$HealthPath,
        [hashtable]$EnvironmentMap = @{},
        [string]$BindHost = '127.0.0.1'
    )

    $healthUrl = "http://127.0.0.1:$Port$HealthPath"
    $existing = Test-HttpHealth -Url $healthUrl -TimeoutSec 2
    if ($existing.ok) {
        Write-Step "$Name already healthy at $healthUrl"
        return @{
            name = $Name
            port = $Port
            health = $healthUrl
            status = 'already_running'
        }
    }

    Ensure-Directory $RuntimeRoot
    $logsDir = Join-Path $RuntimeRoot 'logs'
    $pidsDir = Join-Path $RuntimeRoot 'pids'
    Ensure-Directory $logsDir
    Ensure-Directory $pidsDir

    $stdoutPath = Join-Path $logsDir "$Name.stdout.log"
    $stderrPath = Join-Path $logsDir "$Name.stderr.log"
    $pidPath = Join-Path $pidsDir "$Name.pid"
    $envAssignments = Build-EnvAssignments -EnvironmentMap $EnvironmentMap
    $command = @"
$envAssignments
Set-Location '$($WorkingDirectory.Replace("'", "''"))'
& '$($PythonPath.Replace("'", "''"))' -m uvicorn main:app --host $BindHost --port $Port
"@

    $process = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command) `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    Write-PublicProcessReceipt -PidPath $pidPath -Process $process
    Write-Step "Started $Name (PID $($process.Id))"
    $payload = Wait-HttpHealth -Name $Name -Url $healthUrl -TimeoutSec 45
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener -and $listener.OwningProcess) {
        $listenerProcess = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $listener.OwningProcess)
        if ($listenerProcess.ParentProcessId -ne $process.Id) { throw 'The listener is not a child of the process just launched; ownership was not transferred.' }
        Write-PublicProcessReceipt -PidPath $pidPath -Process (Get-Process -Id $listener.OwningProcess -ErrorAction Stop)
    }

    return @{
        name = $Name
        port = $Port
        health = $healthUrl
        pid = if ($listener -and $listener.OwningProcess) { $listener.OwningProcess } else { $process.Id }
        status = 'started'
        payload = $payload
    }
}

function Start-OllamaIfNeeded {
    $healthUrl = 'http://127.0.0.1:11434/api/tags'
    $probe = Test-HttpHealth -Url $healthUrl -TimeoutSec 2
    if ($probe.ok) {
        Write-Step 'Ollama already healthy on 127.0.0.1:11434'
        return @{ status = 'already_running'; health = $healthUrl }
    }

    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        Write-Step 'Ollama is not installed; local model server will remain unavailable.'
        return @{ status = 'missing' ; health = $healthUrl }
    }

    $logsDir = Join-Path $RuntimeRoot 'logs'
    $pidsDir = Join-Path $RuntimeRoot 'pids'
    Ensure-Directory $logsDir
    Ensure-Directory $pidsDir

    $process = Start-Process -FilePath $ollamaCmd.Source `
        -ArgumentList @('serve') `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir 'ollama.stdout.log') `
        -RedirectStandardError (Join-Path $logsDir 'ollama.stderr.log') `
        -PassThru

    Write-PublicProcessReceipt -PidPath (Join-Path $pidsDir 'ollama.pid') -Process $process
    Write-Step "Started Ollama (PID $($process.Id))"
    try {
        $payload = Wait-HttpHealth -Name 'ollama' -Url $healthUrl -TimeoutSec 30
        return @{ status = 'started'; health = $healthUrl; pid = $process.Id; payload = $payload }
    } catch {
        return @{ status = 'failed'; health = $healthUrl; pid = $process.Id; error = $_.Exception.Message }
    }
}

function Ensure-PrivateControlTunnel {
    param([hashtable]$EnvironmentMap)

    $scriptPath = Join-Path $PSScriptRoot 'windows-private-control-tunnel.ps1'
    if (-not (Test-Path $scriptPath)) {
        throw "Private control tunnel helper is missing: $scriptPath"
    }

    $args = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $scriptPath,
        '-Action', 'ensure',
        '-RuntimeRoot', $RuntimeRoot
    )

    if ($EnvironmentMap['LUMINAX_HOSTNAME']) {
        $args += @('-HostName', [string]$EnvironmentMap['LUMINAX_HOSTNAME'])
    }
    if ($EnvironmentMap['LUMINAX_USERNAME']) {
        $args += @('-UserName', [string]$EnvironmentMap['LUMINAX_USERNAME'])
    }
    if ($EnvironmentMap['LUMINAX_KEY_PATH']) {
        $args += @('-KeyPath', [string]$EnvironmentMap['LUMINAX_KEY_PATH'])
    }
    if ($EnvironmentMap['LUMINAX_SSH_PORT']) {
        $args += @('-SshPort', [string]$EnvironmentMap['LUMINAX_SSH_PORT'])
    }
    if ($EnvironmentMap['LUMINAX_TUNNEL_LOCAL_HOST']) {
        $args += @('-LocalHost', [string]$EnvironmentMap['LUMINAX_TUNNEL_LOCAL_HOST'])
    }
    if ($EnvironmentMap['LUMINAX_TUNNEL_LOCAL_PORT']) {
        $args += @('-LocalPort', [string]$EnvironmentMap['LUMINAX_TUNNEL_LOCAL_PORT'])
    }
    if ($EnvironmentMap['LUMINAX_TUNNEL_TARGET_HOST']) {
        $args += @('-TargetHost', [string]$EnvironmentMap['LUMINAX_TUNNEL_TARGET_HOST'])
    }
    if ($EnvironmentMap['LUMINAX_TUNNEL_TARGET_PORT']) {
        $args += @('-TargetPort', [string]$EnvironmentMap['LUMINAX_TUNNEL_TARGET_PORT'])
    }

    $raw = & powershell.exe @args 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to ensure private control tunnel: $raw"
    }
    if (-not $raw.Trim()) {
        return @{ status = 'unknown'; detail = 'No tunnel output returned.' }
    }
    return (ConvertFrom-JsonCompat -Json $raw)
}

function Ensure-LocalServiceTunnels {
    param()

    $scriptPath = Join-Path $PSScriptRoot 'windows-service-tunnels.ps1'
    if (-not (Test-Path $scriptPath)) {
        throw "Service tunnel helper is missing: $scriptPath"
    }

    $raw = & powershell.exe `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $scriptPath -Apply `
        -Action ensure `
        -RuntimeRoot $RuntimeRoot 2>&1 | Out-String

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to ensure service tunnels: $raw"
    }

    if (-not $raw.Trim()) {
        return @()
    }

    $parsed = ConvertFrom-JsonCompat -Json $raw
    if ($parsed -is [System.Collections.IEnumerable] -and -not ($parsed -is [string])) {
        return @($parsed)
    }

    return @($parsed)
}

function Start-DesktopShell {
    param([string]$ShellUrl)
    $shellDll = Join-Path $PSScriptRoot 'desktop-shell/bin/Release/net8.0-windows/Luminax.DesktopShell.dll'
    if (-not (Test-Path -LiteralPath $shellDll)) { throw 'Build desktop-shell in Release configuration first.' }
    $dotnet = Get-Command dotnet -ErrorAction Stop
    $pidsDir = Join-Path $RuntimeRoot 'pids'
    Ensure-Directory $pidsDir
    $pidPath = Join-Path $pidsDir 'desktop-shell.pid'
    $stopStatus = Stop-PublicRecordedProcess -PidPath $pidPath
    if ((Test-Path -LiteralPath $pidPath) -and $stopStatus -like 'unverified*') {
        throw 'The previous desktop process has no valid ownership receipt. Inspect it manually before restarting.'
    }
    $previousUrl = $env:LUMINAX_SHELL_URL
    $env:LUMINAX_SHELL_URL = $ShellUrl
    try {
        $process = Start-Process -FilePath $dotnet.Source -ArgumentList @('"' + $shellDll + '"') -PassThru
        Write-PublicProcessReceipt -PidPath $pidPath -Process $process
    } finally {
        $env:LUMINAX_SHELL_URL = $previousUrl
    }
    return @{ status = 'started'; pid = $process.Id }
}

$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
Ensure-Directory $RuntimeRoot
Ensure-Directory (Join-Path $RuntimeRoot 'logs')
Ensure-Directory (Join-Path $RuntimeRoot 'pids')
Ensure-Directory (Join-Path $RuntimeRoot 'memory')

$pythonPath = Resolve-PythonPath -RequestedPath $PythonExe
Write-Step "Using Python: $pythonPath"

$requiredModules = @('fastapi', 'uvicorn', 'httpx', 'edge_tts', 'pydantic', 'faster_whisper')
if (-not (Test-PythonModules -PythonPath $pythonPath -Modules $requiredModules)) {
    $fallbackPython = (Get-Command python -ErrorAction SilentlyContinue)
    if ($fallbackPython -and $fallbackPython.Source -and ($fallbackPython.Source -ne $pythonPath) -and (Test-PythonModules -PythonPath $fallbackPython.Source -Modules $requiredModules)) {
        $pythonPath = $fallbackPython.Source
        Write-Step "Falling back to host Python: $pythonPath"
    }
}

if (-not (Test-PythonModules -PythonPath $pythonPath -Modules $requiredModules)) {
    if (-not $RunBootstrapIfNeeded) {
        throw 'Required Python modules are missing. Re-run with -RunBootstrapIfNeeded or run windows-model-bootstrap.ps1 -Mode pc-brain.'
    }
    Write-Step 'Required Python modules were missing. Running windows-model-bootstrap.ps1 -Mode pc-brain -SkipWinget.'
    & (Join-Path $PSScriptRoot 'windows-model-bootstrap.ps1') -Apply -Mode 'pc-brain' -SkipWinget
    $pythonPath = Resolve-PythonPath -RequestedPath $PythonExe
    if (-not (Test-PythonModules -PythonPath $pythonPath -Modules $requiredModules)) {
        throw 'Python bootstrap completed but the required runtime modules are still unavailable.'
    }
}

$envWindows = Read-EnvWindows
$remoteBaseUrl = [string]($envWindows['LUMINAX_SERVER_BASE_URL'])
$privateControlUrl = [string]($envWindows['LUMINAX_PRIVATE_CONTROL_URL'])
if (-not $privateControlUrl) {
    $privateControlUrl = 'http://127.0.0.1:18040'
}
$tunnel = Ensure-PrivateControlTunnel -EnvironmentMap $envWindows
$tunnelStatus = if ($tunnel.ContainsKey('status')) { [string]$tunnel['status'] } else { 'unknown' }
Write-Step ("Private control tunnel status: {0}" -f $tunnelStatus)
$serviceTunnels = Ensure-LocalServiceTunnels
Write-Step ("Service tunnels ready: {0}" -f (($serviceTunnels | ForEach-Object { "{0}={1}" -f $_.name, $_.status }) -join ', '))

$services = @()
$services += Start-OllamaIfNeeded
$services += Start-BackgroundService -Name 'affect-controller' -WorkingDirectory (Join-Path $PSScriptRoot 'payload\affect-controller') -PythonPath $pythonPath -Port 8502 -HealthPath '/health'
$services += Start-BackgroundService -Name 'luminax-brain' -WorkingDirectory (Join-Path $PSScriptRoot 'payload\luminax-brain') -PythonPath $pythonPath -Port 8501 -HealthPath '/health' -EnvironmentMap @{
    LUMINAX_BRAIN_ROOT = (Join-Path $RuntimeRoot 'memory\luminax-brain')
}
$services += Start-BackgroundService -Name 'mcp-router' -WorkingDirectory (Join-Path $PSScriptRoot 'payload\mcp-router') -PythonPath $pythonPath -Port 8503 -HealthPath '/health' -EnvironmentMap @{
    LUMINAX_PROJECT_ROOT = (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'workspace')
    LUMINAX_PC_RUNTIME_ROOT = $RuntimeRoot
    LUMINAX_BRAIN_MEMORY_ROOT = (Join-Path $RuntimeRoot 'memory\luminax-brain')
    LUMINAX_BRAIN_URL = 'http://127.0.0.1:8501'
    ASSISTANT_CORE_URL = 'http://127.0.0.1:18000'
    LUMINAX_SERVER_PUBLIC_IP = if ($envWindows['LUMINAX_HOSTNAME']) { [string]$envWindows['LUMINAX_HOSTNAME'] } else { '' }
    LUMINAX_SERVER_SSH_USER = if ($envWindows['LUMINAX_USERNAME']) { [string]$envWindows['LUMINAX_USERNAME'] } else { '' }
    LUMINAX_SERVER_SSH_KEY = if ($envWindows['LUMINAX_KEY_PATH']) { [string]$envWindows['LUMINAX_KEY_PATH'] } else { (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'secure-keys\luminax_ed25519') }
    LUMINAX_CONTROL_TOKEN = if ($envWindows['LUMINAX_CONTROL_TOKEN']) { [string]$envWindows['LUMINAX_CONTROL_TOKEN'] } else { '' }
    CODEX_CLI_MODEL = 'gpt-5.5'
    CODEX_CLI_REASONING_EFFORT = 'xhigh'
}
$services += Start-BackgroundService -Name 'voice-agent' -WorkingDirectory (Join-Path $PSScriptRoot 'payload\voice-agent') -PythonPath $pythonPath -Port 8030 -HealthPath '/health' -EnvironmentMap @{
    AFFECT_CONTROLLER_URL = 'http://127.0.0.1:8502'
    VOICE_REF_ROOT = (Join-Path $RuntimeRoot 'voice_refs')
    WHISPER_MODEL = 'tiny.en'
}
$services += Start-BackgroundService -Name 'luminax-companion-ui' -WorkingDirectory (Join-Path $PSScriptRoot 'payload\luminax-companion-ui') -PythonPath $pythonPath -Port 8095 -HealthPath '/health' -EnvironmentMap @{
    LUMINAX_COMPANION_ASSISTANT_CORE_URL = 'http://127.0.0.1:18000'
    LUMINAX_COMPANION_ADMIN_API_URL = $privateControlUrl
    LUMINAX_COMPANION_REALTIME_URL = 'http://127.0.0.1:18050'
    LUMINAX_COMPANION_BROWSER_URL = 'http://127.0.0.1:18010'
    LUMINAX_COMPANION_RESEARCH_URL = 'http://127.0.0.1:18060'
    LUMINAX_COMPANION_SITE_URL = 'http://127.0.0.1:18070'
    LUMINAX_COMPANION_ACCOUNT_URL = 'http://127.0.0.1:18085'
    LUMINAX_COMPANION_NETWORK_URL = 'http://127.0.0.1:18090'
    LUMINAX_COMPANION_HIVE_DREAMER_URL = 'http://127.0.0.1:18101'
    LUMINAX_COMPANION_HIVE_ENGINEER_URL = 'http://127.0.0.1:18102'
    LUMINAX_COMPANION_HIVE_CRITIC_URL = 'http://127.0.0.1:18103'
    LUMINAX_COMPANION_VOICE_URL = 'http://127.0.0.1:8030'
    LUMINAX_COMPANION_EDGE_URL = 'http://127.0.0.1:8150'
    LUMINAX_COMPANION_BRAIN_URL = 'http://127.0.0.1:8501'
    LUMINAX_COMPANION_MCP_URL = 'http://127.0.0.1:8503'
    LUMINAX_COMPANION_OLLAMA_URL = 'http://127.0.0.1:11434'
    LUMINAX_COMPANION_FAST_MODEL = 'qwen2.5:14b'
    LUMINAX_COMPANION_FAST_ENABLED = 'false'
    LUMINAX_PROJECT_ROOT = (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'workspace')
    LUMINAX_PC_RUNTIME_ROOT = $RuntimeRoot
    LUMINAX_SERVER_PUBLIC_IP = if ($envWindows['LUMINAX_HOSTNAME']) { [string]$envWindows['LUMINAX_HOSTNAME'] } else { '' }
    LUMINAX_SERVER_SSH_USER = if ($envWindows['LUMINAX_USERNAME']) { [string]$envWindows['LUMINAX_USERNAME'] } else { '' }
    LUMINAX_SERVER_SSH_KEY = if ($envWindows['LUMINAX_KEY_PATH']) { [string]$envWindows['LUMINAX_KEY_PATH'] } else { (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'secure-keys\luminax_ed25519') }
    LUMINAX_COMPANION_CONTROL_TOKEN = if ($envWindows['LUMINAX_CONTROL_TOKEN']) { [string]$envWindows['LUMINAX_CONTROL_TOKEN'] } else { '' }
    LUMINAX_COMPANION_AUTO_EXECUTE_ACTIONS = 'false'
}
$services += Start-BackgroundService -Name 'olympus-edge-bridge' -WorkingDirectory (Join-Path $PSScriptRoot 'payload\olympus-edge-bridge') -PythonPath $pythonPath -Port 8150 -HealthPath '/health' -BindHost '127.0.0.1' -EnvironmentMap @{
    EDGE_LUMINAX_BRAIN_LOCAL_URL = 'http://127.0.0.1:8501'
    EDGE_AFFECT_CONTROLLER_LOCAL_URL = 'http://127.0.0.1:8502'
    EDGE_VOICE_URL = 'http://127.0.0.1:8030'
    OLLAMA_HOST = 'http://127.0.0.1:11434'
    EDGE_DIAGNOSTICS_PATH = (Join-Path $RuntimeRoot 'logs\olympus-edge-bridge-diagnostics.jsonl')
    ADMIN_API_URL = $privateControlUrl
    EDGE_CONTROL_ROUTE_MODE = 'ssh_tunnel'
    PUBLIC_APP_URL = $remoteBaseUrl
    CONTROL_TOKEN = ([string]($envWindows['LUMINAX_CONTROL_TOKEN']))
    EDGE_CONTROL_TOKEN_SOURCE = 'env_windows'
    EDGE_NODE_KEY = 'olympus-edge-bridge'
    EDGE_AUTHORITY_NODE_KEY = ([string]($envWindows['LUMINAX_NODE_KEY']))
}

$shell = $null
$shouldStartShell = $StartShell -and (-not $SkipShell)
if ($shouldStartShell) {
    $shellUrl = if ($envWindows['LUMINAX_LOCAL_UI_URL']) { [string]$envWindows['LUMINAX_LOCAL_UI_URL'] } else { 'http://127.0.0.1:8095/' }
    Write-Step "Starting desktop shell with URL $shellUrl"
    $shell = Start-DesktopShell -ShellUrl $shellUrl
}

$summary = [ordered]@{
    generated_at = (Get-Date).ToString('s')
    runtime_root = $RuntimeRoot
    python = $pythonPath
    private_control = @{
        mode = 'ssh_tunnel'
        url = $privateControlUrl
        tunnel = $tunnel
    }
    service_tunnels = $serviceTunnels
    services = $services
    shell = $shell
}

$summaryPath = Join-Path $RuntimeRoot 'last-start-summary.json'
$summary | ConvertTo-Json -Depth 8 | Set-Content -Path $summaryPath -Encoding UTF8
Write-Step "Local brain summary written to $summaryPath"
$summary | ConvertTo-Json -Depth 8
