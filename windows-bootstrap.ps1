param(
    [string]$HostName,

    [string]$UserName,

    [string]$KeyPath = "",
    [int]$Port = 22,
    [string]$RemoteBaseDir = "/opt/luminax",
    [string]$LocalInstallerPath = ".\luminax.sh",
    [ValidateSet("bootstrap", "validate", "repair", "full", "fail-inject", "regression", "todo")]
    [string]$Mode = "full",
    [string]$Password = "",
    [switch]$NonInteractive,
    [switch]$NoBrowser
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-bootstrap.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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
$ControlToken = ''
if ($launcherEnv.ContainsKey('LUMINAX_CONTROL_TOKEN')) { $ControlToken = [string]$launcherEnv['LUMINAX_CONTROL_TOKEN'] }

if (-not $HostName) { throw "HostName is required. Pass -HostName or set LUMINAX_HOSTNAME in .env.windows." }
if (-not $UserName) { throw "UserName is required. Pass -UserName or set LUMINAX_USERNAME in .env.windows." }

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function New-OpenSshArgs {
    param([string]$UserHost, [int]$Port, [string]$KeyPath)
    $args = @("-p", "$Port", "-o", "StrictHostKeyChecking=yes")
    if ($KeyPath -ne "") {
        $args += @("-i", $KeyPath)
    }
    $args += $UserHost
    return ,$args
}

function Has-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Quote-ForSingleShell {
    param([string]$Value)
    if ($null -eq $Value) { return "''" }
    $replacement = "'" + '"' + "'" + '"' + "'"
    return "'" + ($Value.Replace("'", $replacement)) + "'"
}

function Wrap-WithSudo {
    param([string]$CommandText)
    if ($Password -ne "") {
        $pw = Quote-ForSingleShell -Value $Password
        return ("printf '%s\n' {0} | sudo -S -p '' bash -lc {1}" -f $pw, (Quote-ForSingleShell -Value $CommandText))
    }
    return "sudo bash -lc " + (Quote-ForSingleShell -Value $CommandText)
}

function Invoke-Remote {
    param(
        [string]$CommandText
    )
    $result = Invoke-RemoteResult -CommandText $CommandText
    Write-NativeOutput -Output $result.Output
    if ($result.ExitCode -ne 0) {
        throw "Remote command failed with exit code $($result.ExitCode)."
    }
    return $result.Output
}

function Write-NativeOutput {
    param([object[]]$Output)
    foreach ($entry in @($Output)) {
        if ($null -eq $entry) { continue }
        if ($entry -is [System.Management.Automation.ErrorRecord]) {
            Write-Output $entry.ToString()
        } else {
            Write-Output ([string]$entry)
        }
    }
}

function Invoke-RemoteResult {
    param(
        [string]$CommandText
    )
    $output = @()
    $savedErrorActionPreference = $script:ErrorActionPreference
    try {
        $script:ErrorActionPreference = 'Continue'
        if ($script:Backend -eq "plink") {
            $output = & plink @script:PlinkBase $CommandText 2>&1
            $exitCode = $LASTEXITCODE
        } else {
            $output = & ssh @script:SshBase $CommandText 2>&1
            $exitCode = $LASTEXITCODE
        }
    } finally {
        $script:ErrorActionPreference = $savedErrorActionPreference
    }
    return [pscustomobject]@{
        Output = @($output)
        ExitCode = [int]$exitCode
    }
}

function Copy-ToRemote {
    param([string]$LocalPath, [string]$RemotePath)
    $output = @()
    $savedErrorActionPreference = $script:ErrorActionPreference
    try {
        $script:ErrorActionPreference = 'Continue'
        if ($script:Backend -eq "plink") {
            $output = & pscp @script:PscpBase $LocalPath "$script:UserHost`:$RemotePath" 2>&1
            $exitCode = $LASTEXITCODE
        } else {
            $output = & scp @script:ScpBase $LocalPath "$script:UserHost`:$RemotePath" 2>&1
            $exitCode = $LASTEXITCODE
        }
    } finally {
        $script:ErrorActionPreference = $savedErrorActionPreference
    }
    foreach ($entry in @($output)) {
        if ($null -eq $entry) { continue }
        if ($entry -is [System.Management.Automation.ErrorRecord]) {
            Write-Output $entry.ToString()
        } else {
            Write-Output ([string]$entry)
        }
    }
    if ($exitCode -ne 0) {
        throw "Copy to remote failed with exit code $exitCode."
    }
}

function Copy-FromRemote {
    param([string]$RemotePath, [string]$LocalPath)
    $output = @()
    $savedErrorActionPreference = $script:ErrorActionPreference
    try {
        $script:ErrorActionPreference = 'Continue'
        if ($script:Backend -eq "plink") {
            $output = & pscp @script:PscpBase "$script:UserHost`:$RemotePath" $LocalPath 2>&1
            $exitCode = $LASTEXITCODE
        } else {
            $output = & scp @script:ScpBase "$script:UserHost`:$RemotePath" $LocalPath 2>&1
            $exitCode = $LASTEXITCODE
        }
    } finally {
        $script:ErrorActionPreference = $savedErrorActionPreference
    }
    foreach ($entry in @($output)) {
        if ($null -eq $entry) { continue }
        if ($entry -is [System.Management.Automation.ErrorRecord]) {
            Write-Output $entry.ToString()
        } else {
            Write-Output ([string]$entry)
        }
    }
    if ($exitCode -ne 0) {
        throw "Copy from remote failed with exit code $exitCode."
    }
}

function Test-RemotePath {
    param([string]$RemotePath)
    $quotedPath = Quote-ForSingleShell -Value $RemotePath
    $result = Invoke-RemoteResult -CommandText "test -f $quotedPath"
    return ($result.ExitCode -eq 0)
}

function Copy-FromRemoteIfPresent {
    param(
        [string]$RemotePath,
        [string]$LocalPath,
        [switch]$Required
    )

    if (-not (Test-RemotePath -RemotePath $RemotePath)) {
        if ($Required) {
            throw "Required remote file missing: $RemotePath"
        }
        Write-Warning "Optional remote file missing: $RemotePath"
        return $false
    }

    Copy-FromRemote -RemotePath $RemotePath -LocalPath $LocalPath
    return $true
}

if (-not (Test-Path $LocalInstallerPath)) {
    throw "Installer not found: $LocalInstallerPath"
}

$script:UserHost = "$UserName@$HostName"
$remoteStageDir = '$HOME/.luminax-stage'
$script:Backend = "openssh"
$useKeyTransport = $false
if ($KeyPath -ne "") {
    $useKeyTransport = Test-Path $KeyPath
    if (-not $useKeyTransport) {
        Write-Warning "SSH key path not found, falling back to password/OpenSSH selection: $KeyPath"
    }
}

if ($useKeyTransport) {
    Require-Command "ssh"
    Require-Command "scp"
    $script:SshBase = New-OpenSshArgs -UserHost $script:UserHost -Port $Port -KeyPath $KeyPath
    $script:ScpBase = @("-P", "$Port", "-o", "StrictHostKeyChecking=yes", "-i", $KeyPath)
} elseif ($Password -ne "") {
    if (Has-Command "plink" -and Has-Command "pscp") {
        $script:Backend = "plink"
        $script:PlinkBase = @("-P", "$Port", "-pw", $Password, $script:UserHost)
        $script:PscpBase = @("-P", "$Port", "-pw", $Password)
    } else {
        throw "Password auth requested, but plink/pscp not found. Install PuTTY tools or use key auth."
    }
} else {
    Require-Command "ssh"
    Require-Command "scp"
    $script:SshBase = New-OpenSshArgs -UserHost $script:UserHost -Port $Port -KeyPath $KeyPath
    $script:ScpBase = @("-P", "$Port", "-o", "StrictHostKeyChecking=yes")
    if ($KeyPath -ne "") {
        $script:ScpBase += @("-i", $KeyPath)
    }
}

if ($NonInteractive) {
    if ($script:Backend -eq "openssh") {
        $script:SshBase += @("-o", "BatchMode=yes")
    }
}

Write-Host "Backend: $script:Backend"
Write-Host ("Target : {0}:{1}" -f $script:UserHost, $RemoteBaseDir)

Write-Host "[1/8] Checking remote prerequisites..."
$checkCmd = @'
set -e
for c in bash docker curl openssl python3; do
  command -v "$c" >/dev/null 2>&1 || { echo "missing:$c"; exit 2; }
done
if docker compose version >/dev/null 2>&1 || docker-compose version >/dev/null 2>&1; then
  echo "compose:ok"
else
  echo "missing:compose"
  exit 2
fi
if [ -f /opt/luminax/.env ]; then
  OLLAMA_HOST_VALUE="$(grep '^OLLAMA_HOST=' /opt/luminax/.env 2>/dev/null | head -n 1 | cut -d= -f2- || true)"
  if [ -n "${OLLAMA_HOST_VALUE:-}" ]; then
    curl -fsS "${OLLAMA_HOST_VALUE}/api/tags" >/dev/null || { echo "ollama:unreachable"; exit 3; }
    echo "ollama:ok"
  fi
fi
echo "prereqs:ok"
'@
Invoke-Remote -CommandText $checkCmd

Write-Host "[2/8] Creating remote directories..."
Invoke-Remote -CommandText "mkdir -p '$remoteStageDir'"
Invoke-Remote -CommandText (Wrap-WithSudo -CommandText "mkdir -p '$RemoteBaseDir' '$RemoteBaseDir/logs'")

Write-Host "[3/8] Uploading installer..."
$remoteStageInstaller = "$remoteStageDir/luminax.sh"
$remoteInstaller = "$RemoteBaseDir/luminax.sh"
$normalizedInstaller = Join-Path $env:TEMP ("luminax-upload-{0}.sh" -f ([Guid]::NewGuid().ToString('N')))
$installerContent = Get-Content -Path $LocalInstallerPath -Raw
$installerContent = $installerContent -replace "`r`n", "`n"
$installerContent = $installerContent -replace "`r", "`n"
[System.IO.File]::WriteAllText($normalizedInstaller, $installerContent, (New-Object System.Text.UTF8Encoding($false)))
Copy-ToRemote -LocalPath $normalizedInstaller -RemotePath $remoteStageInstaller
Remove-Item -Path $normalizedInstaller -Force -ErrorAction SilentlyContinue

Write-Host "[4/8] Making installer executable..."
Invoke-Remote -CommandText (Wrap-WithSudo -CommandText "install -m 755 '$remoteStageInstaller' '$remoteInstaller'")

Write-Host "[5/8] Running remote mode: $Mode ..."
$remoteInstallEnv = @("BASE_DIR='$RemoteBaseDir'")
if ($ControlToken) {
    $remoteInstallEnv += "CONTROL_TOKEN_INPUT='$ControlToken'"
    $remoteInstallEnv += "LUMINAX_CONTROL_TOKEN='$ControlToken'"
}
$remoteInstallPrefix = ($remoteInstallEnv -join ' ')
$remoteInstallResult = Invoke-RemoteResult -CommandText (Wrap-WithSudo -CommandText "$remoteInstallPrefix bash '$remoteInstaller' '$Mode'")
Write-NativeOutput -Output $remoteInstallResult.Output
if ($remoteInstallResult.ExitCode -ne 0) {
    Write-Warning "Remote installer exited with code $($remoteInstallResult.ExitCode); exporting report artifacts before deciding whether the run is fatal."
}

Write-Host "[6/8] Pulling latest report artifacts..."
$localOut = Join-Path -Path (Get-Location) -ChildPath "reports"
if (-not (Test-Path $localOut)) {
    New-Item -ItemType Directory -Path $localOut | Out-Null
}
$exportReportsCmd = @"
set -e
mkdir -p '$remoteStageDir'
if [ -f '$RemoteBaseDir/logs/new/install_report.json' ]; then
  cp '$RemoteBaseDir/logs/new/install_report.json' '$remoteStageDir/install_report.json'
elif [ -f '$RemoteBaseDir/install_report.json' ]; then
  cp '$RemoteBaseDir/install_report.json' '$remoteStageDir/install_report.json'
fi
if [ -f '$RemoteBaseDir/logs/new/install_report.txt' ]; then
  cp '$RemoteBaseDir/logs/new/install_report.txt' '$remoteStageDir/install_report.txt'
elif [ -f '$RemoteBaseDir/install_report.txt' ]; then
  cp '$RemoteBaseDir/install_report.txt' '$remoteStageDir/install_report.txt'
fi
if [ -f '$RemoteBaseDir/control-token.txt' ]; then
  cp '$RemoteBaseDir/control-token.txt' '$remoteStageDir/control-token.txt'
  chmod 600 '$remoteStageDir/control-token.txt'
fi
chown '${UserName}:${UserName}' '$remoteStageDir'/* 2>/dev/null || true
"@
Invoke-Remote -CommandText (Wrap-WithSudo -CommandText $exportReportsCmd)
Copy-FromRemoteIfPresent -RemotePath "$remoteStageDir/install_report.json" -LocalPath "$localOut/install_report.json" -Required | Out-Null
Copy-FromRemoteIfPresent -RemotePath "$remoteStageDir/install_report.txt" -LocalPath "$localOut/install_report.txt" | Out-Null
Copy-FromRemoteIfPresent -RemotePath "$remoteStageDir/control-token.txt" -LocalPath "$localOut/control-token.txt" | Out-Null

$installReportPath = Join-Path $localOut 'install_report.json'
$installReport = $null
if (Test-Path $installReportPath) {
    try {
        $installReport = Get-Content -Path $installReportPath -Raw | ConvertFrom-Json
    } catch {
        Write-Warning "Unable to parse install_report.json: $($_.Exception.Message)"
    }
}
if ($remoteInstallResult.ExitCode -ne 0) {
    $reportUsable = $false
    $onlyWindowsNodePending = $false
    if ($null -ne $installReport) {
        $generationOk = [string]$installReport.generation_ok -eq 'true'
        $integrityOk = [string]$installReport.integrity_ok -eq 'true'
        $composeOk = [string]$installReport.compose_ok -eq 'true'
        $reportUsable = $generationOk -and $integrityOk -and $composeOk
        $failedSmokes = @()
        if ($null -ne $installReport.smoke) {
            foreach ($prop in $installReport.smoke.PSObject.Properties) {
                if ([string]$prop.Value -eq 'fail') {
                    $failedSmokes += [string]$prop.Name
                }
            }
        }
        $onlyWindowsNodePending = ($failedSmokes.Count -eq 1 -and $failedSmokes[0] -eq 'windows_node')
    }
    if (-not $reportUsable) {
        throw "Remote installer failed with exit code $($remoteInstallResult.ExitCode), and no usable green install report was exported."
    }
    if ($onlyWindowsNodePending) {
        Write-Warning "Remote install completed, but validation paused on the windows_node smoke before operator control/monitor follow-up. Continuing."
    } else {
        Write-Warning "Remote install completed with a non-green validation report. Continuing so Windows follow-up validation can finish the workflow."
    }
}

Write-Host "[7/8] Reading UI endpoint from remote .env ..."
$remoteEnvRead = Invoke-Remote -CommandText (Wrap-WithSudo -CommandText "grep '^PUBLIC_HOST=' '$RemoteBaseDir/.env' 2>/dev/null || true; grep '^PUBLIC_IP=' '$RemoteBaseDir/.env' 2>/dev/null || true; grep '^PUBLIC_PORT=' '$RemoteBaseDir/.env' 2>/dev/null || true; grep '^PUBLIC_HTTPS_PORT=' '$RemoteBaseDir/.env' 2>/dev/null || true")
$publicHost = "luminax"
$publicIp = $HostName
$publicPort = ""
$publicHttpsPort = ""
foreach ($line in $remoteEnvRead) {
    if ($line -like "PUBLIC_HOST=*") { $publicHost = $line.Split("=",2)[1] }
    if ($line -like "PUBLIC_IP=*") { $publicIp = $line.Split("=",2)[1] }
    if ($line -like "PUBLIC_PORT=*") { $publicPort = $line.Split("=",2)[1] }
    if ($line -like "PUBLIC_HTTPS_PORT=*") { $publicHttpsPort = $line.Split("=",2)[1] }
}

$candidateUrls = New-Object System.Collections.Generic.List[string]
foreach ($hostCandidate in @($publicIp, $HostName, $publicHost) | Select-Object -Unique) {
    if (-not $hostCandidate) { continue }
    foreach ($candidate in @(
        ('https://{0}:6969' -f $hostCandidate),
        ('https://{0}:8443' -f $hostCandidate),
        $(if ($publicHttpsPort) { 'https://{0}:{1}' -f $hostCandidate, $publicHttpsPort }),
        ('https://{0}:6971' -f $hostCandidate),
        ('https://{0}:8444' -f $hostCandidate),
        ('https://{0}:6970' -f $hostCandidate),
        ('https://{0}:8081' -f $hostCandidate)
        $(if ($publicPort) { 'https://{0}:{1}' -f $hostCandidate, $publicPort }),
        "https://$hostCandidate"
    )) {
        if ($candidate -and -not $candidateUrls.Contains($candidate)) {
            $candidateUrls.Add($candidate)
        }
    }
}
$selectedUrl = Select-ServerUrl -Candidates @($candidateUrls)
if (-not $selectedUrl) {
    $selectedUrl = 'https://{0}:6969' -f $publicIp
}

Write-Host "[8/8] Complete."
Write-Host "Remote run complete."
Write-Host "Report JSON: $localOut\install_report.json"
if (Test-Path (Join-Path $localOut 'install_report.txt')) {
    Write-Host "Report TXT : $localOut\install_report.txt"
} else {
    Write-Host "Report TXT : not exported"
}
Write-Host "Primary UI : $selectedUrl"
Write-Host "Fallback UI: https://$publicIp"

$installMonitor = Join-Path -Path $PSScriptRoot -ChildPath "install-windows-node-monitor.ps1"
if (Test-Path $installMonitor) {
    try {
        $controlTokenPath = Join-Path $localOut 'control-token.txt'
        if (Test-Path $controlTokenPath) {
            $controlToken = (Get-Content $controlTokenPath -Raw).Trim()
        } else {
            $controlToken = ''
        }
        if ($controlToken) {
            & $installMonitor -ServerBaseUrl $selectedUrl -ControlToken $controlToken -NodeKey "$env:COMPUTERNAME-$env:USERNAME" -IntervalSec 30
            Write-Host "Windows node monitor installed and running."
        } else {
            Write-Warning "control-token.txt empty; skipped monitor install."
        }
    } catch {
        Write-Warning "Failed to install windows node monitor: $($_.Exception.Message)"
    }
}

if (-not $NoBrowser) {
    try {
        Start-Process $selectedUrl
    } catch {
        Start-Process "https://$publicIp"
    }
}
