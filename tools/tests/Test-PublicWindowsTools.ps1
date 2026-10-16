# Offline regression checks. No applications, services, or external endpoints start.
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $repo 'tools/PublicToolSafety.ps1')
$checks = 0
function Assert-True([bool]$Value, [string]$Message) {
    if (-not $Value) { throw $Message }
    $script:checks++
}
$value = @(Test-PublicToolApply -Tool 'synthetic' -Effects 'test')
Assert-True ($value.Count -eq 1 -and $value[0] -is [bool] -and -not $value[0]) 'Preview must return exactly one false boolean.'
Assert-True (Test-PublicToolApply -Tool 'synthetic' -Effects 'test' -Apply) 'Explicit apply must be recognized.'
Assert-True (-not (Test-PublicToolApply -Tool 'synthetic' -Effects 'test' -Apply -DryRun)) 'DryRun must override Apply.'
$allowedRoot = Join-Path ([IO.Path]::GetTempPath()) 'luminax-synthetic-root'
$goodPath = Join-Path $allowedRoot 'child/file.txt'
Assert-True ((Assert-PublicChildPath -Path $goodPath -Root $allowedRoot) -eq [IO.Path]::GetFullPath($goodPath)) 'Child path must resolve.'
foreach ($bad in @($allowedRoot, (Join-Path $allowedRoot '../outside.txt'), ($allowedRoot + '-sibling/file.txt'))) {
    $rejected = $false
    try { $null = Assert-PublicChildPath -Path $bad -Root $allowedRoot } catch { $rejected = $true }
    Assert-True $rejected 'Parent/root/sibling paths must be rejected.'
}
$started = [DateTime]::Parse('2024-01-01T00:00:00Z').ToUniversalTime()
$process = [pscustomobject]@{ Id = 42; StartTime = $started; Path = 'synthetic.exe' }
$receipt = [pscustomobject]@{ pid = 42; startedUtc = $started.ToString('o'); executable = 'synthetic.exe' }
Assert-True (Test-PublicProcessIdentity $receipt $process) 'Matching receipt must verify.'
$process.StartTime = $started.AddSeconds(1)
Assert-True (-not (Test-PublicProcessIdentity $receipt $process)) 'Reused PID must fail verification.'
$process.StartTime = $started; $process.Path = 'other.exe'
Assert-True (-not (Test-PublicProcessIdentity $receipt $process)) 'Changed executable must fail verification.'
Assert-True (-not (Test-PublicProcessIdentity $null $process)) 'Missing receipt must fail verification.'

$files = @(Get-ChildItem -LiteralPath $repo -File -Filter '*.ps1') +
    @(Get-ChildItem -LiteralPath (Join-Path $repo 'tools') -File -Filter '*.ps1') +
    @(Get-ChildItem -LiteralPath (Join-Path $repo 'tools/CodenameDarius/scripts') -File -Filter '*.ps1') +
    @(Get-ChildItem -LiteralPath (Join-Path $repo 'tools/EpochPortal/scripts') -File -Filter '*.ps1') +
    @((Get-Item -LiteralPath (Join-Path $repo 'payload/windows-model-bootstrap.ps1')),
      (Get-Item -LiteralPath (Join-Path $repo 'payload/windows-companion-launcher.ps1')))
foreach ($file in $files) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) ('PowerShell parsing failed: ' + $file.Name)
    if ($file.Name -eq 'PublicToolSafety.ps1') { continue }
    $argsMap = @{ DryRun = $true }
    foreach ($param in $ast.ParamBlock.Parameters) {
        if ($param.Extent.Text -notmatch 'Mandatory\s*=\s*\$true') { continue }
        $name = $param.Name.VariablePath.UserPath
        $argsMap[$name] = switch ($name) {
            'ServerBaseUrl' { 'http://127.0.0.1:1' }
            'ControlToken' { 'synthetic-token-for-offline-tests-only' }
            default { Join-Path $allowedRoot $name }
        }
    }
    # Redirect host output to verify every public operator entry point stops at its preview.
    $output = & $file.FullName @argsMap 6>&1 | Out-String
    Assert-True ($output -match 'PLAN:' -and $output -match 'No configuration was loaded') ('Missing preview boundary: ' + $file.Name)
}
Write-Host ("PASS: {0} offline Windows checks across {1} scripts." -f $checks, $files.Count)
