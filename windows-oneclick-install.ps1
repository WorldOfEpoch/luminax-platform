param(
    [switch]$Local,
    [string[]]$Profiles = @(),
    [ValidateSet('up', 'down', 'status')][string]$LocalAction = 'up',
    [string]$OpenSsl = '',
    [string]$HostName,

    [string]$UserName,

    [string]$KeyPath = "",
    [string]$Password = "",
    [int]$Port = 22,
    [string]$RemoteBaseDir = "/opt/luminax",

    [string]$InstallRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
    [string]$ModelDataRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
    [ValidateSet("voice-core", "full")]
    [string]$ModelMode = "full",
    [string]$NodeKey = "",
    [int]$MonitorIntervalSec = 30,

    [switch]$SkipPrereqInstall,
    [switch]$SkipModelBootstrap,
    [switch]$SkipServerInstall,
    [switch]$SkipMonitorInstall,
    [switch]$SkipEndpointValidation,
    [switch]$NoBrowser
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-oneclick-install.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Local) {
    if ($Profiles -notcontains 'core') { throw 'Select -Profiles core explicitly, including with optional profiles.' }
    foreach ($profile in $Profiles) {
        if ($profile -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]*$') { throw 'Invalid Compose profile name.' }
    }
    $payloadRoot = Join-Path $PSScriptRoot 'payload'
    $localEnv = Join-Path $payloadRoot '.env'
    $localAuth = Join-Path $payloadRoot 'runtime/luminax.htpasswd'
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install and start Docker with Compose deliberately before local setup.' }
    if (-not (Test-Path -LiteralPath $localEnv) -or -not (Test-Path -LiteralPath $localAuth)) {
        if ($LocalAction -ne 'up') { throw 'Initialize local configuration before this action.' }
        $pythonCommand = Get-Command python -ErrorAction Stop
        $initializeArgs = @((Join-Path $payloadRoot 'tools/initialize_local_config.py'))
        if ($OpenSsl) { $initializeArgs += @('--openssl', $OpenSsl) }
        & $pythonCommand.Source @initializeArgs
        if ($LASTEXITCODE -ne 0) { throw 'Local configuration initialization failed. Existing partial configuration is never overwritten automatically.' }
    }
    $composeArgs = @('compose', '--project-directory', $payloadRoot, '--env-file', $localEnv, '-f', (Join-Path $payloadRoot 'docker-compose.yml'))
    foreach ($profile in $Profiles) { $composeArgs += @('--profile', $profile) }
    switch ($LocalAction) {
        'up' { $composeArgs += @('up', '-d', '--build') }
        'down' { $composeArgs += @('down') }
        'status' { $composeArgs += @('ps') }
    }
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) { throw 'The selected local Compose operation failed.' }
    return
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
if (-not $Password -and $launcherEnv.ContainsKey('LUMINAX_SSH_PASSWORD')) { $Password = [string]$launcherEnv['LUMINAX_SSH_PASSWORD'] }
if ((-not $RemoteBaseDir -or $RemoteBaseDir -eq '/opt/luminax') -and $launcherEnv.ContainsKey('LUMINAX_REMOTE_BASE_DIR')) { $RemoteBaseDir = [string]$launcherEnv['LUMINAX_REMOTE_BASE_DIR'] }
if ((-not $InstallRoot -or $InstallRoot -eq (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax')) -and $launcherEnv.ContainsKey('LUMINAX_INSTALL_ROOT')) { $InstallRoot = [string]$launcherEnv['LUMINAX_INSTALL_ROOT'] }
if ((-not $ModelDataRoot -or $ModelDataRoot -eq (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax')) -and $launcherEnv.ContainsKey('LUMINAX_MODEL_DATA_ROOT')) { $ModelDataRoot = [string]$launcherEnv['LUMINAX_MODEL_DATA_ROOT'] }

$RepoRoot = $PSScriptRoot
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ReportRoot = Join-Path -Path $RepoRoot -ChildPath "reports\windows-oneclick\$RunStamp"
$RunLogPath = Join-Path -Path $ReportRoot -ChildPath "oneclick.log"
$SummaryJsonPath = Join-Path -Path $ReportRoot -ChildPath "oneclick-summary.json"
$SummaryTxtPath = Join-Path -Path $ReportRoot -ChildPath "oneclick-summary.txt"

New-Item -ItemType Directory -Path $ReportRoot -Force | Out-Null

if (-not $HostName) { throw "HostName is required. Pass -HostName or set LUMINAX_HOSTNAME in .env.windows." }
if (-not $UserName) { throw "UserName is required. Pass -UserName or set LUMINAX_USERNAME in .env.windows." }

function Write-Stage {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date).ToString("s"), $Message
    Write-Host $line -ForegroundColor Cyan
    $maxAttempts = 10
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            $stream = [System.IO.StreamWriter]::new($RunLogPath, $true, [System.Text.UTF8Encoding]::new($false))
            try {
                $stream.WriteLine($line)
            } finally {
                $stream.Dispose()
            }
            return
        } catch {
            if ($attempt -eq $maxAttempts) {
                return
            }
            Start-Sleep -Milliseconds (100 * $attempt)
        }
    }
}

function Cmd-Exists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Command {
    param([string]$Name, [string]$Hint = "")
    if (-not (Cmd-Exists $Name)) {
        if ($Hint) { throw "Missing command: $Name. $Hint" }
        throw "Missing command: $Name"
    }
}

function Ensure-WingetPackage {
    param([string]$PackageId)
    if ($SkipPrereqInstall) { return }
    if (-not (Cmd-Exists "winget")) { return }
    Write-Stage "Ensuring package via winget: $PackageId"
    winget install --id $PackageId --accept-source-agreements --accept-package-agreements --silent | Out-Null
}

function Enable-InsecureLocalTls {
    # Compatibility entry point: certificate verification stays enabled.
}


function Get-BasicAuthHeaders {
    if (-not $UserName -or -not $Password) {
        return @{}
    }
    $plain = '{0}:{1}' -f $UserName, $Password
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::ASCII.GetBytes($plain))
    return @{ Authorization = "Basic $encoded" }
}

function Invoke-JsonGet {
    param([string]$Uri, [int]$TimeoutSec = 20)
    $authHeaders = Get-BasicAuthHeaders
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        if ($authHeaders.Count -gt 0) {
            return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method Get -Headers $authHeaders -TimeoutSec $TimeoutSec
        }
        return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method Get -TimeoutSec $TimeoutSec
    }
    if ($authHeaders.Count -gt 0) {
        $plain = '{0}:{1}' -f $UserName, $Password
        $args = @(
            '--silent',
            '--show-error',
            '--http1.1',
            '--connect-timeout', '5',
            '--max-time', "$TimeoutSec",
            '--user', $plain,
            '--url', $Uri
        )
        $raw = (& curl.exe @args 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "curl.exe failed for ${Uri}: $raw"
        }
        return ($raw | ConvertFrom-Json)
    }
    return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method Get -TimeoutSec $TimeoutSec
}

function Test-Endpoint {
    param(
        [string]$BaseUrl,
        [string]$Path,
        [int]$Retries = 18,
        [int]$DelaySec = 5
    )
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        return [pscustomobject]@{
            ok = $false
            uri = $null
            attempt = 0
            data = $null
            error = "Base URL is empty."
        }
    }
    $uri = "$BaseUrl$Path"
    for ($i = 1; $i -le $Retries; $i++) {
        try {
            $resp = Invoke-JsonGet -Uri $uri -TimeoutSec 20
            return [pscustomobject]@{
                ok = $true
                uri = $uri
                attempt = $i
                data = $resp
                error = $null
            }
        } catch {
            $err = $_.Exception.Message
            Write-Stage "Endpoint probe failed ($i/$Retries): $uri :: $err"
            if ($i -lt $Retries) { Start-Sleep -Seconds $DelaySec }
        }
    }
    return [pscustomobject]@{
        ok = $false
        uri = $uri
        attempt = $Retries
        data = $null
        error = "No successful response after $Retries attempts."
    }
}

