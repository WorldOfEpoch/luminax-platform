param(
    [ValidateSet('all','server-only','monitor-only','dashboard','status','files-list','file-read','file-write')]
    [string]$Mode = 'all',

    [string]$HostName = '',
    [string]$UserName = '',
    [string]$KeyPath = '',
    [string]$Password = '',
    [int]$Port = 22,
    [string]$RemoteBaseDir = '/opt/luminax',

    [string]$ServerBaseUrl = '',
    [string]$ControlToken = '',
    [string]$NodeKey = '',
    [ValidateSet('workspace','project')]
    [string]$FileRoot = 'workspace',
    [string]$RemotePath = '',
    [string]$LocalPath = '',
    [string]$SourcePath = '',

    [string]$InstallRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
    [string]$ModelDataRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
    [ValidateSet('voice-core','full')]
    [string]$ModelMode = 'full',
    [int]$MonitorIntervalSec = 30,

    [switch]$SkipModelBootstrap,
    [switch]$SkipPrereqInstall,
    [switch]$NoBrowser
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-controller.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


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

$launcherEnv = Read-LauncherEnv
if (-not $HostName -and $launcherEnv.ContainsKey('LUMINAX_HOSTNAME')) { $HostName = [string]$launcherEnv['LUMINAX_HOSTNAME'] }
if (-not $UserName -and $launcherEnv.ContainsKey('LUMINAX_USERNAME')) { $UserName = [string]$launcherEnv['LUMINAX_USERNAME'] }
if (-not $KeyPath -and $launcherEnv.ContainsKey('LUMINAX_KEY_PATH')) { $KeyPath = [string]$launcherEnv['LUMINAX_KEY_PATH'] }
if (-not $Password -and $launcherEnv.ContainsKey('LUMINAX_SSH_PASSWORD')) { $Password = [string]$launcherEnv['LUMINAX_SSH_PASSWORD'] }
if (-not $ServerBaseUrl -and $launcherEnv.ContainsKey('LUMINAX_SERVER_BASE_URL')) { $ServerBaseUrl = [string]$launcherEnv['LUMINAX_SERVER_BASE_URL'] }
if (-not $ControlToken -and $launcherEnv.ContainsKey('LUMINAX_CONTROL_TOKEN')) { $ControlToken = [string]$launcherEnv['LUMINAX_CONTROL_TOKEN'] }
if ((-not $RemoteBaseDir -or $RemoteBaseDir -eq '/opt/luminax') -and $launcherEnv.ContainsKey('LUMINAX_REMOTE_BASE_DIR')) { $RemoteBaseDir = [string]$launcherEnv['LUMINAX_REMOTE_BASE_DIR'] }
if ((-not $InstallRoot -or $InstallRoot -eq (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax')) -and $launcherEnv.ContainsKey('LUMINAX_INSTALL_ROOT')) { $InstallRoot = [string]$launcherEnv['LUMINAX_INSTALL_ROOT'] }
if ((-not $ModelDataRoot -or $ModelDataRoot -eq (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax')) -and $launcherEnv.ContainsKey('LUMINAX_MODEL_DATA_ROOT')) { $ModelDataRoot = [string]$launcherEnv['LUMINAX_MODEL_DATA_ROOT'] }

$RepoRoot = $PSScriptRoot
$OneClick = Join-Path $RepoRoot 'windows-oneclick-install.ps1'
$InstallMonitor = Join-Path $RepoRoot 'install-windows-node-monitor.ps1'
if (-not $ControlToken -and $env:LUMINAX_CONTROL_TOKEN) {
    $ControlToken = [string]$env:LUMINAX_CONTROL_TOKEN
}

function Write-Step {
    param([string]$Message)
    Write-Host ('[{0}] {1}' -f (Get-Date).ToString('s'), $Message) -ForegroundColor Cyan
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

function Invoke-CurlJson {
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Body = @{},
        [int]$TimeoutSec = 30
    )
    $args = @(
        '--silent',
        '--show-error',
        '--http1.1',
        '--connect-timeout', '8',
        '--max-time', "$TimeoutSec",
        '--request', $Method,
        '--url', $Uri
    )
    if ($UserName -and $Password) {
        $args += @('--user', ('{0}:{1}' -f $UserName, $Password))
    }
    if ($Method -ne 'GET') {
        $jsonBody = $Body | ConvertTo-Json -Depth 16 -Compress
        $args += @('--header', 'Content-Type: application/json', '--data', $jsonBody)
    }
    $rawLines = & curl.exe @args 2>&1
    $exitCode = $LASTEXITCODE
    $raw = (($rawLines | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
    if ($exitCode -ne 0) {
        throw "curl.exe failed with exit code $exitCode for ${Uri}: $raw"
    }
    if (-not $raw) { return $null }
    return ($raw | ConvertFrom-Json)
}

function Invoke-ControlGrant {
    param([string]$Token)
    $baseUrl = Resolve-BaseUrl
    if (-not $baseUrl) { throw 'ServerBaseUrl or HostName is required for control grant.' }
    $authHeaders = Get-BasicAuthHeaders
    try {
        $response = $null
        if ($PSVersionTable.PSVersion.Major -ge 7) {
            if ($authHeaders.Count -gt 0) {
                $response = Invoke-RestMethod -MaximumRedirection 0 -Uri "$baseUrl/api/grant_control" -Method Post -Headers $authHeaders -ContentType 'application/json' -Body (@{ token = $Token } | ConvertTo-Json -Depth 4) -TimeoutSec 20
            } else {
                $response = Invoke-RestMethod -MaximumRedirection 0 -Uri "$baseUrl/api/grant_control" -Method Post -ContentType 'application/json' -Body (@{ token = $Token } | ConvertTo-Json -Depth 4) -TimeoutSec 20
            }
        } else {
            $response = Invoke-CurlJson -Method 'POST' -Uri "$baseUrl/api/grant_control" -Body @{ token = $Token } -TimeoutSec 20
        }
        if ($null -ne $response -and (-not $response.PSObject.Properties['detail'])) {
            return $response
        }
        throw 'grant_control route returned an invalid response; falling back to redis grant.'
    } catch {
        $redisGrantCmd = "cd /opt/luminax && docker compose exec -T redis redis-cli SET luminax_control_granted true EX 600"
        $raw = Invoke-RemoteShell -Command $redisGrantCmd
        return [pscustomobject]@{
            status = 'granted_via_redis'
            seconds = 600
            detail = (($raw | Out-String).Trim())
        }
    }
}

function Try-StatusRequest {
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Body = @{},
        [int]$TimeoutSec = 25
    )
    $authHeaders = Get-BasicAuthHeaders
    try {
        if ($PSVersionTable.PSVersion.Major -ge 7) {
            if ($Method -eq 'GET') {
                if ($authHeaders.Count -gt 0) {
                    return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method Get -Headers $authHeaders -TimeoutSec $TimeoutSec
                }
                return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method Get -TimeoutSec $TimeoutSec
            }
            if ($authHeaders.Count -gt 0) {
                return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method $Method -Headers $authHeaders -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Depth 16) -TimeoutSec $TimeoutSec
            }
            return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method $Method -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Depth 16) -TimeoutSec $TimeoutSec
        }
        return Invoke-CurlJson -Method $Method -Uri $Uri -Body $Body -TimeoutSec $TimeoutSec
    } catch {
        return [pscustomobject]@{
            status = 'error'
            detail = $_.Exception.Message
            uri = $Uri
        }
    }
}

function Require-Path {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "Missing required script: $Path"
    }
}

function Invoke-RemoteShell {
    param([string]$Command)
    if (-not $HostName -or -not $UserName) {
        throw 'HostName and UserName are required for SSH fallback.'
    }
    if ($KeyPath) {
        $sshArgs = @(
            '-o', 'StrictHostKeyChecking=yes',
            '-p', "$Port",
            '-i', $KeyPath,
            "$UserName@$HostName",
            $Command
        )
        return (& ssh @sshArgs)
    }
    if ($Password) {
        if (-not (Get-Command plink -ErrorAction SilentlyContinue)) {
            throw 'PuTTY plink is required for password-based SSH fallback.'
        }
        $plinkArgs = @(
            '-batch',
            '-P', "$Port",
            '-pw', $Password,
            "$UserName@$HostName",
            $Command
        )
        return (& plink @plinkArgs)
    }
    throw 'No SSH transport configured for fallback.'
}

function Get-BaseUrlCandidates {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($ServerBaseUrl) {
        $candidates.Add($ServerBaseUrl.TrimEnd('/'))
    }
    if ($HostName) {
        foreach ($candidate in @(
            "https://$HostName",
            "https://$HostName:6969",
            "https://$HostName:8443",
            "https://$HostName:6971",
            "https://$HostName:8444",
            "https://$HostName:6970",
            "https://$HostName:8081"
        )) {
            if (-not $candidates.Contains($candidate)) {
                $candidates.Add($candidate)
            }
        }
    }
    return @($candidates)
}

function Test-BaseUrlCandidate {
    param([string]$BaseUrl)
    if (-not $BaseUrl) { return $false }
    $uri = "$($BaseUrl.TrimEnd('/'))/api/health"
    $authHeaders = Get-BasicAuthHeaders
    try {
        Enable-InsecureLocalTls
        if ($PSVersionTable.PSVersion.Major -ge 7) {
            if ($authHeaders.Count -gt 0) {
                $null = Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method Get -Headers $authHeaders -TimeoutSec 8
            } else {
                $null = Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method Get -TimeoutSec 8
            }
        } else {
            $null = Invoke-CurlJson -Method 'GET' -Uri $uri -TimeoutSec 8
        }
        return $true
    } catch {
        return $false
    }
}

function Resolve-BaseUrl {
    foreach ($candidate in Get-BaseUrlCandidates) {
        if (Test-BaseUrlCandidate -BaseUrl $candidate) {
            return $candidate.TrimEnd('/')
        }
    }
    if ($ServerBaseUrl) { return $ServerBaseUrl.TrimEnd('/') }
    if ($HostName) { return "https://$HostName:6969" }
    return ''
}

function Require-ControlToken {
    $token = [string]$ControlToken
    if (-not $token.Trim()) {
        throw 'ControlToken is required for this mode. Pass -ControlToken or set LUMINAX_CONTROL_TOKEN.'
    }
    return $token.Trim()
}

function Ensure-InstallIdentity {
    if (-not $HostName) { throw 'HostName is required for install modes.' }
    if (-not $UserName) { throw 'UserName is required for install modes.' }
}

function Run-OneClick {
    param([switch]$SkipMonitorInstall)
    Require-Path $OneClick
    Ensure-InstallIdentity

    $args = @{
        HostName = $HostName
        UserName = $UserName
        Port = $Port
        RemoteBaseDir = $RemoteBaseDir
        InstallRoot = $InstallRoot
        ModelDataRoot = $ModelDataRoot
        ModelMode = $ModelMode
        MonitorIntervalSec = $MonitorIntervalSec
        NoBrowser = $NoBrowser
        SkipModelBootstrap = $SkipModelBootstrap
        SkipPrereqInstall = $SkipPrereqInstall
        SkipMonitorInstall = $SkipMonitorInstall
    }
    if ($KeyPath) { $args.KeyPath = $KeyPath }
    if ($Password) { $args.Password = $Password }

    & $OneClick @args
}

function Run-MonitorInstall {
    Require-Path $InstallMonitor

    $baseUrl = Resolve-BaseUrl
    if (-not $baseUrl) { throw 'ServerBaseUrl or HostName is required for monitor-only mode.' }
    if (-not $ControlToken) { throw 'ControlToken is required for monitor-only mode.' }

    $args = @{
        ServerBaseUrl = $baseUrl
        ControlToken = $ControlToken
        IntervalSec = $MonitorIntervalSec
        InstallRoot = $InstallRoot
    }
    if ($NodeKey) { $args.NodeKey = $NodeKey }

    & $InstallMonitor @args
}

function Open-Dashboard {
    $baseUrl = Resolve-BaseUrl
    if (-not $baseUrl) { throw 'ServerBaseUrl or HostName is required for dashboard mode.' }
    Write-Step "Opening dashboard: $baseUrl"
    Start-Process $baseUrl | Out-Null
}

function Show-Status {
    $baseUrl = Resolve-BaseUrl
    if (-not $baseUrl) { throw 'ServerBaseUrl or HostName is required for status mode.' }
    $token = Require-ControlToken
    Enable-InsecureLocalTls

    try {
        $grant = Invoke-ControlGrant -Token $token
        $encToken = [System.Uri]::EscapeDataString($token)
        $health = Try-StatusRequest -Method 'GET' -Uri "$baseUrl/admin/telemetry/latest" -TimeoutSec 25
        $services = Try-StatusRequest -Method 'GET' -Uri "$baseUrl/admin/services/status" -TimeoutSec 25
        $nodes = Try-StatusRequest -Method 'GET' -Uri "$baseUrl/admin/windows-node/list?token=$encToken" -TimeoutSec 25

        Write-Host '=== Control Plane Health ===' -ForegroundColor Green
        $health | ConvertTo-Json -Depth 8
        Write-Host '=== Control Grant ===' -ForegroundColor Green
        $grant | ConvertTo-Json -Depth 6
        Write-Host '=== Services Status ===' -ForegroundColor Green
        $services | ConvertTo-Json -Depth 8
        Write-Host '=== Windows Node Link ===' -ForegroundColor Green
        $nodes | ConvertTo-Json -Depth 8
    } catch {
        throw "Status check failed: $($_.Exception.Message)"
    }
}

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Path,
        [hashtable]$Body = @{}
    )
    $baseUrl = Resolve-BaseUrl
    if (-not $baseUrl) { throw 'ServerBaseUrl or HostName is required for API modes.' }
    $uri = "$baseUrl$Path"
    $authHeaders = Get-BasicAuthHeaders
    Enable-InsecureLocalTls
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        if ($Method -eq 'GET') {
            if ($authHeaders.Count -gt 0) {
                return Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method Get -Headers $authHeaders -TimeoutSec 30
            }
            return Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method Get -TimeoutSec 30
        }
        if ($authHeaders.Count -gt 0) {
            return Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method $Method -Headers $authHeaders -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Depth 16) -TimeoutSec 30
        }
        return Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method $Method -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Depth 16) -TimeoutSec 30
    }
    return Invoke-CurlJson -Method $Method -Uri $uri -Body $Body -TimeoutSec 45
}

