param(
    [string]$DataRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
    [string]$SourceOllamaModels = "$env:USERPROFILE\.ollama\models",
    [string]$TargetOllamaModels = "",
    [switch]$MoveAdditionalCaches,
    [switch]$CreateSourceJunction,
    [switch]$StartOllamaAfter
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'move-existing-models-to-d.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "[move-models] $Message" -ForegroundColor Cyan
}

function Ensure-Dir([string]$PathValue) {
    if (-not (Test-Path -LiteralPath $PathValue)) {
        New-Item -ItemType Directory -Path $PathValue -Force | Out-Null
    }
}

function Invoke-RobocopyMirror([string]$FromPath, [string]$ToPath) {
    Ensure-Dir $ToPath
    $logPath = Join-Path $DataRoot "logs\robocopy_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
    Ensure-Dir (Split-Path -Parent $logPath)
    Write-Step "Copying: $FromPath -> $ToPath"
    $args = @(
        "`"$FromPath`"",
        "`"$ToPath`"",
        "/E",
        "/COPY:DAT",
        "/DCOPY:DAT",
        "/R:2",
        "/W:2",
        "/NFL",
        "/NDL",
        "/NP",
        "/MT:16",
        "/LOG+:$logPath"
    )
    $proc = Start-Process -FilePath "robocopy.exe" -ArgumentList $args -NoNewWindow -PassThru -Wait
    if ($proc.ExitCode -gt 7) {
        throw "Robocopy failed from '$FromPath' to '$ToPath' (exit=$($proc.ExitCode)). See $logPath"
    }
}

function Stop-OllamaIfRunning {
    if (Get-Process -Name 'ollama' -ErrorAction SilentlyContinue) {
        throw 'Stop your Ollama instance deliberately before migrating its model files.'
    }
}

function Start-OllamaIfRequested {
    if (-not $StartOllamaAfter) { return }
    Write-Step "Starting Ollama service/process (best effort)"
    try { Start-Service -Name "Ollama" -ErrorAction SilentlyContinue } catch {}
}

if (-not $TargetOllamaModels) {
    $TargetOllamaModels = Join-Path $DataRoot "windows-models\ollama-models"
}

Ensure-Dir $DataRoot
Write-Step "Data root: $DataRoot"
Write-Step "Source Ollama models: $SourceOllamaModels"
Write-Step "Target Ollama models: $TargetOllamaModels"

if (-not (Test-Path -LiteralPath $SourceOllamaModels)) {
    Write-Warning "Source path not found: $SourceOllamaModels"
    Write-Warning "If models are already on D:, you can ignore this."
} else {
    Stop-OllamaIfRunning
    Invoke-RobocopyMirror -FromPath $SourceOllamaModels -ToPath $TargetOllamaModels

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupPath = "${SourceOllamaModels}.migrated_${timestamp}"
    Write-Step "Renaming source folder to backup: $backupPath"
    Rename-Item -LiteralPath $SourceOllamaModels -NewName (Split-Path -Leaf $backupPath)

    [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $TargetOllamaModels, "User")
    Write-Step "Persisted user env OLLAMA_MODELS=$TargetOllamaModels"

    if ($CreateSourceJunction) {
        $sourceParent = Split-Path -Parent $SourceOllamaModels
        Ensure-Dir $sourceParent
        Write-Step "Creating junction: $SourceOllamaModels -> $TargetOllamaModels"
        New-Item -ItemType Junction -Path $SourceOllamaModels -Target $TargetOllamaModels | Out-Null
    }
}

if ($MoveAdditionalCaches) {
    $moves = @(
        @{ from = "$env:USERPROFILE\.cache\huggingface"; to = Join-Path $DataRoot "windows-models\cache\huggingface" },
        @{ from = "$env:USERPROFILE\.cache\torch"; to = Join-Path $DataRoot "windows-models\cache\torch" },
        @{ from = "$env:LOCALAPPDATA\pip\Cache"; to = Join-Path $DataRoot "windows-models\cache\pip" }
    )
    foreach ($m in $moves) {
        if (-not (Test-Path -LiteralPath $m.from)) { continue }
        Invoke-RobocopyMirror -FromPath $m.from -ToPath $m.to
        Write-Step "Keeping source cache in place for safety: $($m.from)"
    }
}

Start-OllamaIfRequested
Write-Step "Migration pass complete."
Write-Step "Re-open terminals/apps to pick up updated OLLAMA_MODELS user environment."
