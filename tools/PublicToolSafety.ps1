# Shared execution boundary for the existing Windows operator scripts.
# Dot-sourcing this file does not read settings, create files, or start services.
function Test-PublicToolApply {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Tool,
        [Parameter(Mandatory = $true)][string]$Effects,
        [switch]$Apply,
        [switch]$DryRun
    )

    if ($DryRun -or -not $Apply) {
        Write-Host ("PLAN: {0}" -f $Tool)
        Write-Host $Effects
        Write-Host 'No configuration was loaded and no action was performed. Review the script and rerun with -Apply to execute.'
        return $false
    }
    return $true
}

function Assert-PublicChildPath {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Root)
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    $prefix = $resolvedRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The target must be a child of the explicitly selected workspace.'
    }
    return $resolvedPath
}

function Write-PublicProcessReceipt {
    param([Parameter(Mandatory = $true)][string]$PidPath, [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
    # A numeric PID alone cannot establish ownership after process exit/reuse.
    $receipt = [ordered]@{
        pid = $Process.Id
        startedUtc = $Process.StartTime.ToUniversalTime().ToString('o')
        executable = $Process.Path
    }
    if (-not $receipt.executable) { throw 'Cannot record process ownership without its executable path.' }
    $receipt | ConvertTo-Json | Set-Content -LiteralPath ($PidPath + '.json') -Encoding UTF8
    Set-Content -LiteralPath $PidPath -Value $Process.Id -Encoding ASCII
}

function Test-PublicProcessIdentity {
    param($Receipt, $Process)
    if (-not $Receipt -or -not $Process) { return $false }
    try {
        return ([int]$Receipt.pid -eq $Process.Id -and
            [string]$Receipt.startedUtc -eq $Process.StartTime.ToUniversalTime().ToString('o') -and
            [string]::Equals([string]$Receipt.executable, [string]$Process.Path, [StringComparison]::OrdinalIgnoreCase))
    } catch { return $false }
}

function Stop-PublicRecordedProcess {
    param([Parameter(Mandatory = $true)][string]$PidPath)
    $receiptPath = $PidPath + '.json'
    if (-not (Test-Path -LiteralPath $receiptPath)) { return 'unverified_no_receipt' }
    try { $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json }
    catch { return 'unverified_invalid_receipt' }
    $process = Get-Process -Id ([int]$receipt.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return 'already_stopped' }
    if (-not (Test-PublicProcessIdentity -Receipt $receipt -Process $process)) { return 'unverified_identity_changed' }
    Stop-Process -InputObject $process -Force -ErrorAction Stop
    return 'stopped'
}