function Select-ServerUrl {
    param([string[]]$Candidates)
    foreach ($c in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        $parsed = $null
        if (-not [System.Uri]::TryCreate($c, [System.UriKind]::Absolute, [ref]$parsed)) { continue }
        if ([string]::IsNullOrWhiteSpace($parsed.Host)) { continue }
        $probe = Test-Endpoint -BaseUrl $c -Path "/api/health" -Retries 2 -DelaySec 2
        if ($probe.ok) {
            Write-Stage "Selected server URL: $c"
            return $c
        }
    }
    return $null
}

Write-Stage "Project Light one-click orchestrator started."
Write-Stage "Reports: $ReportRoot"

$windowsBootstrapScript = Join-Path -Path $RepoRoot -ChildPath "windows-bootstrap.ps1"
$windowsModelBootstrapScript = Join-Path -Path $RepoRoot -ChildPath "windows-model-bootstrap.ps1"
$installMonitorScript = Join-Path -Path $RepoRoot -ChildPath "install-windows-node-monitor.ps1"
$installerScript = Join-Path -Path $RepoRoot -ChildPath "luminax.sh"

foreach ($requiredPath in @($windowsBootstrapScript, $windowsModelBootstrapScript, $installMonitorScript, $installerScript)) {
    if (-not (Test-Path $requiredPath)) {
        throw "Required file missing: $requiredPath"
    }
}

