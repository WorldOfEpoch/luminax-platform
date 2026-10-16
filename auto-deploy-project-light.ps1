param(
    [Parameter(Mandatory = $true)]
    [string]$HostName,
    [Parameter(Mandatory = $true)]
    [string]$UserName,
    [string]$KeyPath = "",
    [string]$Password = "",
    [int]$Port = 22,
    [string]$RemoteBaseDir = "/opt/luminax",
    [int]$Retries = 1
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'auto-deploy-project-light.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$launcher = Join-Path -Path $PSScriptRoot -ChildPath "windows-bootstrap.ps1"
if (-not (Test-Path $launcher)) {
    throw "Launcher not found: $launcher"
}

$guard = Join-Path -Path $PSScriptRoot -ChildPath "guard_luminax_size.sh"
if (Test-Path $guard) {
    try {
        bash $guard "$PSScriptRoot/luminax.sh"
    } catch {
        throw "Installer guard failed. Refusing deploy: $($_.Exception.Message)"
    }
}

for ($attempt = 0; $attempt -le $Retries; $attempt++) {
    Write-Host "Project Light deploy attempt $($attempt + 1) / $($Retries + 1)"
    try {
        $params = @{
            HostName      = $HostName
            UserName      = $UserName
            Port          = $Port
            RemoteBaseDir = $RemoteBaseDir
            Mode          = "todo"
        }
        if ($KeyPath) { $params.KeyPath = $KeyPath }
        if ($Password) { $params.Password = $Password }
        & $launcher @params
        Write-Host "Deployment completed successfully."
        exit 0
    } catch {
        Write-Warning "Attempt failed: $($_.Exception.Message)"
        if ($attempt -ge $Retries) {
            throw
        }
        Start-Sleep -Seconds 5
    }
}
