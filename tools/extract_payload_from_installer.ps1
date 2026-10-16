param(
  [string]$InstallerPath = '',
  [string]$OutputRoot = ''
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot '../tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'tools\extract_payload_from_installer.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $InstallerPath -or -not $OutputRoot) { throw 'Provide explicit InstallerPath and new OutputRoot.' }

if (-not (Test-Path -LiteralPath $InstallerPath)) {
  throw "Installer not found: $InstallerPath"
}

$lines = Get-Content -LiteralPath $InstallerPath
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
if (Test-Path -LiteralPath $OutputRoot) { throw 'OutputRoot must not exist; extraction never clears an existing directory.' }
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$rx = '^cat > "\$BASE_DIR/(.+)" <<''EOF''$'
$exports = 0
$idx = 0

while ($idx -lt $lines.Count) {
  $line = $lines[$idx]
  $m = [regex]::Match($line, $rx)
  if (-not $m.Success) {
    $idx++
    continue
  }

  $rel = $m.Groups[1].Value.Trim()
  if ([string]::IsNullOrWhiteSpace($rel)) {
    $idx++
    continue
  }

  $start = $idx + 1
  $end = $null
  for ($j = $start; $j -lt $lines.Count; $j++) {
    if ($lines[$j] -eq 'EOF') {
      $end = $j
      break
    }
  }
  if ($null -eq $end) {
    Write-Warning "Skipping unterminated block for $rel"
    $idx++
    continue
  }

  $payloadLines = @()
  if ($end -gt $start) {
    $payloadLines = $lines[$start..($end-1)]
  }

  $target = Assert-PublicChildPath -Root $OutputRoot -Path (Join-Path -Path $OutputRoot -ChildPath $rel)
  $targetDir = Split-Path -Path $target -Parent
  if ($targetDir) {
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
  }

  $payloadText = ""
  if ($payloadLines.Count -gt 0) {
    $payloadText = ($payloadLines -join "`n")
  }
  [System.IO.File]::WriteAllText($target, $payloadText, $utf8NoBom)

  $exports++
  $idx = $end + 1
}

Write-Host "[extract] exported $exports payload files to $OutputRoot"