if (-not (Test-Path $InstallRoot)) {
    Write-Stage "Creating install root: $InstallRoot"
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
}
if (-not (Test-Path $ModelDataRoot)) {
    Write-Stage "Creating model data root: $ModelDataRoot"
    New-Item -ItemType Directory -Path $ModelDataRoot -Force | Out-Null
}

Enable-InsecureLocalTls

$useKeyTransport = $false
if ($KeyPath) {
    $useKeyTransport = Test-Path $KeyPath
    if (-not $useKeyTransport) {
        Write-Stage "SSH key path not found; falling back to password/OpenSSH availability checks: $KeyPath"
    }
}

Write-Stage "Running local prerequisite checks."
if ($useKeyTransport) {
    Ensure-Command -Name "ssh" -Hint "OpenSSH client is required for key-based SSH."
    Ensure-Command -Name "scp" -Hint "OpenSSH client is required for key-based SSH."
} elseif ($Password) {
    if (-not (Cmd-Exists "plink") -or -not (Cmd-Exists "pscp")) {
        Ensure-WingetPackage -PackageId "PuTTY.PuTTY"
    }
    Ensure-Command -Name "plink" -Hint "Install PuTTY or use key-based SSH."
    Ensure-Command -Name "pscp" -Hint "Install PuTTY or use key-based SSH."
} else {
    if (-not (Cmd-Exists "ssh") -or -not (Cmd-Exists "scp")) {
        Ensure-WingetPackage -PackageId "Git.Git"
    }
    Ensure-Command -Name "ssh" -Hint "OpenSSH client is required."
    Ensure-Command -Name "scp" -Hint "OpenSSH client is required."
}
Ensure-Command -Name "powershell"

if (-not $SkipMonitorInstall) {
    Ensure-Command -Name "Register-ScheduledTask" -Hint "ScheduledTasks module is required for monitor install."
}

$summary = [ordered]@{
    started_at = (Get-Date).ToString("o")
    host = $HostName
    user = $UserName
    port = $Port
    remote_base_dir = $RemoteBaseDir
    install_root = $InstallRoot
    model_data_root = $ModelDataRoot
    model_mode = $ModelMode
    phases = [ordered]@{
        prereqs = "ok"
        model_bootstrap = "skipped"
        server_install = "skipped"
        monitor_install = "skipped"
        endpoint_validation = "skipped"
    }
    artifacts = [ordered]@{
        run_log = $RunLogPath
        bootstrap_report_json = $null
        bootstrap_report_txt = $null
        control_token_txt = $null
        remote_logs_new = "$RemoteBaseDir/logs/new"
        remote_logs_old = "$RemoteBaseDir/logs/old"
    }
    server_base_url = $null
    endpoint_results = @{}
    errors = @()
}