function Encode-UrlComponent {
    param([string]$Value)
    return [System.Uri]::EscapeDataString($Value)
}

function Get-ReadEndpoint {
    if ($FileRoot -eq 'workspace') { return '/api/workspace/read' }
    return '/api/project/read'
}

function Get-WriteEndpoint {
    if ($FileRoot -eq 'workspace') { return '/api/workspace/write' }
    return '/api/project/write'
}

function Run-FilesList {
    $token = Require-ControlToken
    $path = if ($RemotePath) { $RemotePath } else { '' }
    $endpoint = "/api/files/list?root=$FileRoot&path=$(Encode-UrlComponent -Value $path)&token=$(Encode-UrlComponent -Value $token)"
    $resp = Invoke-Api -Method 'GET' -Path $endpoint
    $resp | ConvertTo-Json -Depth 12
}

function Run-FileRead {
    $token = Require-ControlToken
    if (-not $RemotePath) { throw 'RemotePath is required for file-read mode.' }
    $endpoint = "$(Get-ReadEndpoint)?path=$(Encode-UrlComponent -Value $RemotePath)&token=$(Encode-UrlComponent -Value $token)"
    $resp = Invoke-Api -Method 'GET' -Path $endpoint
    $content = [string]($resp.content)
    if ($LocalPath) {
        $target = [System.IO.Path]::GetFullPath($LocalPath)
        $dir = Split-Path -Parent $target
        if ($dir -and -not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Set-Content -Path $target -Value $content -Encoding UTF8
        Write-Step "Saved ${FileRoot}:$RemotePath to $target"
    } else {
        $content
    }
}

function Run-FileWrite {
    $token = Require-ControlToken
    if (-not $RemotePath) { throw 'RemotePath is required for file-write mode.' }
    if (-not $SourcePath) { throw 'SourcePath is required for file-write mode.' }
    if (-not (Test-Path $SourcePath)) { throw "Source file not found: $SourcePath" }
    $sourceFull = [System.IO.Path]::GetFullPath($SourcePath)
    $content = Get-Content -Path $sourceFull -Raw -Encoding UTF8
    $endpoint = Get-WriteEndpoint
    $resp = Invoke-Api -Method 'POST' -Path $endpoint -Body @{
        token = $token
        path = $RemotePath
        content = $content
    }
    Write-Step "Updated ${FileRoot}:$RemotePath from $sourceFull"
    $resp | ConvertTo-Json -Depth 10
}

Write-Step "Windows controller mode: $Mode"

switch ($Mode) {
    'all' {
        Write-Step 'Running full path: install server + monitor + endpoint checks.'
        Run-OneClick
    }
    'server-only' {
        Write-Step 'Running server install path only (monitor install skipped).'
        Run-OneClick -SkipMonitorInstall
    }
    'monitor-only' {
        Write-Step 'Installing/starting persistent Windows monitor link only.'
        Run-MonitorInstall
    }
    'dashboard' {
        Open-Dashboard
    }
    'status' {
        Show-Status
    }
    'files-list' {
        Run-FilesList
    }
    'file-read' {
        Run-FileRead
    }
    'file-write' {
        Run-FileWrite
    }
}

Write-Step 'Windows controller completed.'
