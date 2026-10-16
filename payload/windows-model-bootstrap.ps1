param(
  [string[]]$Models = @(),
  [string]$OptionalToolRepository = '',
  [switch]$RunProviderDiagnostics,
  [string]$Mode = "voice-core",
  [string]$OllamaHost = "http://127.0.0.1:11434",
  [string]$DataRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
  [string]$VenvName = ".venv-models",
  [switch]$PersistOllamaModelsPath,
  [switch]$SkipWinget,
  [switch]$Apply,
  [switch]$DryRun
)
. (Join-Path $PSScriptRoot '../tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'payload/windows-model-bootstrap.ps1' -Effects 'Install explicitly selected model/dependency tooling; inspect the source and select downloads before applying.' -Apply:$Apply -DryRun:$DryRun)) { return }

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "[windows-model-bootstrap] $msg" -ForegroundColor Cyan }
function Cmd-Exists($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Invoke-LoggedNativeCommand {
  param(
    [string]$Executable,
    [string[]]$Arguments,
    [string]$DisplayName
  )

  $savedErrorActionPreference = $script:ErrorActionPreference
  try {
    # Native installers like pip often write warnings to stderr even on success.
    $script:ErrorActionPreference = "Continue"
    $output = & $Executable @Arguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $script:ErrorActionPreference = $savedErrorActionPreference
  }

  foreach ($entry in @($output)) {
    if ($null -eq $entry) { continue }
    if ($entry -is [System.Management.Automation.ErrorRecord]) {
      Write-Output $entry.ToString()
      continue
    }
    Write-Output ([string]$entry)
  }

  if ($exitCode -ne 0) {
    throw "$DisplayName failed with exit code $exitCode."
  }
}

function Invoke-PipInstall {
  param(
    [string[]]$Packages,
    [switch]$UpgradePip
  )

  $args = @('-m', 'pip', 'install', '--disable-pip-version-check', '--prefer-binary')
  if ($UpgradePip) {
    $args += '--upgrade'
  }
  $args += $Packages

  $displayName = if ($UpgradePip) {
    'pip upgrade'
  } else {
    'pip install ' + ($Packages -join ' ')
  }

  Invoke-LoggedNativeCommand -Executable $python -Arguments $args -DisplayName $displayName
}

function Resolve-PythonLauncherPath([string]$VersionFlag) {
  if (-not (Cmd-Exists py)) { return $null }
  $output = & py $VersionFlag -c "import sys; print(sys.executable)" 2>$null
  if ($LASTEXITCODE -ne 0) { return $null }
  $path = (($output | Select-Object -Last 1) -as [string]).Trim()
  if (-not $path) { return $null }
  return $path
}

function Get-PythonMinorVersion([string]$ExecutablePath) {
  $output = & $ExecutablePath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
  if ($LASTEXITCODE -ne 0) { return '' }
  return (($output | Select-Object -Last 1) -as [string]).Trim()
}

function Get-PreferredPythonExecutable {
  foreach ($flag in @('-3.11', '-3.12', '-3.13')) {
    $path = Resolve-PythonLauncherPath -VersionFlag $flag
    if ($path -and (Test-Path $path)) {
      return $path
    }
  }
  if (Cmd-Exists python) {
    return (Get-Command python -ErrorAction Stop).Source
  }
  throw 'python not found in PATH and py launcher could not resolve a usable interpreter.'
}

function Get-DriveSummary([string]$DriveLetter) {
  try {
    $d = Get-PSDrive -Name $DriveLetter -ErrorAction Stop
    [pscustomobject]@{
      drive = $DriveLetter
      free_gb = [math]::Round($d.Free / 1GB, 2)
      used_gb = [math]::Round(($d.Used) / 1GB, 2)
      total_gb = [math]::Round(($d.Free + $d.Used) / 1GB, 2)
    }
  } catch {
    $null
  }
}
function Ensure-WingetPackage($id) {
  if ($SkipWinget) { return }
  Write-Step "Ensuring package: $id"
  winget install --id $id --accept-source-agreements --accept-package-agreements --silent | Out-Null
}

Write-Step "Starting bootstrap mode: $Mode"
$cDrive = Get-DriveSummary "C"
$dDrive = Get-DriveSummary "D"
if ($cDrive) { Write-Step ("C: free {0} GB / total {1} GB" -f $cDrive.free_gb, $cDrive.total_gb) }
if ($dDrive) { Write-Step ("D: free {0} GB / total {1} GB" -f $dDrive.free_gb, $dDrive.total_gb) }
if ($cDrive -and $cDrive.free_gb -lt 40) { Write-Warning "C: free space is low. Choose a data root with sufficient free space." }

if (-not $SkipWinget) {
  if (-not (Cmd-Exists winget)) { throw "winget is required unless -SkipWinget is set." }
  Ensure-WingetPackage "Git.Git"
  Ensure-WingetPackage "Python.Python.3.11"
  Ensure-WingetPackage "Gyan.FFmpeg"
}

if (-not (Cmd-Exists python)) { throw "python not found in PATH." }
if (-not (Cmd-Exists pip)) { throw "pip not found in PATH." }

$root = Join-Path $DataRoot "windows-models"
if (-not (Test-Path $root)) { New-Item -ItemType Directory -Path $root | Out-Null }
Set-Location $root

$preferredPython = Get-PreferredPythonExecutable
$preferredPythonVersion = Get-PythonMinorVersion -ExecutablePath $preferredPython
if ($preferredPythonVersion) {
  Write-Step "Preferred Python for model environment: $preferredPythonVersion ($preferredPython)"
}

if (-not (Test-Path $VenvName)) {
  Write-Step "Creating virtual environment"
  & $preferredPython -m venv $VenvName
}

$python = Join-Path $root "$VenvName\Scripts\python.exe"
$pip = Join-Path $root "$VenvName\Scripts\pip.exe"
$venvPythonVersion = Get-PythonMinorVersion -ExecutablePath $python
if ($venvPythonVersion) {
  Write-Step "Using venv Python $venvPythonVersion ($python)"
  if ([version]("$venvPythonVersion.0") -lt [version]'3.11.0') {
    Write-Warning "The existing model venv uses Python $venvPythonVersion. New environments will prefer Python 3.11+, but this venv will be reused to avoid a full reinstall."
  }
}

# Keep model/runtime caches under the explicitly selected data root.
$cacheRoot = Join-Path $root "cache"
$modelRoot = Join-Path $root "models"
$ollamaModelRoot = Join-Path $root "ollama-models"
foreach ($p in @($cacheRoot, $modelRoot, $ollamaModelRoot)) {
  if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}
$env:HF_HOME = Join-Path $cacheRoot "hf"
$env:TRANSFORMERS_CACHE = Join-Path $cacheRoot "transformers"
$env:TORCH_HOME = Join-Path $cacheRoot "torch"
$env:XDG_CACHE_HOME = Join-Path $cacheRoot "xdg"
$env:PIP_CACHE_DIR = Join-Path $cacheRoot "pip"
$env:TTS_HOME = Join-Path $modelRoot "tts"
$env:WHISPER_CACHE_DIR = Join-Path $modelRoot "whisper"
$env:OLLAMA_MODELS = $ollamaModelRoot
foreach ($p in @($env:HF_HOME,$env:TRANSFORMERS_CACHE,$env:TORCH_HOME,$env:XDG_CACHE_HOME,$env:PIP_CACHE_DIR,$env:TTS_HOME,$env:WHISPER_CACHE_DIR,$env:OLLAMA_MODELS)) {
  if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

if ($PersistOllamaModelsPath) {
  Write-Step "Persisting OLLAMA_MODELS for current user to $($env:OLLAMA_MODELS)"
  [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $env:OLLAMA_MODELS, "User")
}

Write-Step "Installing core python packages"
Invoke-PipInstall -Packages @('pip') -UpgradePip
Invoke-PipInstall -Packages @(
  'faster-whisper',
  'TTS',
  'sounddevice',
  'numpy',
  'scipy',
  'requests',
  'websockets'
)

if ($Mode -in @("voice-core", "full")) {
  Write-Step "Installing voice streaming stack (WhisperLive-compatible deps + XTTS support)"
  Invoke-PipInstall -Packages @('ctranslate2', 'onnxruntime', 'pydub')
}

if ($Mode -eq "full") {
  Write-Step "Installing orchestration stack"
  Invoke-PipInstall -Packages @('pipecat-ai', 'chromadb', 'pyyaml', 'mempalace')
}

if ($Models.Count -gt 0 -and (Cmd-Exists ollama)) {
  Write-Step "Ollama found; pulling explicitly selected models to OLLAMA_MODELS=$($env:OLLAMA_MODELS)"
  $selectedModels = $Models
  foreach ($m in $selectedModels) {
    try { ollama pull $m | Out-Null } catch { Write-Warning ("ollama pull failed for {0}: {1}" -f $m, $_.Exception.Message) }
  }
} else {
  Write-Warning "No model downloads requested or Ollama unavailable. Select -Models explicitly to download."
}

$clawRoot = Join-Path $root "tools\claw-code"
$clawExe = Join-Path $clawRoot "rust\target\debug\claw.exe"
if ($Mode -eq "full" -and $OptionalToolRepository) {
  if ((Cmd-Exists git) -and (Cmd-Exists cargo)) {
    Write-Step "Preparing claw-code orchestrator source tree"
    if (-not (Test-Path $clawRoot)) {
      git clone -- $OptionalToolRepository $clawRoot | Out-Null
      if ($LASTEXITCODE -ne 0) { throw 'Optional source clone failed.' }
    } else {
      throw 'The optional source checkout already exists; inspect and update it deliberately.'
    }
    $clawRustRoot = Join-Path $clawRoot "rust"
    if (Test-Path $clawRustRoot) {
      Push-Location $clawRustRoot
      try {
        cargo build --workspace
      } finally {
        Pop-Location
      }
      if ($RunProviderDiagnostics -and (Test-Path $clawExe) -and $env:ANTHROPIC_API_KEY) {
        try { & $clawExe doctor | Out-Null } catch { Write-Warning ("claw doctor failed: {0}" -f $_.Exception.Message) }
      } elseif (-not $env:ANTHROPIC_API_KEY) {
        Write-Warning "ANTHROPIC_API_KEY not set; skipping claw doctor verification."
      }
    }
  } else {
    Write-Warning "git and cargo are required to build claw-code from source."
  }
}

$capabilities = [ordered]@{
  generated_at = (Get-Date).ToString("s")
  mode = $Mode
  env_root = $root
  components = @{
    python = (python --version) -join " "
    ffmpeg = (Cmd-Exists ffmpeg)
    git = (Cmd-Exists git)
    cargo = (Cmd-Exists cargo)
    ollama = (Cmd-Exists ollama)
    ollama_models_path = $env:OLLAMA_MODELS
    mempalace = $Mode -eq "full"
    claw_code = (Test-Path $clawExe)
    claw_code_path = $clawExe
  }
  installed_skills = @(
    "streaming_asr",
    "streaming_tts",
    "voice_cloning",
    "voice_orchestration",
    "persistent_memory",
    "agentic_orchestration"
  )
  notes = "Feed this file to windows-node-monitor heartbeat payload if desired."
}

$capPath = Join-Path $root "windows-model-capabilities.json"
$capabilities | ConvertTo-Json -Depth 6 | Set-Content -Path $capPath -Encoding UTF8

Write-Step "Bootstrap completed."
Write-Step "Capability file: $capPath"
Write-Step "Activate venv: $root\\$VenvName\\Scripts\\Activate.ps1"
Write-Step "Data root: $root"
Write-Step "Cache root: $cacheRoot"
Write-Step "Model root: $modelRoot"
Write-Step "If you already have models on C:, run move-existing-models-to-d.ps1 from the Luminax repo."