if (-not $SkipModelBootstrap) {
    Write-Stage "Phase: model bootstrap ($ModelMode)"
    try {
        & $windowsModelBootstrapScript -Mode $ModelMode -DataRoot $ModelDataRoot -PersistOllamaModelsPath -SkipWinget:$SkipPrereqInstall 2>&1 | Tee-Object -FilePath $RunLogPath -Append | Out-Null
        $summary.phases.model_bootstrap = "ok"
    } catch {
        $summary.phases.model_bootstrap = "failed"
        $summary.errors += "Model bootstrap failed: $($_.Exception.Message)"
        throw
    }
}

Push-Location $RepoRoot
try {
    if (-not $SkipServerInstall) {
        Write-Stage "Phase: server install (remote full mode)"
        $bootstrapParams = @{
            HostName = $HostName
            UserName = $UserName
            Port = $Port
            RemoteBaseDir = $RemoteBaseDir
            LocalInstallerPath = $installerScript
            Mode = "full"
            NoBrowser = $true
        }
        if ($KeyPath) { $bootstrapParams.KeyPath = $KeyPath }
        if (-not $useKeyTransport -and $Password) { $bootstrapParams.Password = $Password }
        try {
            & $windowsBootstrapScript @bootstrapParams 2>&1 | Tee-Object -FilePath $RunLogPath -Append | Out-Null
            $summary.phases.server_install = "ok"
        } catch {
            $summary.phases.server_install = "failed"
            $summary.errors += "Server install failed: $($_.Exception.Message)"
            throw
        }
    }
} finally {
    Pop-Location
}

$bootstrapReportDir = Join-Path -Path $RepoRoot -ChildPath "reports"
$installReportJson = Join-Path -Path $bootstrapReportDir -ChildPath "install_report.json"
$installReportTxt = Join-Path -Path $bootstrapReportDir -ChildPath "install_report.txt"
$controlTokenPath = Join-Path -Path $bootstrapReportDir -ChildPath "control-token.txt"

if (Test-Path $installReportJson) {
    $summary.artifacts.bootstrap_report_json = $installReportJson
    Copy-Item -Path $installReportJson -Destination (Join-Path $ReportRoot "install_report.json") -Force
}
if (Test-Path $installReportTxt) {
    $summary.artifacts.bootstrap_report_txt = $installReportTxt
    Copy-Item -Path $installReportTxt -Destination (Join-Path $ReportRoot "install_report.txt") -Force
}
if (Test-Path $controlTokenPath) {
    $summary.artifacts.control_token_txt = $controlTokenPath
    Copy-Item -Path $controlTokenPath -Destination (Join-Path $ReportRoot "control-token.txt") -Force
}

$candidateUrls = @(
    ('https://{0}:6969' -f $HostName),
    ('https://{0}:8443' -f $HostName),
    ('https://{0}:6971' -f $HostName),
    ('https://{0}:8444' -f $HostName),
    ('https://{0}:6970' -f $HostName),
    ('https://{0}:8081' -f $HostName),
    ('https://{0}' -f $HostName),
    "https://luminax:6969",
    "https://luminax:8443",
    "https://luminax:6971",
    "https://luminax:8444",
    "https://luminax:6970",
    "https://luminax:8081",
    "https://luminax"
) | Select-Object -Unique
$selectedUrl = Select-ServerUrl -Candidates $candidateUrls
if (-not $selectedUrl) {
    $selectedUrl = 'https://{0}:6969' -f $HostName
    Write-Stage "No immediate URL probe success; falling back to $selectedUrl."
}
$summary.server_base_url = $selectedUrl

if (-not $SkipMonitorInstall) {
    Write-Stage "Phase: install windows node monitor"
    if (-not (Test-Path $controlTokenPath)) {
        $summary.phases.monitor_install = "failed"
        $summary.errors += "Control token file missing; monitor install skipped."
    } else {
        try {
            $token = (Get-Content -Path $controlTokenPath -Raw).Trim()
            if (-not $NodeKey) { $NodeKey = "$env:COMPUTERNAME-$env:USERNAME" }
            & $installMonitorScript -ServerBaseUrl $selectedUrl -ControlToken $token -NodeKey $NodeKey -IntervalSec $MonitorIntervalSec -InstallRoot $InstallRoot 2>&1 | Tee-Object -FilePath $RunLogPath -Append | Out-Null
            $summary.phases.monitor_install = "ok"
        } catch {
            $summary.phases.monitor_install = "failed"
            $summary.errors += "Monitor install failed: $($_.Exception.Message)"
        }
    }
}

if (-not $SkipEndpointValidation) {
    Write-Stage "Phase: endpoint validation"
    $checks = [ordered]@{
        health_light = "/api/health/light"
        health = "/api/health"
        telemetry = "/admin/telemetry/latest"
        capabilities = "/api/capabilities"
        hive_status = "/api/hive/status"
        hive_debate_latest = "/api/hive/debate/latest"
        memory_recent = "/api/memory/mysql_recent?limit=5"
    }
    $allOk = $true
    foreach ($k in $checks.Keys) {
        $probe = Test-Endpoint -BaseUrl $selectedUrl -Path $checks[$k] -Retries 18 -DelaySec 5
        $summary.endpoint_results[$k] = @{
            ok = $probe.ok
            uri = $probe.uri
            attempt = $probe.attempt
            error = $probe.error
        }
        if (-not $probe.ok) { $allOk = $false }
    }
    $summary.phases.endpoint_validation = if ($allOk) { "ok" } else { "failed" }
}

$summary.completed_at = (Get-Date).ToString("o")
$summary.status = if ($summary.errors.Count -eq 0 -and $summary.phases.server_install -ne "failed") { "ok" } else { "partial" }

$summary | ConvertTo-Json -Depth 20 | Set-Content -Path $SummaryJsonPath -Encoding UTF8

$txt = @()
$txt += "Project Light One-Click Summary"
$txt += "Status: $($summary.status)"
$txt += "Started: $($summary.started_at)"
$txt += "Completed: $($summary.completed_at)"
$txt += "Server URL: $($summary.server_base_url)"
$txt += ""
$txt += "Phase results:"
$summary.phases.GetEnumerator() | ForEach-Object { $txt += ("- {0}: {1}" -f $_.Key, $_.Value) }
$txt += ""
$txt += "Artifacts:"
$summary.artifacts.GetEnumerator() | ForEach-Object { $txt += ("- {0}: {1}" -f $_.Key, $_.Value) }
$txt += ""
$txt += "Endpoint probes:"
$summary.endpoint_results.GetEnumerator() | ForEach-Object {
    $txt += ("- {0}: ok={1} uri={2} attempt={3} error={4}" -f $_.Key, $_.Value.ok, $_.Value.uri, $_.Value.attempt, $_.Value.error)
}
if ($summary.errors.Count -gt 0) {
    $txt += ""
    $txt += "Errors:"
    $summary.errors | ForEach-Object { $txt += "- $_" }
}
$txt | Set-Content -Path $SummaryTxtPath -Encoding UTF8

Write-Stage "Summary JSON: $SummaryJsonPath"
Write-Stage "Summary TXT : $SummaryTxtPath"
Write-Stage "Run log     : $RunLogPath"
Write-Host "Primary UI  : $selectedUrl" -ForegroundColor Green

if (-not $NoBrowser) {
    try { Start-Process $selectedUrl } catch {}
}

if ($summary.status -eq "ok") {
    exit 0
}
exit 2
