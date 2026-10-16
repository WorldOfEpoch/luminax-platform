param(
    [ValidateSet('app', 'install', 'server-only', 'monitor', 'dashboard', 'status', 'files-list', 'file-read', 'file-write')]
    [string]$Mode = 'app',
    [string]$HostName = '',
    [string]$UserName = '',
    [string]$KeyPath = '',
    [string]$Password = '',
    [int]$Port = 22,
    [string]$RemoteBaseDir = '/opt/luminax',
    [string]$ServerBaseUrl = '',
    [string]$ControlToken = '',
    [string]$NodeKey = '',
    [ValidateSet('workspace', 'project')]
    [string]$FileRoot = 'workspace',
    [string]$RemotePath = '',
    [string]$LocalPath = '',
    [string]$SourcePath = '',
    [string]$InstallRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
    [string]$ModelDataRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
    [ValidateSet('voice-core', 'full')]
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
if (-not (Test-PublicToolApply -Tool 'windows-program.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-LauncherEnv {
    param([string]$Path = (Join-Path $PSScriptRoot '.env.windows'))
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    foreach ($line in Get-Content -Path $Path) {
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

function Format-EnvValue {
    param([string]$Value)
    $text = [string]$Value
    if ($text -match '\s' -or $text.Contains('#')) {
        return '"' + $text.Replace('"', '\"') + '"'
    }
    return $text
}

function Write-LauncherEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$Map
    )
    $preferredOrder = @(
        'LUMINAX_HOSTNAME',
        'LUMINAX_USERNAME',
        'LUMINAX_KEY_PATH',
        'LUMINAX_SSH_PASSWORD',
        'LUMINAX_SSH_PORT',
        'LUMINAX_SERVER_BASE_URL',
        'LUMINAX_CONTROL_TOKEN',
        'LUMINAX_REMOTE_BASE_DIR',
        'LUMINAX_INSTALL_ROOT',
        'LUMINAX_MODEL_DATA_ROOT',
        'LUMINAX_NODE_KEY'
    )
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($key in $preferredOrder) {
        if ($Map.ContainsKey($key)) {
            $lines.Add(('{0}={1}' -f $key, (Format-EnvValue -Value ([string]$Map[$key]))))
        }
    }
    foreach ($key in ($Map.Keys | Sort-Object)) {
        if ($preferredOrder -contains $key) { continue }
        $lines.Add(('{0}={1}' -f $key, (Format-EnvValue -Value ([string]$Map[$key]))))
    }
    Set-Content -Path $Path -Value $lines -Encoding UTF8
}

function Get-ControllerPath {
    $controller = Join-Path $PSScriptRoot 'windows-controller.ps1'
    if (-not (Test-Path $controller)) {
        throw "Missing windows-controller.ps1 at $controller"
    }
    return $controller
}

function Get-WorkspaceRoot {
    return (Split-Path -Parent $PSScriptRoot)
}

function Enable-InsecureLocalTls {
    # Compatibility entry point: certificate verification stays enabled.
}


function Get-BasicAuthHeaders {
    param(
        [string]$UserName = '',
        [string]$Password = ''
    )
    if (-not $UserName -or -not $Password) {
        return @{}
    }
    $plain = '{0}:{1}' -f $UserName, $Password
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::ASCII.GetBytes($plain))
    return @{ Authorization = "Basic $encoded" }
}

function Invoke-CurlJsonGet {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSec = 12,
        [string]$UserName = '',
        [string]$Password = ''
    )
    $args = @(
        '--silent',
        '--show-error',
        '--http1.1',
        '--connect-timeout', '5',
        '--max-time', "$TimeoutSec",
        '--url', $Uri
    )
    if ($UserName -and $Password) {
        $args += @('--user', ('{0}:{1}' -f $UserName, $Password))
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

function Get-BaseUrlCandidates {
    param([hashtable]$State)
    $candidates = New-Object System.Collections.Generic.List[string]
    $explicit = [string]$State.ServerBaseUrl
    if ($explicit) {
        $candidates.Add($explicit.TrimEnd('/'))
    }
    $targetHost = [string]$State.HostName
    if ($targetHost) {
        foreach ($candidate in @(
            ('https://{0}:6969' -f $targetHost),
            ('https://{0}:8443' -f $targetHost),
            ('https://{0}:6971' -f $targetHost),
            ('https://{0}:8444' -f $targetHost),
            ('https://{0}:6970' -f $targetHost),
            ('https://{0}:8081' -f $targetHost),
            ('https://{0}' -f $targetHost)
        )) {
            if (-not $candidates.Contains($candidate)) {
                $candidates.Add($candidate)
            }
        }
    }
    return @($candidates)
}

function Test-LuminaxConnection {
    param([hashtable]$State)
    Enable-InsecureLocalTls
    $authHeaders = Get-BasicAuthHeaders -UserName ([string]$State.UserName) -Password ([string]$State.Password)
    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in Get-BaseUrlCandidates -State $State) {
        $uri = "$($candidate.TrimEnd('/'))/api/health"
        try {
            if ($PSVersionTable.PSVersion.Major -ge 7) {
                if ($authHeaders.Count -gt 0) {
                    $payload = Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method Get -Headers $authHeaders -TimeoutSec 10
                } else {
                    $payload = Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method Get -TimeoutSec 10
                }
            } else {
                $payload = Invoke-CurlJsonGet -Uri $uri -TimeoutSec 10 -UserName ([string]$State.UserName) -Password ([string]$State.Password)
            }
            return [pscustomobject]@{
                ok = $true
                base_url = $candidate.TrimEnd('/')
                uri = $uri
                payload = $payload
                error = ''
            }
        } catch {
            $errors.Add(('{0} :: {1}' -f $candidate, $_.Exception.Message))
        }
    }
    return [pscustomobject]@{
        ok = $false
        base_url = ''
        uri = ''
        payload = $null
        error = (($errors | Select-Object -First 6) -join '; ')
    }
}

function Get-ProjectRecords {
    $workspaceRoot = Get-WorkspaceRoot
    $records = @()
    foreach ($dir in Get-ChildItem -Path $workspaceRoot -Directory | Sort-Object Name) {
        $nameplatePath = Join-Path $dir.FullName 'nameplate.txt'
        if (-not (Test-Path $nameplatePath)) { continue }
        $feedbackLocal = Join-Path $dir.FullName 'Feedback'
        $feedbackWorkspace = Join-Path $workspaceRoot ("Feedback " + $dir.Name)
        $records += [pscustomobject]@{
            Name = $dir.Name
            Path = $dir.FullName
            NameplatePath = $nameplatePath
            DetailsPath = Join-Path $dir.FullName 'details.txt'
            FeedbackPath = if (Test-Path $feedbackLocal) { $feedbackLocal } else { $feedbackWorkspace }
        }
    }
    return $records
}

function Get-ProjectSummaryText {
    param([pscustomobject]$Project)
    if (-not $Project) { return 'No project selected.' }
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add(("Project: {0}" -f $Project.Name))
    $lines.Add(("Path: {0}" -f $Project.Path))
    if (Test-Path $Project.FeedbackPath) {
        $lines.Add(("Feedback: {0}" -f $Project.FeedbackPath))
    }
    $lines.Add('')
    if (Test-Path $Project.NameplatePath) {
        $lines.Add('Nameplate')
        $lines.Add('---------')
        foreach ($line in Get-Content -Path $Project.NameplatePath) {
            $lines.Add($line)
        }
    } else {
        $lines.Add('Nameplate missing.')
    }
    $lines.Add('')
    if (Test-Path $Project.DetailsPath) {
        $lines.Add('Recent details')
        $lines.Add('--------------')
        foreach ($line in (Get-Content -Path $Project.DetailsPath | Select-Object -Last 12)) {
            $lines.Add($line)
        }
    } else {
        $lines.Add('details.txt missing.')
    }
    return ($lines -join [Environment]::NewLine)
}

function Open-PathInCode {
    param([Parameter(Mandatory = $true)][string]$Path)
    $codeCommand = Get-Command code -ErrorAction SilentlyContinue
    if ($codeCommand) {
        & $codeCommand.Source $Path | Out-Null
        return $true
    }
    return $false
}

function Start-ProjectShell {
    param([Parameter(Mandatory = $true)][string]$Path)
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $Path
}

function Build-ControllerArgs {
    param(
        [Parameter(Mandatory = $true)][string]$ControllerMode,
        [Parameter(Mandatory = $true)][hashtable]$State
    )
    $args = @{
        Mode = $ControllerMode
        HostName = [string]$State.HostName
        UserName = [string]$State.UserName
        Port = [int]$(if ($null -ne $State.Port) { $State.Port } else { 22 })
        RemoteBaseDir = [string]$(if ($State.RemoteBaseDir) { $State.RemoteBaseDir } else { '/opt/luminax' })
        ServerBaseUrl = [string]$State.ServerBaseUrl
        ControlToken = [string]$State.ControlToken
        NodeKey = [string]$State.NodeKey
        FileRoot = [string]$(if ($State.FileRoot) { $State.FileRoot } else { 'workspace' })
        RemotePath = [string]$State.RemotePath
        LocalPath = [string]$State.LocalPath
        SourcePath = [string]$State.SourcePath
        InstallRoot = [string]$(if ($State.InstallRoot) { $State.InstallRoot } else { (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') })
        ModelDataRoot = [string]$(if ($State.ModelDataRoot) { $State.ModelDataRoot } else { (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') })
        ModelMode = [string]$(if ($State.ModelMode) { $State.ModelMode } else { 'full' })
        MonitorIntervalSec = [int]$(if ($null -ne $State.MonitorIntervalSec) { $State.MonitorIntervalSec } else { 30 })
    }
    if ([string]$State.KeyPath) { $args.KeyPath = [string]$State.KeyPath }
    if ([string]$State.Password) { $args.Password = [string]$State.Password }
    if ([bool]$State.SkipModelBootstrap) { $args.SkipModelBootstrap = $true }
    if ([bool]$State.SkipPrereqInstall) { $args.SkipPrereqInstall = $true }
    if ([bool]$State.NoBrowser) { $args.NoBrowser = $true }
    return $args
}

function Invoke-ControllerMode {
    param(
        [Parameter(Mandatory = $true)][string]$ControllerMode,
        [Parameter(Mandatory = $true)][hashtable]$State,
        [scriptblock]$OnOutput
    )
    $controller = Get-ControllerPath
    $controllerArgs = Build-ControllerArgs -ControllerMode $ControllerMode -State $State
    if ($OnOutput) {
        & $controller -Apply @controllerArgs *>&1 | ForEach-Object {
            $text = ($_ | Out-String).TrimEnd()
            if ($text) {
                & $OnOutput $text
            }
        }
        return
    }
    & $controller -Apply @controllerArgs
}

$launcherEnv = Read-LauncherEnv
if (-not $HostName -and $launcherEnv.ContainsKey('LUMINAX_HOSTNAME')) { $HostName = [string]$launcherEnv['LUMINAX_HOSTNAME'] }
if (-not $UserName -and $launcherEnv.ContainsKey('LUMINAX_USERNAME')) { $UserName = [string]$launcherEnv['LUMINAX_USERNAME'] }
if (-not $KeyPath -and $launcherEnv.ContainsKey('LUMINAX_KEY_PATH')) { $KeyPath = [string]$launcherEnv['LUMINAX_KEY_PATH'] }
if (-not $Password -and $launcherEnv.ContainsKey('LUMINAX_SSH_PASSWORD')) { $Password = [string]$launcherEnv['LUMINAX_SSH_PASSWORD'] }
if (-not $ServerBaseUrl -and $launcherEnv.ContainsKey('LUMINAX_SERVER_BASE_URL')) { $ServerBaseUrl = [string]$launcherEnv['LUMINAX_SERVER_BASE_URL'] }
if (-not $ControlToken -and $launcherEnv.ContainsKey('LUMINAX_CONTROL_TOKEN')) { $ControlToken = [string]$launcherEnv['LUMINAX_CONTROL_TOKEN'] }
if (-not $NodeKey -and $launcherEnv.ContainsKey('LUMINAX_NODE_KEY')) { $NodeKey = [string]$launcherEnv['LUMINAX_NODE_KEY'] }
if (($Port -eq 22) -and $launcherEnv.ContainsKey('LUMINAX_SSH_PORT')) {
    try { $Port = [int]$launcherEnv['LUMINAX_SSH_PORT'] } catch {}
}
if ((-not $RemoteBaseDir -or $RemoteBaseDir -eq '/opt/luminax') -and $launcherEnv.ContainsKey('LUMINAX_REMOTE_BASE_DIR')) { $RemoteBaseDir = [string]$launcherEnv['LUMINAX_REMOTE_BASE_DIR'] }
if ((-not $InstallRoot -or $InstallRoot -eq (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax')) -and $launcherEnv.ContainsKey('LUMINAX_INSTALL_ROOT')) { $InstallRoot = [string]$launcherEnv['LUMINAX_INSTALL_ROOT'] }
if ((-not $ModelDataRoot -or $ModelDataRoot -eq (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax')) -and $launcherEnv.ContainsKey('LUMINAX_MODEL_DATA_ROOT')) { $ModelDataRoot = [string]$launcherEnv['LUMINAX_MODEL_DATA_ROOT'] }

function Start-LuminaxWindowsApp {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $envPath = Join-Path $PSScriptRoot '.env.windows'
    $colors = @{
        Form = [System.Drawing.Color]::FromArgb(17, 23, 34)
        Panel = [System.Drawing.Color]::FromArgb(24, 31, 45)
        Button = [System.Drawing.Color]::FromArgb(48, 84, 150)
        Accent = [System.Drawing.Color]::FromArgb(85, 171, 255)
        Text = [System.Drawing.Color]::FromArgb(238, 242, 248)
        Subtle = [System.Drawing.Color]::FromArgb(172, 184, 201)
        Good = [System.Drawing.Color]::FromArgb(105, 198, 140)
        Warn = [System.Drawing.Color]::FromArgb(240, 192, 85)
        Bad = [System.Drawing.Color]::FromArgb(230, 100, 102)
        Input = [System.Drawing.Color]::FromArgb(12, 17, 27)
    }

    function New-UiLabel {
        param([string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height = 18)
        $label = New-Object System.Windows.Forms.Label
        $label.Text = $Text
        $label.SetBounds($X, $Y, $Width, $Height)
        $label.ForeColor = $colors.Text
        $label.BackColor = [System.Drawing.Color]::Transparent
        return $label
    }

    function New-UiTextBox {
        param(
            [int]$X,
            [int]$Y,
            [int]$Width,
            [string]$Text = '',
            [bool]$Multiline = $false,
            [bool]$ReadOnly = $false,
            [bool]$PasswordBox = $false
        )
        $box = New-Object System.Windows.Forms.TextBox
        $box.SetBounds($X, $Y, $Width, $(if ($Multiline) { 240 } else { 27 }))
        $box.Text = $Text
        $box.Multiline = $Multiline
        $box.ReadOnly = $ReadOnly
        $box.BackColor = $colors.Input
        $box.ForeColor = $colors.Text
        $box.BorderStyle = 'FixedSingle'
        if ($PasswordBox) { $box.UseSystemPasswordChar = $true }
        if ($Multiline) {
            $box.ScrollBars = 'Vertical'
            $box.Font = New-Object System.Drawing.Font('Consolas', 9)
        }
        return $box
    }

    function New-UiButton {
        param([string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height = 34)
        $button = New-Object System.Windows.Forms.Button
        $button.Text = $Text
        $button.SetBounds($X, $Y, $Width, $Height)
        $button.FlatStyle = 'Flat'
        $button.FlatAppearance.BorderColor = $colors.Accent
        $button.BackColor = $colors.Button
        $button.ForeColor = $colors.Text
        $button.Cursor = 'Hand'
        return $button
    }

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Mount Olympus Windows Operator'
    $form.StartPosition = 'CenterScreen'
    $form.Size = New-Object System.Drawing.Size(1280, 860)
    $form.MinimumSize = New-Object System.Drawing.Size(1180, 820)
    $form.BackColor = $colors.Form
    $form.ForeColor = $colors.Text
    $form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

    $header = New-Object System.Windows.Forms.Label
    $header.Text = 'Mount Olympus Windows Operator'
    $header.SetBounds(16, 10, 500, 28)
    $header.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 16)
    $header.ForeColor = $colors.Text
    $form.Controls.Add($header)

    $subHeader = New-Object System.Windows.Forms.Label
    $subHeader.Text = 'One script to deploy, connect, monitor, and jump straight into project work on Windows.'
    $subHeader.SetBounds(18, 38, 760, 20)
    $subHeader.ForeColor = $colors.Subtle
    $form.Controls.Add($subHeader)

    $connectionGroup = New-Object System.Windows.Forms.GroupBox
    $connectionGroup.Text = 'Connection + Install'
    $connectionGroup.SetBounds(12, 70, 1240, 220)
    $connectionGroup.ForeColor = $colors.Text
    $connectionGroup.BackColor = $colors.Panel
    $form.Controls.Add($connectionGroup)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Host' -X 16 -Y 28 -Width 100))
    $txtHost = New-UiTextBox -X 16 -Y 48 -Width 150 -Text $HostName
    $connectionGroup.Controls.Add($txtHost)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'User' -X 180 -Y 28 -Width 100))
    $txtUser = New-UiTextBox -X 180 -Y 48 -Width 150 -Text $UserName
    $connectionGroup.Controls.Add($txtUser)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'SSH Key Path' -X 344 -Y 28 -Width 180))
    $txtKey = New-UiTextBox -X 344 -Y 48 -Width 370 -Text $KeyPath
    $connectionGroup.Controls.Add($txtKey)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'SSH Password' -X 728 -Y 28 -Width 140))
    $txtPassword = New-UiTextBox -X 728 -Y 48 -Width 170 -Text $Password -PasswordBox $true
    $connectionGroup.Controls.Add($txtPassword)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'SSH Port' -X 912 -Y 28 -Width 90))
    $txtPort = New-UiTextBox -X 912 -Y 48 -Width 70 -Text ([string]$Port)
    $connectionGroup.Controls.Add($txtPort)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Remote Base Dir' -X 996 -Y 28 -Width 150))
    $txtRemoteBase = New-UiTextBox -X 996 -Y 48 -Width 220 -Text $RemoteBaseDir
    $connectionGroup.Controls.Add($txtRemoteBase)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Server Base URL' -X 16 -Y 84 -Width 150))
    $txtBaseUrl = New-UiTextBox -X 16 -Y 104 -Width 314 -Text $ServerBaseUrl
    $connectionGroup.Controls.Add($txtBaseUrl)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Control Token' -X 344 -Y 84 -Width 150))
    $txtToken = New-UiTextBox -X 344 -Y 104 -Width 314 -Text $ControlToken
    $connectionGroup.Controls.Add($txtToken)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Install Root' -X 672 -Y 84 -Width 150))
    $txtInstallRoot = New-UiTextBox -X 672 -Y 104 -Width 260 -Text $InstallRoot
    $connectionGroup.Controls.Add($txtInstallRoot)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Model Data Root' -X 946 -Y 84 -Width 150))
    $txtModelRoot = New-UiTextBox -X 946 -Y 104 -Width 270 -Text $ModelDataRoot
    $connectionGroup.Controls.Add($txtModelRoot)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Node Key' -X 16 -Y 140 -Width 120))
    $txtNodeKey = New-UiTextBox -X 16 -Y 160 -Width 220 -Text $NodeKey
    $connectionGroup.Controls.Add($txtNodeKey)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Model Mode' -X 250 -Y 140 -Width 120))
    $cmbModelMode = New-Object System.Windows.Forms.ComboBox
    $cmbModelMode.SetBounds(250, 160, 150, 28)
    $cmbModelMode.DropDownStyle = 'DropDownList'
    $cmbModelMode.BackColor = $colors.Input
    $cmbModelMode.ForeColor = $colors.Text
    [void]$cmbModelMode.Items.Add('full')
    [void]$cmbModelMode.Items.Add('voice-core')
    $cmbModelMode.SelectedItem = $ModelMode
    if (-not $cmbModelMode.SelectedItem) { $cmbModelMode.SelectedIndex = 0 }
    $connectionGroup.Controls.Add($cmbModelMode)

    $connectionGroup.Controls.Add((New-UiLabel -Text 'Monitor Interval (sec)' -X 414 -Y 140 -Width 160))
    $numInterval = New-Object System.Windows.Forms.NumericUpDown
    $numInterval.SetBounds(414, 160, 90, 28)
    $numInterval.Minimum = 10
    $numInterval.Maximum = 600
    $numInterval.Value = [Math]::Max(10, [Math]::Min(600, $MonitorIntervalSec))
    $numInterval.BackColor = $colors.Input
    $numInterval.ForeColor = $colors.Text
    $connectionGroup.Controls.Add($numInterval)

    $chkSkipModel = New-Object System.Windows.Forms.CheckBox
    $chkSkipModel.Text = 'Skip model bootstrap'
    $chkSkipModel.SetBounds(540, 160, 170, 24)
    $chkSkipModel.ForeColor = $colors.Text
    $chkSkipModel.Checked = [bool]$SkipModelBootstrap
    $connectionGroup.Controls.Add($chkSkipModel)

    $chkSkipPrereq = New-Object System.Windows.Forms.CheckBox
    $chkSkipPrereq.Text = 'Skip prerequisite install'
    $chkSkipPrereq.SetBounds(720, 160, 190, 24)
    $chkSkipPrereq.ForeColor = $colors.Text
    $chkSkipPrereq.Checked = [bool]$SkipPrereqInstall
    $connectionGroup.Controls.Add($chkSkipPrereq)

    $chkNoBrowser = New-Object System.Windows.Forms.CheckBox
    $chkNoBrowser.Text = 'Do not auto-open browser'
    $chkNoBrowser.SetBounds(920, 160, 210, 24)
    $chkNoBrowser.ForeColor = $colors.Text
    $chkNoBrowser.Checked = [bool]$NoBrowser -or ($Mode -eq 'app')
    $connectionGroup.Controls.Add($chkNoBrowser)

    $actionsGroup = New-Object System.Windows.Forms.GroupBox
    $actionsGroup.Text = 'Operator Actions'
    $actionsGroup.SetBounds(12, 300, 1240, 86)
    $actionsGroup.ForeColor = $colors.Text
    $actionsGroup.BackColor = $colors.Panel
    $form.Controls.Add($actionsGroup)

    $btnSave = New-UiButton -Text 'Save Settings' -X 18 -Y 30 -Width 130
    $btnTest = New-UiButton -Text 'Test Connection' -X 156 -Y 30 -Width 130
    $btnInstall = New-UiButton -Text 'Deploy + Connect' -X 294 -Y 30 -Width 140
    $btnServerOnly = New-UiButton -Text 'Server Only' -X 442 -Y 30 -Width 120
    $btnMonitor = New-UiButton -Text 'Install Monitor' -X 570 -Y 30 -Width 130
    $btnDashboard = New-UiButton -Text 'Open Dashboard' -X 708 -Y 30 -Width 130
    $btnStatus = New-UiButton -Text 'Show Status' -X 846 -Y 30 -Width 120
    $btnLogs = New-UiButton -Text 'Open Logs' -X 974 -Y 30 -Width 110
    $btnInstallFolder = New-UiButton -Text 'Install Folder' -X 1092 -Y 30 -Width 120
    foreach ($control in @($btnSave, $btnTest, $btnInstall, $btnServerOnly, $btnMonitor, $btnDashboard, $btnStatus, $btnLogs, $btnInstallFolder)) {
        $actionsGroup.Controls.Add($control)
    }

    $projectsGroup = New-Object System.Windows.Forms.GroupBox
    $projectsGroup.Text = 'Project Assist'
    $projectsGroup.SetBounds(12, 396, 560, 400)
    $projectsGroup.ForeColor = $colors.Text
    $projectsGroup.BackColor = $colors.Panel
    $form.Controls.Add($projectsGroup)

    $projectsGroup.Controls.Add((New-UiLabel -Text 'Project' -X 16 -Y 30 -Width 120))
    $cmbProjects = New-Object System.Windows.Forms.ComboBox
    $cmbProjects.SetBounds(16, 50, 250, 28)
    $cmbProjects.DropDownStyle = 'DropDownList'
    $cmbProjects.BackColor = $colors.Input
    $cmbProjects.ForeColor = $colors.Text
    $projectsGroup.Controls.Add($cmbProjects)

    $btnRefreshProjects = New-UiButton -Text 'Refresh' -X 278 -Y 48 -Width 88 -Height 30
    $btnOpenFolder = New-UiButton -Text 'Open Folder' -X 374 -Y 48 -Width 98 -Height 30
    $btnShell = New-UiButton -Text 'PowerShell Here' -X 16 -Y 88 -Width 130 -Height 30
    $btnCode = New-UiButton -Text 'Open In Code' -X 154 -Y 88 -Width 118 -Height 30
    $btnFeedback = New-UiButton -Text 'Feedback Folder' -X 280 -Y 88 -Width 124 -Height 30
    foreach ($control in @($btnRefreshProjects, $btnOpenFolder, $btnShell, $btnCode, $btnFeedback)) {
        $projectsGroup.Controls.Add($control)
    }

    $txtProjectSummary = New-UiTextBox -X 16 -Y 132 -Width 522 -Multiline $true -ReadOnly $true
    $projectsGroup.Controls.Add($txtProjectSummary)

    $outputGroup = New-Object System.Windows.Forms.GroupBox
    $outputGroup.Text = 'Run Output'
    $outputGroup.SetBounds(582, 396, 670, 400)
    $outputGroup.ForeColor = $colors.Text
    $outputGroup.BackColor = $colors.Panel
    $form.Controls.Add($outputGroup)

    $txtOutput = New-UiTextBox -X 16 -Y 28 -Width 638 -Multiline $true -ReadOnly $true
    $outputGroup.Controls.Add($txtOutput)

    $statusLabel = New-Object System.Windows.Forms.Label
    $statusLabel.Text = 'Ready.'
    $statusLabel.SetBounds(16, 802, 1230, 24)
    $statusLabel.ForeColor = $colors.Subtle
    $form.Controls.Add($statusLabel)

    $projectRecords = @()

    $appendLog = {
        param([string]$Line)
        if (-not $Line) { return }
        $timestamped = '[{0}] {1}' -f (Get-Date).ToString('HH:mm:ss'), $Line
        $txtOutput.AppendText($timestamped + [Environment]::NewLine)
        $txtOutput.SelectionStart = $txtOutput.Text.Length
        $txtOutput.ScrollToCaret()
        [System.Windows.Forms.Application]::DoEvents()
    }

    $setStatus = {
        param([string]$Text, [System.Drawing.Color]$Color)
        $statusLabel.Text = $Text
        $statusLabel.ForeColor = $Color
        [System.Windows.Forms.Application]::DoEvents()
    }

    $collectState = {
        $portValue = 22
        try { $portValue = [int]$txtPort.Text.Trim() } catch { $portValue = 22 }
        return @{
            HostName = $txtHost.Text.Trim()
            UserName = $txtUser.Text.Trim()
            KeyPath = $txtKey.Text.Trim()
            Password = $txtPassword.Text
            Port = $portValue
            RemoteBaseDir = $txtRemoteBase.Text.Trim()
            ServerBaseUrl = $txtBaseUrl.Text.Trim().TrimEnd('/')
            ControlToken = $txtToken.Text.Trim()
            NodeKey = $txtNodeKey.Text.Trim()
            FileRoot = $FileRoot
            RemotePath = $RemotePath
            LocalPath = $LocalPath
            SourcePath = $SourcePath
            InstallRoot = $txtInstallRoot.Text.Trim()
            ModelDataRoot = $txtModelRoot.Text.Trim()
            ModelMode = [string]$cmbModelMode.SelectedItem
            MonitorIntervalSec = [int]$numInterval.Value
            SkipModelBootstrap = $chkSkipModel.Checked
            SkipPrereqInstall = $chkSkipPrereq.Checked
            NoBrowser = $chkNoBrowser.Checked
        }
    }

    $saveSettings = {
        $state = & $collectState
        $map = Read-LauncherEnv -Path $envPath
        $map['LUMINAX_HOSTNAME'] = [string]$state.HostName
        $map['LUMINAX_USERNAME'] = [string]$state.UserName
        $map['LUMINAX_KEY_PATH'] = [string]$state.KeyPath
        $map['LUMINAX_SSH_PASSWORD'] = [string]$state.Password
        $map['LUMINAX_SSH_PORT'] = [string]$state.Port
        $map['LUMINAX_SERVER_BASE_URL'] = [string]$state.ServerBaseUrl
        $map['LUMINAX_CONTROL_TOKEN'] = [string]$state.ControlToken
        $map['LUMINAX_REMOTE_BASE_DIR'] = [string]$state.RemoteBaseDir
        $map['LUMINAX_INSTALL_ROOT'] = [string]$state.InstallRoot
        $map['LUMINAX_MODEL_DATA_ROOT'] = [string]$state.ModelDataRoot
        $map['LUMINAX_NODE_KEY'] = [string]$state.NodeKey
        Write-LauncherEnv -Path $envPath -Map $map
        & $appendLog "Saved launcher settings to $envPath"
        & $setStatus 'Saved launcher settings.' $colors.Good
    }

    $selectedProject = {
        $name = [string]$cmbProjects.SelectedItem
        if (-not $name) { return $null }
        return ($projectRecords | Where-Object { $_.Name -eq $name } | Select-Object -First 1)
    }

    $refreshProjects = {
        $projectRecords = Get-ProjectRecords
        $cmbProjects.Items.Clear()
        foreach ($record in $projectRecords) {
            [void]$cmbProjects.Items.Add($record.Name)
        }
        if ($cmbProjects.Items.Count -gt 0 -and $cmbProjects.SelectedIndex -lt 0) {
            $cmbProjects.SelectedIndex = 0
        }
        & $appendLog ("Loaded {0} project folders." -f $projectRecords.Count)
    }

    $showProjectSummary = {
        $project = & $selectedProject
        $txtProjectSummary.Text = Get-ProjectSummaryText -Project $project
    }

    $runControllerAction = {
        param([string]$ControllerMode, [string]$Label)
        try {
            & $saveSettings
            & $appendLog ("Starting {0}..." -f $Label)
            & $setStatus ("Running {0}..." -f $Label) $colors.Warn
            $state = & $collectState
            Invoke-ControllerMode -ControllerMode $ControllerMode -State $state -OnOutput $appendLog
            $probe = Test-LuminaxConnection -State $state
            if ($probe.ok) {
                $txtBaseUrl.Text = $probe.base_url
                & $appendLog ("Healthy Mount Olympus endpoint: {0}" -f $probe.base_url)
                & $saveSettings
            }
            & $setStatus ("Completed {0}." -f $Label) $colors.Good
        } catch {
            & $appendLog ("ERROR during {0}: {1}" -f $Label, $_.Exception.Message)
            & $setStatus ("Failed {0}." -f $Label) $colors.Bad
        }
    }

    $btnSave.Add_Click({
        try { & $saveSettings } catch { & $appendLog ("ERROR saving settings: " + $_.Exception.Message) }
    })

    $btnTest.Add_Click({
        try {
            & $saveSettings
            & $appendLog 'Testing connection candidates...'
            $probe = Test-LuminaxConnection -State (& $collectState)
            if ($probe.ok) {
                $txtBaseUrl.Text = $probe.base_url
                & $appendLog ("Connection OK: {0}" -f $probe.uri)
                if ($probe.payload) {
                    & $appendLog ("Health payload: " + (($probe.payload | ConvertTo-Json -Depth 8 -Compress)))
                }
                & $saveSettings
                & $setStatus 'Connection healthy.' $colors.Good
            } else {
                & $appendLog ("Connection failed: {0}" -f $probe.error)
                & $setStatus 'Connection failed.' $colors.Bad
            }
        } catch {
            & $appendLog ("ERROR testing connection: " + $_.Exception.Message)
            & $setStatus 'Connection test failed.' $colors.Bad
        }
    })

    $btnInstall.Add_Click({ & $runControllerAction 'all' 'deploy + connect' })
    $btnServerOnly.Add_Click({ & $runControllerAction 'server-only' 'server-only deploy' })
    $btnMonitor.Add_Click({ & $runControllerAction 'monitor-only' 'monitor install' })
    $btnDashboard.Add_Click({ & $runControllerAction 'dashboard' 'dashboard open' })
    $btnStatus.Add_Click({ & $runControllerAction 'status' 'status check' })

    $btnLogs.Add_Click({
        $reportsPath = Join-Path $PSScriptRoot 'reports'
        if (-not (Test-Path $reportsPath)) {
            New-Item -ItemType Directory -Path $reportsPath -Force | Out-Null
        }
        Start-Process explorer.exe $reportsPath | Out-Null
    })

    $btnInstallFolder.Add_Click({
        $state = & $collectState
        if (-not $state.InstallRoot) {
            & $appendLog 'Install root is blank.'
            return
        }
        if (-not (Test-Path $state.InstallRoot)) {
            New-Item -ItemType Directory -Path $state.InstallRoot -Force | Out-Null
        }
        Start-Process explorer.exe $state.InstallRoot | Out-Null
    })

    $btnRefreshProjects.Add_Click({
        & $refreshProjects
        & $showProjectSummary
    })

    $cmbProjects.Add_SelectedIndexChanged({ & $showProjectSummary })

    $btnOpenFolder.Add_Click({
        $project = & $selectedProject
        if (-not $project) { return }
        Start-Process explorer.exe $project.Path | Out-Null
    })

    $btnShell.Add_Click({
        $project = & $selectedProject
        if (-not $project) { return }
        Start-ProjectShell -Path $project.Path
    })

    $btnCode.Add_Click({
        $project = & $selectedProject
        if (-not $project) { return }
        if (-not (Open-PathInCode -Path $project.Path)) {
            & $appendLog 'VS Code command not found on PATH.'
            & $setStatus 'VS Code command not found.' $colors.Bad
        }
    })

    $btnFeedback.Add_Click({
        $project = & $selectedProject
        if (-not $project) { return }
        if (-not (Test-Path $project.FeedbackPath)) {
            New-Item -ItemType Directory -Path $project.FeedbackPath -Force | Out-Null
        }
        Start-Process explorer.exe $project.FeedbackPath | Out-Null
    })

    & $refreshProjects
    & $showProjectSummary
    & $appendLog 'Windows operator app ready.'
    & $setStatus 'Ready.' $colors.Subtle

    [void]$form.ShowDialog()
}

function Invoke-CurlJsonPost {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)]$Body,
        [int]$TimeoutSec = 120,
        [string]$UserName = '',
        [string]$Password = ''
    )
    $payloadPath = Join-Path $env:TEMP ('luminax-post-{0}.json' -f ([Guid]::NewGuid().ToString('N')))
    [System.IO.File]::WriteAllText(
        $payloadPath,
        ($Body | ConvertTo-Json -Depth 12 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )
    try {
        $args = @(
            '--silent',
            '--show-error',
            '--http1.1',
            '--connect-timeout', '5',
            '--max-time', "$TimeoutSec",
            '--header', 'Content-Type: application/json',
            '--data-binary', ('@' + $payloadPath),
            '--url', $Uri
        )
        if ($UserName -and $Password) {
            $args += @('--user', ('{0}:{1}' -f $UserName, $Password))
        }
        $rawLines = & curl.exe @args 2>&1
        $exitCode = $LASTEXITCODE
        $raw = (($rawLines | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
        if ($exitCode -ne 0) {
            throw "curl.exe failed with exit code $exitCode for ${Uri}: $raw"
        }
        if (-not $raw) { return $null }
        return ($raw | ConvertFrom-Json)
    } finally {
        Remove-Item $payloadPath -Force -ErrorAction SilentlyContinue
    }
}

function Get-ProjectKey {
    param([pscustomobject]$Project)
    if (-not $Project) { return '' }
    $name = [string]$Project.Name
    return (($name.ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-'))
}

function Get-ProjectContextText {
    param(
        [pscustomobject]$Project,
        [int]$DetailLines = 10
    )
    if (-not $Project) { return '' }
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add(('Project: {0}' -f $Project.Name))
    $lines.Add(('Path: {0}' -f $Project.Path))
    if (Test-Path $Project.NameplatePath) {
        $lines.Add('')
        $lines.Add('Nameplate')
        $lines.Add('---------')
        foreach ($line in (Get-Content -Path $Project.NameplatePath | Select-Object -First 28)) {
            $lines.Add($line)
        }
    }
    if (Test-Path $Project.DetailsPath) {
        $lines.Add('')
        $lines.Add('Recent details')
        $lines.Add('--------------')
        foreach ($line in (Get-Content -Path $Project.DetailsPath | Select-Object -Last $DetailLines)) {
            $lines.Add($line)
        }
    }
    return ($lines -join [Environment]::NewLine)
}

function Add-ProjectDetailsLine {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Project,
        [Parameter(Mandatory = $true)][string]$Text
    )
    if (-not (Test-Path $Project.DetailsPath)) {
        Set-Content -Path $Project.DetailsPath -Value @() -Encoding UTF8
    }
    $entry = '{0} - {1}' -f (Get-Date -Format 'yyyy-MM-dd'), $Text.Trim()
    Add-Content -Path $Project.DetailsPath -Value $entry -Encoding UTF8
    return $entry
}

function Write-ProjectFeedbackNote {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Project,
        [Parameter(Mandatory = $true)][string]$Text
    )
    if (-not (Test-Path $Project.FeedbackPath)) {
        New-Item -ItemType Directory -Path $Project.FeedbackPath -Force | Out-Null
    }
    $notePath = Join-Path $Project.FeedbackPath ('feedback_{0}.txt' -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    $content = @(
        ('Project: {0}' -f $Project.Name),
        ('Created: {0}' -f (Get-Date).ToString('o')),
        '',
        $Text.Trim()
    )
    Set-Content -Path $notePath -Value $content -Encoding UTF8
    return $notePath
}

function Start-LuminaxWindowsStudioApp {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $envPath = Join-Path $PSScriptRoot '.env.windows'
    $theme = @{
        BgTop = [System.Drawing.Color]::FromArgb(5, 10, 17)
        BgBottom = [System.Drawing.Color]::FromArgb(9, 19, 30)
        HeroA = [System.Drawing.Color]::FromArgb(16, 34, 50)
        HeroB = [System.Drawing.Color]::FromArgb(9, 19, 30)
        Panel = [System.Drawing.Color]::FromArgb(14, 27, 40)
        PanelStrong = [System.Drawing.Color]::FromArgb(18, 34, 49)
        Input = [System.Drawing.Color]::FromArgb(14, 26, 38)
        InputSoft = [System.Drawing.Color]::FromArgb(18, 32, 45)
        Text = [System.Drawing.Color]::FromArgb(242, 246, 251)
        Muted = [System.Drawing.Color]::FromArgb(196, 212, 226)
        Mint = [System.Drawing.Color]::FromArgb(125, 240, 205)
        Sky = [System.Drawing.Color]::FromArgb(156, 214, 255)
        Gold = [System.Drawing.Color]::FromArgb(255, 211, 132)
        Ember = [System.Drawing.Color]::FromArgb(255, 154, 121)
        Danger = [System.Drawing.Color]::FromArgb(255, 143, 157)
        PrimaryText = [System.Drawing.Color]::FromArgb(7, 17, 26)
    }

    function New-Font {
        param(
            [float]$Size,
            [System.Drawing.FontStyle]$Style = [System.Drawing.FontStyle]::Regular,
            [string]$Family = 'Segoe UI'
        )
        return New-Object System.Drawing.Font($Family, $Size, $Style)
    }

    function Set-DoubleBuffered {
        param([System.Windows.Forms.Control]$Control)
        try {
            $prop = $Control.GetType().GetProperty('DoubleBuffered', [System.Reflection.BindingFlags]'NonPublic,Instance')
            if ($prop) { $prop.SetValue($Control, $true, $null) }
        } catch {}
    }

    function New-RoundedPath {
        param([System.Drawing.Rectangle]$Rect, [int]$Radius = 24)
        $d = [int][Math]::Max(8, [Math]::Min($Radius * 2, [Math]::Min($Rect.Width, $Rect.Height)))
        $path = New-Object System.Drawing.Drawing2D.GraphicsPath
        $path.StartFigure()
        $path.AddArc([int]$Rect.X, [int]$Rect.Y, $d, $d, 180, 90)
        $path.AddArc(([int]$Rect.Right - $d), [int]$Rect.Y, $d, $d, 270, 90)
        $path.AddArc(([int]$Rect.Right - $d), ([int]$Rect.Bottom - $d), $d, $d, 0, 90)
        $path.AddArc([int]$Rect.X, ([int]$Rect.Bottom - $d), $d, $d, 90, 90)
        $path.CloseFigure()
        return $path
    }

    function Apply-CardChrome {
        param(
            [System.Windows.Forms.Control]$Control,
            [string]$Variant = 'panel',
            [int]$Radius = 28
        )
        Set-DoubleBuffered $Control
        $Control.BackColor = if ($Variant -eq 'hero') { $theme.HeroA } else { $theme.PanelStrong }
        $Control.BorderStyle = 'FixedSingle'
        $Control.Padding = New-Object System.Windows.Forms.Padding(0)
    }

    function New-TitleLabel {
        param([string]$Text, [float]$Size = 16, [bool]$Hero = $false)
        $label = New-Object System.Windows.Forms.Label
        $label.Text = $Text
        $label.AutoSize = $true
        $label.ForeColor = $theme.Text
        $label.Font = New-Font -Size $Size -Style ([System.Drawing.FontStyle]::Bold)
        if ($Hero) {
            $label.MaximumSize = New-Object System.Drawing.Size(720, 0)
        }
        return $label
    }

    function New-BodyLabel {
        param([string]$Text, [float]$Size = 9.2, [int]$MaxWidth = 0)
        $label = New-Object System.Windows.Forms.Label
        $label.Text = $Text
        $label.AutoSize = $true
        $label.ForeColor = $theme.Muted
        $label.Font = New-Font -Size $Size
        if ($MaxWidth -gt 0) { $label.MaximumSize = New-Object System.Drawing.Size($MaxWidth, 0) }
        return $label
    }

    function New-EyebrowLabel {
        param([string]$Text, [System.Drawing.Color]$Color = $theme.Mint)
        $label = New-Object System.Windows.Forms.Label
        $label.Text = $Text.ToUpperInvariant()
        $label.AutoSize = $true
        $label.ForeColor = $Color
        $label.Font = New-Font -Size 8.3 -Family 'Consolas'
        return $label
    }

    function New-ActionButton {
        param([string]$Text, [string]$Variant = 'secondary', [int]$Width = 126, [int]$Height = 38)
        $button = New-Object System.Windows.Forms.Button
        $button.Text = $Text
        $button.Width = $Width
        $button.Height = $Height
        $button.FlatStyle = 'Flat'
        $button.FlatAppearance.BorderSize = 1
        $button.Cursor = 'Hand'
        $button.Font = New-Font -Size 9.2 -Style ([System.Drawing.FontStyle]::Bold)
        switch ($Variant) {
            'primary' {
                $button.BackColor = $theme.Mint
                $button.ForeColor = $theme.PrimaryText
                $button.FlatAppearance.BorderColor = $theme.Mint
                $button.Tag = [pscustomobject]@{
                    BaseBack = $theme.Mint
                    HoverBack = [System.Drawing.Color]::FromArgb(99, 227, 197)
                    BaseFore = $theme.PrimaryText
                    Active = $false
                }
            }
            'nav' {
                $button.BackColor = $theme.Panel
                $button.ForeColor = $theme.Muted
                $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(58, $theme.Sky)
                $button.Tag = [pscustomobject]@{
                    BaseBack = $theme.Panel
                    HoverBack = [System.Drawing.Color]::FromArgb(16, 32, 48)
                    BaseFore = $theme.Muted
                    Active = $false
                }
            }
            default {
                $button.BackColor = $theme.HeroA
                $button.ForeColor = $theme.Text
                $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(58, $theme.Sky)
                $button.Tag = [pscustomobject]@{
                    BaseBack = $theme.HeroA
                    HoverBack = [System.Drawing.Color]::FromArgb(18, 38, 58)
                    BaseFore = $theme.Text
                    Active = $false
                }
            }
        }
        $button.Add_MouseEnter({
            param($sender, $eventArgs)
            if ($sender.Tag.Active) { return }
            $sender.BackColor = $sender.Tag.HoverBack
            $sender.ForeColor = $sender.Tag.BaseFore
        })
        $button.Add_MouseLeave({
            param($sender, $eventArgs)
            if ($sender.Tag.Active) { return }
            $sender.BackColor = $sender.Tag.BaseBack
            $sender.ForeColor = $sender.Tag.BaseFore
        })
        return $button
    }

    function Set-ButtonActive {
        param([System.Windows.Forms.Button]$Button, [bool]$Active)
        if (-not $Button) { return }
        $Button.Tag.Active = $Active
        if ($Active) {
            $Button.BackColor = [System.Drawing.Color]::FromArgb(18, 38, 58)
            $Button.ForeColor = $theme.Text
            $Button.FlatAppearance.BorderColor = $theme.Sky
        } else {
            $Button.BackColor = $Button.Tag.BaseBack
            $Button.ForeColor = $Button.Tag.BaseFore
            $Button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(58, $theme.Sky)
        }
    }

    function New-TextInput {
        param([bool]$Multiline = $false, [bool]$ReadOnly = $false, [bool]$PasswordBox = $false)
        $box = New-Object System.Windows.Forms.TextBox
        $box.Multiline = $Multiline
        $box.ReadOnly = $ReadOnly
        $box.BackColor = if ($ReadOnly) { $theme.InputSoft } else { $theme.Input }
        $box.ForeColor = $theme.Text
        $box.BorderStyle = 'FixedSingle'
        $box.Font = if ($Multiline -or $ReadOnly) { New-Font -Size 9.0 -Family 'Consolas' } else { New-Font -Size 9.4 }
        if ($PasswordBox) { $box.UseSystemPasswordChar = $true }
        if ($Multiline) {
            $box.ScrollBars = 'Vertical'
            $box.AcceptsReturn = $true
            $box.AcceptsTab = $true
        }
        return $box
    }

    function New-Chip {
        param([string]$Text)
        $label = New-Object System.Windows.Forms.Label
        $label.Text = $Text
        $label.AutoSize = $false
        $label.Height = 30
        $label.Width = 210
        $label.Padding = New-Object System.Windows.Forms.Padding(12, 7, 12, 6)
        $label.TextAlign = 'MiddleLeft'
        $label.BackColor = $theme.HeroA
        $label.ForeColor = $theme.Sky
        $label.BorderStyle = 'FixedSingle'
        $label.Font = New-Font -Size 8.6 -Family 'Consolas'
        return $label
    }

    function Set-ChipState {
        param([System.Windows.Forms.Label]$Chip, [string]$Text, [string]$Mode = 'info')
        if (-not $Chip) { return }
        $Chip.Text = $Text
        switch ($Mode) {
            'good' {
                $Chip.ForeColor = $theme.Mint
                $Chip.BackColor = [System.Drawing.Color]::FromArgb(16, 43, 41)
            }
            'warn' {
                $Chip.ForeColor = $theme.Gold
                $Chip.BackColor = [System.Drawing.Color]::FromArgb(44, 33, 16)
            }
            'bad' {
                $Chip.ForeColor = $theme.Danger
                $Chip.BackColor = [System.Drawing.Color]::FromArgb(50, 19, 24)
            }
            default {
                $Chip.ForeColor = $theme.Sky
                $Chip.BackColor = $theme.HeroA
            }
        }
    }

    function New-LogBox {
        $box = New-Object System.Windows.Forms.RichTextBox
        $box.ReadOnly = $true
        $box.BorderStyle = 'None'
        $box.BackColor = $theme.InputSoft
        $box.ForeColor = $theme.Text
        $box.Font = New-Font -Size 9 -Family 'Consolas'
        $box.DetectUrls = $false
        return $box
    }

    function Add-RichEntry {
        param(
            [Parameter(Mandatory = $true)][System.Windows.Forms.RichTextBox]$Box,
            [Parameter(Mandatory = $true)][string]$Prefix,
            [Parameter(Mandatory = $true)][string]$Text,
            [System.Drawing.Color]$PrefixColor = $theme.Sky,
            [System.Drawing.Color]$BodyColor = $theme.Text
        )
        $message = [string]$Text
        if (-not $message) { return }
        $message = $message.Trim()
        if (-not $message) { return }
        $Box.SelectionStart = $Box.TextLength
        $Box.SelectionLength = 0
        $Box.SelectionColor = $PrefixColor
        $Box.AppendText($Prefix)
        $Box.SelectionColor = $BodyColor
        $Box.AppendText($message + [Environment]::NewLine + [Environment]::NewLine)
        $Box.SelectionColor = $theme.Text
        $Box.ScrollToCaret()
    }

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Mount Olympus Windows Studio'
    $targetScreen = [System.Windows.Forms.Screen]::FromPoint([System.Windows.Forms.Cursor]::Position)
    if (-not $targetScreen) {
        $targetScreen = [System.Windows.Forms.Screen]::PrimaryScreen
    }
    $workingArea = $targetScreen.WorkingArea
    $formWidth = [Math]::Min(1440, [Math]::Max(1180, ($workingArea.Width - 40)))
    $formHeight = [Math]::Min(920, [Math]::Max(760, ($workingArea.Height - 40)))
    $form.StartPosition = 'Manual'
    $form.Size = New-Object System.Drawing.Size($formWidth, $formHeight)
    $form.MinimumSize = New-Object System.Drawing.Size(([Math]::Min(1320, $formWidth)), ([Math]::Min(860, $formHeight)))
    $form.Location = New-Object System.Drawing.Point(
        ([int]($workingArea.Left + [Math]::Max(0, (($workingArea.Width - $formWidth) / 2)))),
        ([int]($workingArea.Top + [Math]::Max(0, (($workingArea.Height - $formHeight) / 2))))
    )
    $form.BackColor = $theme.BgBottom
    $form.ForeColor = $theme.Text
    $form.Font = New-Font -Size 9.3
    $form.Opacity = 1
    $form.WindowState = 'Normal'
    $form.ShowInTaskbar = $true

    $toolTips = New-Object System.Windows.Forms.ToolTip
    $toolTips.InitialDelay = 160
    $toolTips.ReshowDelay = 80
    $toolTips.AutoPopDelay = 9000
    $toolTips.ShowAlways = $true

    $hero = New-Object System.Windows.Forms.Panel
    $hero.SetBounds(18, 18, 1388, 190)
    $hero.Anchor = 'Top,Left,Right'
    Apply-CardChrome -Control $hero -Variant 'hero' -Radius 32
    $form.Controls.Add($hero)

    $leftRail = New-Object System.Windows.Forms.Panel
    $leftRail.SetBounds(18, 216, 316, 662)
    $leftRail.Anchor = 'Top,Bottom,Left'
    $leftRail.BackColor = [System.Drawing.Color]::Transparent
    $form.Controls.Add($leftRail)

    $rightStage = New-Object System.Windows.Forms.Panel
    $rightStage.SetBounds(352, 216, 1054, 662)
    $rightStage.Anchor = 'Top,Bottom,Left,Right'
    $rightStage.BackColor = [System.Drawing.Color]::Transparent
    $form.Controls.Add($rightStage)

    $heroEyebrow = New-EyebrowLabel -Text 'Mount Olympus Windows Studio'
    $heroEyebrow.Location = New-Object System.Drawing.Point(28, 24)
    $hero.Controls.Add($heroEyebrow)

    $heroTitle = New-TitleLabel -Text 'Mount Olympus on desktop: your project arm, not just a server window.' -Size 20.4 -Hero $true
    $heroTitle.Location = New-Object System.Drawing.Point(24, 44)
    $heroTitle.MaximumSize = New-Object System.Drawing.Size(650, 0)
    $hero.Controls.Add($heroTitle)

    $heroCopy = New-BodyLabel -Text 'Talk to Zeus from the command deck, use Local Fast on-box, generate art, keep project notes tight, and only drop into Operations when you are actually wiring or repairing Mount Olympus.' -Size 9.7 -MaxWidth 650
    $heroCopy.Location = New-Object System.Drawing.Point(28, 102)
    $hero.Controls.Add($heroCopy)

    $chipSession = New-Chip -Text 'Session: ready'
    $chipSession.Location = New-Object System.Drawing.Point(28, 150)
    $hero.Controls.Add($chipSession)
    $chipEndpoint = New-Chip -Text 'Endpoint: not checked'
    $chipEndpoint.Location = New-Object System.Drawing.Point(248, 150)
    $hero.Controls.Add($chipEndpoint)
    $chipProject = New-Chip -Text 'Project: none selected'
    $chipProject.Location = New-Object System.Drawing.Point(468, 150)
    $hero.Controls.Add($chipProject)
    $chipSession.Visible = $false
    $chipEndpoint.Visible = $false
    $chipProject.Visible = $false

    $txtStudioPrompt = New-TextInput -Multiline $true
    $txtStudioPrompt.SetBounds(28, 144, 732, 34)
    $txtStudioPrompt.Anchor = 'Left,Right,Top'
    $hero.Controls.Add($txtStudioPrompt)
    $btnStudioSend = New-ActionButton -Text 'Ask Zeus' -Variant 'primary' -Width 124 -Height 34
    $btnStudioSend.Location = New-Object System.Drawing.Point(772, 144)
    $hero.Controls.Add($btnStudioSend)

    $heroGuide = New-EyebrowLabel -Text 'Recommended flow' -Color $theme.Gold
    $heroGuide.Location = New-Object System.Drawing.Point(910, 26)
    $hero.Controls.Add($heroGuide)

    $heroGuideCopy = New-TitleLabel -Text 'Ask from the command deck first. Use Operations only when you are changing the machine.' -Size 11.3
    $heroGuideCopy.Location = New-Object System.Drawing.Point(906, 44)
    $heroGuideCopy.MaximumSize = New-Object System.Drawing.Size(430, 0)
    $hero.Controls.Add($heroGuideCopy)

    $btnHeroSave = New-ActionButton -Text 'Save Profile' -Variant 'secondary' -Width 126
    $btnHeroSave.Location = New-Object System.Drawing.Point(906, 128)
    $hero.Controls.Add($btnHeroSave)
    $btnHeroTest = New-ActionButton -Text 'Test Link' -Variant 'secondary' -Width 112
    $btnHeroTest.Location = New-Object System.Drawing.Point(1042, 128)
    $hero.Controls.Add($btnHeroTest)
    $btnHeroDeploy = New-ActionButton -Text 'Deploy + Connect' -Variant 'primary' -Width 154
    $btnHeroDeploy.Location = New-Object System.Drawing.Point(1164, 128)
    $hero.Controls.Add($btnHeroDeploy)

    $guideCard = New-Object System.Windows.Forms.Panel
    $guideCard.SetBounds(0, 0, 316, 210)
    $guideCard.Anchor = 'Top,Left,Right'
    Apply-CardChrome -Control $guideCard -Variant 'panel' -Radius 28
    $leftRail.Controls.Add($guideCard)
    $guideEyebrow = New-EyebrowLabel -Text 'What To Press When' -Color $theme.Gold
    $guideEyebrow.Location = New-Object System.Drawing.Point(20, 20)
    $guideCard.Controls.Add($guideEyebrow)
    $guideTitle = New-TitleLabel -Text 'Work from the arm first.' -Size 13.5
    $guideTitle.Location = New-Object System.Drawing.Point(18, 42)
    $guideCard.Controls.Add($guideTitle)
    foreach ($stepInfo in @(
        @{ Y = 82; Text = '1. Pick a project, type in the command deck, and stay in Assistant for normal work.' },
        @{ Y = 114; Text = '2. Ask Zeus for the full brain, or Ask Local Fast for on-box drafts and quick passes.' },
        @{ Y = 146; Text = '3. Generate Image for assets, or open a local image lane when one is running on this machine.' },
        @{ Y = 178; Text = '4. Use Projects for notes and feedback. Open Operations only when you need deploy, monitor, or repair.' }
    )) {
        $stepLabel = New-BodyLabel -Text $stepInfo.Text -Size 9.2 -MaxWidth 272
        $stepLabel.Location = New-Object System.Drawing.Point(20, $stepInfo.Y)
        $guideCard.Controls.Add($stepLabel)
    }

    $signalCard = New-Object System.Windows.Forms.Panel
    $signalCard.SetBounds(0, 226, 316, 140)
    $signalCard.Anchor = 'Top,Left,Right'
    Apply-CardChrome -Control $signalCard -Variant 'panel' -Radius 28
    $leftRail.Controls.Add($signalCard)
    $signalEyebrow = New-EyebrowLabel -Text 'Live Signal'
    $signalEyebrow.Location = New-Object System.Drawing.Point(20, 20)
    $signalCard.Controls.Add($signalEyebrow)
    $signalTitle = New-TitleLabel -Text 'Session snapshot' -Size 13.5
    $signalTitle.Location = New-Object System.Drawing.Point(18, 42)
    $signalCard.Controls.Add($signalTitle)
    $txtSignalSnapshot = New-TextInput -Multiline $true -ReadOnly $true
    $txtSignalSnapshot.SetBounds(20, 74, 276, 58)
    $txtSignalSnapshot.Font = New-Font -Size 8.8 -Family 'Consolas'
    $signalCard.Controls.Add($txtSignalSnapshot)

    $activityCard = New-Object System.Windows.Forms.Panel
    $activityCard.SetBounds(0, 382, 316, 280)
    $activityCard.Anchor = 'Top,Bottom,Left,Right'
    Apply-CardChrome -Control $activityCard -Variant 'panel' -Radius 28
    $leftRail.Controls.Add($activityCard)
    $activityEyebrow = New-EyebrowLabel -Text 'Activity'
    $activityEyebrow.Location = New-Object System.Drawing.Point(20, 20)
    $activityCard.Controls.Add($activityEyebrow)
    $activityTitle = New-TitleLabel -Text 'Operator stream' -Size 13.5
    $activityTitle.Location = New-Object System.Drawing.Point(18, 42)
    $activityCard.Controls.Add($activityTitle)
    $btnClearActivity = New-ActionButton -Text 'Clear' -Variant 'secondary' -Width 76 -Height 30
    $btnClearActivity.Location = New-Object System.Drawing.Point(220, 20)
    $activityCard.Controls.Add($btnClearActivity)
    $rtbActivity = New-LogBox
    $rtbActivity.SetBounds(20, 74, 276, 186)
    $rtbActivity.Anchor = 'Top,Bottom,Left,Right'
    $activityCard.Controls.Add($rtbActivity)

    $workspaceCard = New-Object System.Windows.Forms.Panel
    $workspaceCard.SetBounds(0, 0, 1054, 662)
    $workspaceCard.Anchor = 'Top,Bottom,Left,Right'
    Apply-CardChrome -Control $workspaceCard -Variant 'panel' -Radius 30
    $rightStage.Controls.Add($workspaceCard)

    $tabs = New-Object System.Windows.Forms.TabControl
    $tabs.SetBounds(18, 18, 1018, 626)
    $tabs.Anchor = 'Top,Bottom,Left,Right'
    $tabs.Font = New-Font -Size 9.2 -Style ([System.Drawing.FontStyle]::Bold)
    $tabs.Appearance = 'Normal'
    $tabs.BackColor = $theme.Panel
    $tabs.ForeColor = $theme.Text
    $workspaceCard.Controls.Add($tabs)

    $tabStudio = New-Object System.Windows.Forms.TabPage
    $tabStudio.Text = 'Assistant'
    $tabStudio.BackColor = $theme.PanelStrong
    $tabs.Controls.Add($tabStudio)

    $studioTitle = New-TitleLabel -Text 'Use Zeus like a desktop arm.' -Size 15.3
    $studioTitle.Location = New-Object System.Drawing.Point(20, 18)
    $tabStudio.Controls.Add($studioTitle)
    $studioCopy = New-BodyLabel -Text 'The command deck above handles asking and creating. This tab keeps the transcript, live lanes, context, and project jump points close.' -Size 9.2 -MaxWidth 900
    $studioCopy.Location = New-Object System.Drawing.Point(20, 46)
    $tabStudio.Controls.Add($studioCopy)
    $studioProjectLabel = New-BodyLabel -Text 'Project' -Size 8.9
    $studioProjectLabel.Location = New-Object System.Drawing.Point(20, 76)
    $tabStudio.Controls.Add($studioProjectLabel)
    $cmbStudioProjects = New-Object System.Windows.Forms.ComboBox
    $cmbStudioProjects.SetBounds(20, 96, 222, 30)
    $cmbStudioProjects.DropDownStyle = 'DropDownList'
    $cmbStudioProjects.BackColor = $theme.Input
    $cmbStudioProjects.ForeColor = $theme.Text
    $tabStudio.Controls.Add($cmbStudioProjects)
    $btnContextRefresh = New-ActionButton -Text 'Refresh' -Variant 'secondary' -Width 90 -Height 30
    $btnContextRefresh.Location = New-Object System.Drawing.Point(252, 96)
    $tabStudio.Controls.Add($btnContextRefresh)
    $lensLabel = New-BodyLabel -Text 'Role / lens' -Size 8.9
    $lensLabel.Location = New-Object System.Drawing.Point(360, 76)
    $tabStudio.Controls.Add($lensLabel)
    $txtStudioLens = New-TextInput -Multiline $true
    $txtStudioLens.SetBounds(360, 96, 280, 42)
    $tabStudio.Controls.Add($txtStudioLens)
    $assistantToolsLabel = New-BodyLabel -Text 'Live lanes + generation' -Size 8.9
    $assistantToolsLabel.Location = New-Object System.Drawing.Point(660, 76)
    $tabStudio.Controls.Add($assistantToolsLabel)
    $txtAssistantTools = New-TextInput -Multiline $true -ReadOnly $true
    $txtAssistantTools.SetBounds(660, 96, 328, 112)
    $txtAssistantTools.Anchor = 'Top,Right'
    $txtAssistantTools.Font = New-Font -Size 8.7 -Family 'Consolas'
    $tabStudio.Controls.Add($txtAssistantTools)
    $rtbChat = New-LogBox
    $rtbChat.SetBounds(20, 146, 620, 264)
    $rtbChat.Anchor = 'Top,Left,Right'
    $tabStudio.Controls.Add($rtbChat)
    $contextLabel = New-BodyLabel -Text 'Project context' -Size 8.9
    $contextLabel.Location = New-Object System.Drawing.Point(660, 220)
    $tabStudio.Controls.Add($contextLabel)
    $txtProjectContext = New-TextInput -Multiline $true -ReadOnly $true
    $txtProjectContext.SetBounds(660, 240, 328, 72)
    $txtProjectContext.Anchor = 'Top,Right'
    $tabStudio.Controls.Add($txtProjectContext)
    $btnStudioPlan = New-ActionButton -Text 'Generate Plan' -Variant 'secondary' -Width 126 -Height 28
    $btnStudioPlan.Location = New-Object System.Drawing.Point(20, 424)
    $tabStudio.Controls.Add($btnStudioPlan)
    $btnStudioTask = New-ActionButton -Text 'Queue Task' -Variant 'secondary' -Width 126 -Height 28
    $btnStudioTask.Location = New-Object System.Drawing.Point(154, 424)
    $tabStudio.Controls.Add($btnStudioTask)
    $btnAssistantRefresh = New-ActionButton -Text 'Refresh Tools' -Variant 'secondary' -Width 126 -Height 28
    $btnAssistantRefresh.Location = New-Object System.Drawing.Point(288, 424)
    $tabStudio.Controls.Add($btnAssistantRefresh)
    $btnStudioLocal = New-ActionButton -Text 'Ask Local Fast' -Variant 'secondary' -Width 118 -Height 28
    $btnStudioLocal.Location = New-Object System.Drawing.Point(422, 424)
    $tabStudio.Controls.Add($btnStudioLocal)
    $btnStudioImage = New-ActionButton -Text 'Generate Image' -Variant 'primary' -Width 118 -Height 28
    $btnStudioImage.Location = New-Object System.Drawing.Point(536, 424)
    $tabStudio.Controls.Add($btnStudioImage)
    $btnAssistantAsset = New-ActionButton -Text 'Open Last Asset' -Variant 'secondary' -Width 154 -Height 28
    $btnAssistantAsset.Location = New-Object System.Drawing.Point(660, 328)
    $tabStudio.Controls.Add($btnAssistantAsset)
    $btnOpenLocalImageLane = New-ActionButton -Text 'Local Image Lane' -Variant 'secondary' -Width 154 -Height 28
    $btnOpenLocalImageLane.Location = New-Object System.Drawing.Point(834, 328)
    $tabStudio.Controls.Add($btnOpenLocalImageLane)
    $btnStudioShell = New-ActionButton -Text 'PowerShell Here' -Variant 'secondary' -Width 154 -Height 28
    $btnStudioShell.Location = New-Object System.Drawing.Point(660, 360)
    $tabStudio.Controls.Add($btnStudioShell)
    $btnStarterPlan = New-ActionButton -Text 'Todo List' -Variant 'secondary' -Width 154 -Height 28
    $btnStarterPlan.Location = New-Object System.Drawing.Point(660, 392)
    $tabStudio.Controls.Add($btnStarterPlan)
    $btnStarterReview = New-ActionButton -Text 'Exercise' -Variant 'secondary' -Width 154 -Height 28
    $btnStarterReview.Location = New-Object System.Drawing.Point(660, 424)
    $tabStudio.Controls.Add($btnStarterReview)
    $btnStarterDraft = New-ActionButton -Text 'Life Goals' -Variant 'secondary' -Width 154 -Height 28
    $btnStarterDraft.Location = New-Object System.Drawing.Point(834, 424)
    $tabStudio.Controls.Add($btnStarterDraft)
    $btnStarterFix = New-ActionButton -Text 'Reminder' -Variant 'secondary' -Width 154 -Height 28
    $btnStarterFix.Location = New-Object System.Drawing.Point(834, 392)
    $tabStudio.Controls.Add($btnStarterFix)
    $btnStudioFolder = New-ActionButton -Text 'Open Folder' -Variant 'secondary' -Width 154 -Height 28
    $btnStudioFolder.Location = New-Object System.Drawing.Point(660, 360)
    $tabStudio.Controls.Add($btnStudioFolder)
    $btnStudioCode = New-ActionButton -Text 'Open In Code' -Variant 'secondary' -Width 154 -Height 28
    $btnStudioCode.Location = New-Object System.Drawing.Point(834, 360)
    $tabStudio.Controls.Add($btnStudioCode)
    $btnStudioShell.Visible = $false
    $btnStudioClear = New-ActionButton -Text 'Clear Chat' -Variant 'secondary' -Width 110 -Height 28
    $btnStudioClear.Location = New-Object System.Drawing.Point(878, 16)
    $tabStudio.Controls.Add($btnStudioClear)

    $tabProjects = New-Object System.Windows.Forms.TabPage
    $tabProjects.Text = 'Projects'
    $tabProjects.BackColor = $theme.PanelStrong
    $tabs.Controls.Add($tabProjects)
    $projectsTitle = New-TitleLabel -Text 'See the nameplate, recent details, and update project history fast.' -Size 15.5
    $projectsTitle.Location = New-Object System.Drawing.Point(20, 18)
    $projectsTitle.MaximumSize = New-Object System.Drawing.Size(720, 0)
    $tabProjects.Controls.Add($projectsTitle)
    $projectPickLabel = New-BodyLabel -Text 'Project' -Size 8.9
    $projectPickLabel.Location = New-Object System.Drawing.Point(20, 66)
    $tabProjects.Controls.Add($projectPickLabel)
    $cmbProjectsView = New-Object System.Windows.Forms.ComboBox
    $cmbProjectsView.SetBounds(20, 86, 222, 30)
    $cmbProjectsView.DropDownStyle = 'DropDownList'
    $cmbProjectsView.BackColor = $theme.Input
    $cmbProjectsView.ForeColor = $theme.Text
    $tabProjects.Controls.Add($cmbProjectsView)
    $btnProjectsRefresh = New-ActionButton -Text 'Refresh' -Variant 'secondary' -Width 90 -Height 30
    $btnProjectsRefresh.Location = New-Object System.Drawing.Point(252, 86)
    $tabProjects.Controls.Add($btnProjectsRefresh)
    $btnProjectFolder = New-ActionButton -Text 'Folder' -Variant 'secondary' -Width 88 -Height 30
    $btnProjectFolder.Location = New-Object System.Drawing.Point(352, 86)
    $tabProjects.Controls.Add($btnProjectFolder)
    $btnProjectCode = New-ActionButton -Text 'Code' -Variant 'secondary' -Width 88 -Height 30
    $btnProjectCode.Location = New-Object System.Drawing.Point(448, 86)
    $tabProjects.Controls.Add($btnProjectCode)
    $btnProjectShell = New-ActionButton -Text 'Shell' -Variant 'secondary' -Width 88 -Height 30
    $btnProjectShell.Location = New-Object System.Drawing.Point(544, 86)
    $tabProjects.Controls.Add($btnProjectShell)
    $btnProjectFeedbackFolder = New-ActionButton -Text 'Feedback' -Variant 'secondary' -Width 106 -Height 30
    $btnProjectFeedbackFolder.Location = New-Object System.Drawing.Point(640, 86)
    $tabProjects.Controls.Add($btnProjectFeedbackFolder)
    $txtProjectSummary = New-TextInput -Multiline $true -ReadOnly $true
    $txtProjectSummary.SetBounds(20, 132, 620, 470)
    $txtProjectSummary.Anchor = 'Top,Bottom,Left'
    $tabProjects.Controls.Add($txtProjectSummary)
    $detailLabel = New-BodyLabel -Text 'Add a line to details.txt' -Size 8.9
    $detailLabel.Location = New-Object System.Drawing.Point(660, 66)
    $tabProjects.Controls.Add($detailLabel)
    $txtDetailEntry = New-TextInput -Multiline $true
    $txtDetailEntry.SetBounds(660, 86, 328, 118)
    $tabProjects.Controls.Add($txtDetailEntry)
    $btnAddDetail = New-ActionButton -Text 'Add Detail Line' -Variant 'primary' -Width 136 -Height 34
    $btnAddDetail.Location = New-Object System.Drawing.Point(660, 214)
    $tabProjects.Controls.Add($btnAddDetail)
    $feedbackLabel = New-BodyLabel -Text 'Capture a feedback note' -Size 8.9
    $feedbackLabel.Location = New-Object System.Drawing.Point(660, 274)
    $tabProjects.Controls.Add($feedbackLabel)
    $txtFeedbackEntry = New-TextInput -Multiline $true
    $txtFeedbackEntry.SetBounds(660, 294, 328, 220)
    $txtFeedbackEntry.Anchor = 'Top,Bottom,Right'
    $tabProjects.Controls.Add($txtFeedbackEntry)
    $btnAddFeedback = New-ActionButton -Text 'Capture Feedback' -Variant 'secondary' -Width 148 -Height 34
    $btnAddFeedback.Location = New-Object System.Drawing.Point(660, 524)
    $tabProjects.Controls.Add($btnAddFeedback)

    $tabOperations = New-Object System.Windows.Forms.TabPage
    $tabOperations.Text = 'Operations'
    $tabOperations.BackColor = $theme.PanelStrong
    $tabs.Controls.Add($tabOperations)
    $opsTitle = New-TitleLabel -Text 'Configure connection, deploy, monitor, and verify the live endpoint.' -Size 15.5
    $opsTitle.Location = New-Object System.Drawing.Point(20, 18)
    $tabOperations.Controls.Add($opsTitle)
    $txtHost = New-TextInput; $txtHost.SetBounds(20, 86, 170, 28); $txtHost.Text = $HostName
    $txtUser = New-TextInput; $txtUser.SetBounds(204, 86, 170, 28); $txtUser.Text = $UserName
    $txtKey = New-TextInput; $txtKey.SetBounds(388, 86, 314, 28); $txtKey.Text = $KeyPath
    $txtPassword = New-TextInput -PasswordBox $true; $txtPassword.SetBounds(716, 86, 160, 28); $txtPassword.Text = $Password
    $txtPort = New-TextInput; $txtPort.SetBounds(890, 86, 74, 28); $txtPort.Text = [string]$Port
    $txtRemoteBase = New-TextInput; $txtRemoteBase.SetBounds(20, 148, 202, 28); $txtRemoteBase.Text = $RemoteBaseDir
    $txtBaseUrl = New-TextInput; $txtBaseUrl.SetBounds(236, 148, 246, 28); $txtBaseUrl.Text = $ServerBaseUrl
    $txtToken = New-TextInput; $txtToken.SetBounds(496, 148, 190, 28); $txtToken.Text = $ControlToken
    $txtInstallRoot = New-TextInput; $txtInstallRoot.SetBounds(700, 148, 156, 28); $txtInstallRoot.Text = $InstallRoot
    $txtModelRoot = New-TextInput; $txtModelRoot.SetBounds(870, 148, 120, 28); $txtModelRoot.Text = $ModelDataRoot
    $txtNodeKey = New-TextInput; $txtNodeKey.SetBounds(20, 210, 170, 28); $txtNodeKey.Text = $NodeKey
    foreach ($field in @(
        @{ Label = 'Host'; X = 20; Y = 66; Control = $txtHost },
        @{ Label = 'User'; X = 204; Y = 66; Control = $txtUser },
        @{ Label = 'SSH Key Path'; X = 388; Y = 66; Control = $txtKey },
        @{ Label = 'SSH Password'; X = 716; Y = 66; Control = $txtPassword },
        @{ Label = 'SSH Port'; X = 890; Y = 66; Control = $txtPort },
        @{ Label = 'Remote Base Dir'; X = 20; Y = 128; Control = $txtRemoteBase },
        @{ Label = 'Server Base URL'; X = 236; Y = 128; Control = $txtBaseUrl },
        @{ Label = 'Control Token'; X = 496; Y = 128; Control = $txtToken },
        @{ Label = 'Install Root'; X = 700; Y = 128; Control = $txtInstallRoot },
        @{ Label = 'Model Data Root'; X = 870; Y = 128; Control = $txtModelRoot },
        @{ Label = 'Node Key'; X = 20; Y = 190; Control = $txtNodeKey }
    )) {
        $fieldLabel = New-BodyLabel -Text $field.Label -Size 8.8
        $fieldLabel.Location = New-Object System.Drawing.Point($field.X, $field.Y)
        $tabOperations.Controls.Add($fieldLabel)
        $tabOperations.Controls.Add($field.Control)
    }
    $modeLabel = New-BodyLabel -Text 'Model Mode' -Size 8.8
    $modeLabel.Location = New-Object System.Drawing.Point(204, 190)
    $tabOperations.Controls.Add($modeLabel)
    $cmbModelMode = New-Object System.Windows.Forms.ComboBox
    $cmbModelMode.SetBounds(204, 210, 154, 28)
    $cmbModelMode.DropDownStyle = 'DropDownList'
    $cmbModelMode.BackColor = $theme.Input
    $cmbModelMode.ForeColor = $theme.Text
    [void]$cmbModelMode.Items.Add('full')
    [void]$cmbModelMode.Items.Add('voice-core')
    $cmbModelMode.SelectedItem = $ModelMode
    if (-not $cmbModelMode.SelectedItem) { $cmbModelMode.SelectedIndex = 0 }
    $tabOperations.Controls.Add($cmbModelMode)
    $intervalLabel = New-BodyLabel -Text 'Monitor Interval (sec)' -Size 8.8
    $intervalLabel.Location = New-Object System.Drawing.Point(372, 190)
    $tabOperations.Controls.Add($intervalLabel)
    $numInterval = New-Object System.Windows.Forms.NumericUpDown
    $numInterval.SetBounds(372, 210, 98, 28)
    $numInterval.Minimum = 10
    $numInterval.Maximum = 600
    $numInterval.Value = [Math]::Max(10, [Math]::Min(600, $MonitorIntervalSec))
    $numInterval.BackColor = $theme.Input
    $numInterval.ForeColor = $theme.Text
    $tabOperations.Controls.Add($numInterval)
    $chkSkipModel = New-Object System.Windows.Forms.CheckBox
    $chkSkipModel.Text = 'Skip model bootstrap'
    $chkSkipModel.SetBounds(486, 210, 170, 24)
    $chkSkipModel.ForeColor = $theme.Text
    $chkSkipModel.Checked = [bool]$SkipModelBootstrap
    $tabOperations.Controls.Add($chkSkipModel)
    $chkSkipPrereq = New-Object System.Windows.Forms.CheckBox
    $chkSkipPrereq.Text = 'Skip prerequisite install'
    $chkSkipPrereq.SetBounds(664, 210, 186, 24)
    $chkSkipPrereq.ForeColor = $theme.Text
    $chkSkipPrereq.Checked = [bool]$SkipPrereqInstall
    $tabOperations.Controls.Add($chkSkipPrereq)
    $chkNoBrowser = New-Object System.Windows.Forms.CheckBox
    $chkNoBrowser.Text = 'Do not auto-open browser'
    $chkNoBrowser.SetBounds(854, 210, 164, 24)
    $chkNoBrowser.ForeColor = $theme.Text
    $chkNoBrowser.Checked = [bool]$NoBrowser -or ($Mode -eq 'app')
    $tabOperations.Controls.Add($chkNoBrowser)
    $btnSave = New-ActionButton -Text 'Save Profile' -Variant 'secondary' -Width 100 -Height 34
    $btnTest = New-ActionButton -Text 'Test Link' -Variant 'secondary' -Width 92 -Height 34
    $btnInstall = New-ActionButton -Text 'Deploy + Connect' -Variant 'primary' -Width 128 -Height 34
    $btnServerOnly = New-ActionButton -Text 'Server Only' -Variant 'secondary' -Width 98 -Height 34
    $btnMonitor = New-ActionButton -Text 'Install Monitor' -Variant 'secondary' -Width 112 -Height 34
    $btnDashboard = New-ActionButton -Text 'Open Dashboard' -Variant 'secondary' -Width 116 -Height 34
    $btnStatus = New-ActionButton -Text 'Show Status' -Variant 'secondary' -Width 96 -Height 34
    $btnLogs = New-ActionButton -Text 'Open Logs' -Variant 'secondary' -Width 88 -Height 34
    $btnInstallFolder = New-ActionButton -Text 'Install Folder' -Variant 'secondary' -Width 110 -Height 34
    $opsButtons = @($btnSave, $btnTest, $btnInstall, $btnServerOnly, $btnMonitor, $btnDashboard, $btnStatus, $btnLogs, $btnInstallFolder)
    $opsX = 20
    foreach ($button in $opsButtons) {
        $button.Location = New-Object System.Drawing.Point($opsX, 260)
        $tabOperations.Controls.Add($button)
        $opsX += ($button.Width + 8)
    }
    $rtbOutput = New-LogBox
    $rtbOutput.SetBounds(20, 320, 968, 282)
    $rtbOutput.Anchor = 'Top,Bottom,Left,Right'
    $tabOperations.Controls.Add($rtbOutput)

    $footerLabel = New-Object System.Windows.Forms.Label
    $footerLabel.SetBounds(24, 884, 1360, 24)
    $footerLabel.Anchor = 'Left,Right,Bottom'
    $footerLabel.ForeColor = $theme.Muted
    $footerLabel.BackColor = [System.Drawing.Color]::Transparent
    $footerLabel.Font = New-Font -Size 9.2
    $footerLabel.Text = 'Ready.'
    $form.Controls.Add($footerLabel)

    $appState = @{
        SelectedProjectName = ''
        LastEndpoint = [string]$ServerBaseUrl
        LastAction = 'Ready'
        Healthy = $false
        SelectedImageToolKey = ''
        LastAssetUrl = ''
        LocalImageLaneName = ''
        LocalImageLaneUrl = ''
        LocalImageLaneReady = $false
    }
    $projectRecords = @()
    $syncingProjects = $false

    $appendActivity = {
        param([string]$Text, [string]$Kind = 'info')
        $color = switch ($Kind) { 'good' { $theme.Mint } 'warn' { $theme.Gold } 'bad' { $theme.Danger } default { $theme.Sky } }
        Add-RichEntry -Box $rtbActivity -Prefix ('[{0}] ' -f (Get-Date).ToString('HH:mm:ss')) -Text $Text -PrefixColor $color -BodyColor $theme.Muted
    }
    $appendOutput = {
        param([string]$Text, [string]$Kind = 'info')
        $color = switch ($Kind) { 'good' { $theme.Mint } 'warn' { $theme.Gold } 'bad' { $theme.Danger } default { $theme.Sky } }
        Add-RichEntry -Box $rtbOutput -Prefix ('[{0}] ' -f (Get-Date).ToString('HH:mm:ss')) -Text $Text -PrefixColor $color -BodyColor $theme.Text
    }
    $appendChat = {
        param([string]$Speaker, [string]$Text, [string]$Kind = 'assistant')
        $color = switch ($Kind) { 'user' { $theme.Gold } 'system' { $theme.Mint } 'bad' { $theme.Danger } default { $theme.Sky } }
        Add-RichEntry -Box $rtbChat -Prefix ("{0} :: " -f $Speaker) -Text $Text -PrefixColor $color -BodyColor $theme.Text
    }
    $setFooter = {
        param([string]$Text, [string]$Kind = 'info')
        $footerLabel.Text = $Text
        $footerLabel.ForeColor = switch ($Kind) { 'good' { $theme.Mint } 'warn' { $theme.Gold } 'bad' { $theme.Danger } default { $theme.Muted } }
    }
    $selectedProject = {
        if (-not $appState.SelectedProjectName) { return $null }
        return ($projectRecords | Where-Object { $_.Name -eq $appState.SelectedProjectName } | Select-Object -First 1)
    }
    $updateSignal = {
        $project = & $selectedProject
        $endpointText = if ($appState.LastEndpoint) { $appState.LastEndpoint } else { 'not checked yet' }
        $projectText = if ($project) { $project.Name } else { 'none selected' }
        $nextText = if (-not $appState.Healthy) { 'Next: use Operations and run Test Link.' } elseif ($tabs.SelectedTab -ne $tabStudio) { 'Next: use the command deck or return to Assistant.' } else { 'Next: use the command deck to ask, plan, or generate.' }
        $txtSignalSnapshot.Text = @(
            ('Action : {0}' -f $appState.LastAction),
            ('Endpoint: {0}' -f $endpointText),
            ('Project : {0}' -f $projectText),
            $nextText
        ) -join [Environment]::NewLine
        Set-ChipState -Chip $chipSession -Text ('Session: ' + $appState.LastAction) -Mode $(if ($appState.Healthy) { 'good' } else { 'info' })
        Set-ChipState -Chip $chipEndpoint -Text ('Endpoint: ' + $endpointText) -Mode $(if ($appState.Healthy) { 'good' } else { 'warn' })
        Set-ChipState -Chip $chipProject -Text ('Project: ' + $projectText) -Mode $(if ($project) { 'good' } else { 'info' })
    }
    $setSelectedProject = {
        param([string]$Name)
        $syncingProjects = $true
        try {
            $appState.SelectedProjectName = [string]$Name
            if ($cmbStudioProjects.SelectedItem -ne $Name) { $cmbStudioProjects.SelectedItem = $Name }
            if ($cmbProjectsView.SelectedItem -ne $Name) { $cmbProjectsView.SelectedItem = $Name }
        } finally { $syncingProjects = $false }
    }
    $refreshProjectViews = {
        $project = & $selectedProject
        $txtProjectSummary.Text = Get-ProjectSummaryText -Project $project
        $txtProjectContext.Text = if ($project) { Get-ProjectContextText -Project $project -DetailLines 10 } else { 'Select a project to ground Studio prompts in real repo context.' }
        & $updateSignal
    }
    $refreshProjects = {
        $projectRecords = Get-ProjectRecords
        $current = [string]$appState.SelectedProjectName
        $cmbStudioProjects.Items.Clear()
        $cmbProjectsView.Items.Clear()
        foreach ($record in $projectRecords) {
            [void]$cmbStudioProjects.Items.Add($record.Name)
            [void]$cmbProjectsView.Items.Add($record.Name)
        }
        if ($projectRecords.Count -gt 0) {
            if (-not $current -or -not ($projectRecords | Where-Object { $_.Name -eq $current })) { $current = [string]$projectRecords[0].Name }
            & $setSelectedProject $current
        } else {
            & $setSelectedProject ''
        }
        & $refreshProjectViews
        & $appendActivity ("Loaded {0} project folders." -f $projectRecords.Count) 'good'
    }
    $collectState = {
        $portValue = 22
        try { $portValue = [int]$txtPort.Text.Trim() } catch { $portValue = 22 }
        @{
            HostName = $txtHost.Text.Trim(); UserName = $txtUser.Text.Trim(); KeyPath = $txtKey.Text.Trim(); Password = $txtPassword.Text
            Port = $portValue; RemoteBaseDir = $txtRemoteBase.Text.Trim(); ServerBaseUrl = $txtBaseUrl.Text.Trim().TrimEnd('/')
            ControlToken = $txtToken.Text.Trim(); NodeKey = $txtNodeKey.Text.Trim(); FileRoot = $FileRoot; RemotePath = $RemotePath
            LocalPath = $LocalPath; SourcePath = $SourcePath; InstallRoot = $txtInstallRoot.Text.Trim(); ModelDataRoot = $txtModelRoot.Text.Trim()
            ModelMode = [string]$cmbModelMode.SelectedItem; MonitorIntervalSec = [int]$numInterval.Value
            SkipModelBootstrap = $chkSkipModel.Checked; SkipPrereqInstall = $chkSkipPrereq.Checked; NoBrowser = $chkNoBrowser.Checked
        }
    }
    $saveSettings = {
        $state = & $collectState
        $map = Read-LauncherEnv -Path $envPath
        foreach ($pair in @(
            @{ K = 'LUMINAX_HOSTNAME'; V = $state.HostName }, @{ K = 'LUMINAX_USERNAME'; V = $state.UserName }, @{ K = 'LUMINAX_KEY_PATH'; V = $state.KeyPath },
            @{ K = 'LUMINAX_SSH_PASSWORD'; V = $state.Password }, @{ K = 'LUMINAX_SSH_PORT'; V = [string]$state.Port }, @{ K = 'LUMINAX_SERVER_BASE_URL'; V = $state.ServerBaseUrl },
            @{ K = 'LUMINAX_CONTROL_TOKEN'; V = $state.ControlToken }, @{ K = 'LUMINAX_REMOTE_BASE_DIR'; V = $state.RemoteBaseDir }, @{ K = 'LUMINAX_INSTALL_ROOT'; V = $state.InstallRoot },
            @{ K = 'LUMINAX_MODEL_DATA_ROOT'; V = $state.ModelDataRoot }, @{ K = 'LUMINAX_NODE_KEY'; V = $state.NodeKey }
        )) { $map[$pair.K] = [string]$pair.V }
        Write-LauncherEnv -Path $envPath -Map $map
        $appState.LastAction = 'Profile saved'
        & $appendActivity ("Saved launcher settings to {0}" -f $envPath) 'good'
        & $setFooter 'Saved launcher settings.' 'good'
        & $updateSignal
    }
    $ensureHealthyConnection = {
        try {
            $probe = Test-LuminaxConnection -State (& $collectState)
            if ($probe.ok) {
                $txtBaseUrl.Text = $probe.base_url
                $appState.LastEndpoint = $probe.base_url
                $appState.Healthy = $true
                $appState.LastAction = 'Link healthy'
                & $appendActivity ("Healthy Mount Olympus endpoint: {0}" -f $probe.base_url) 'good'
                if ($probe.payload) { & $appendOutput ("Health payload: {0}" -f ($probe.payload | ConvertTo-Json -Depth 8 -Compress)) 'info' }
                & $saveSettings
                & $setFooter 'Connection healthy.' 'good'
                return $probe.base_url
            }
            $appState.Healthy = $false
            $appState.LastAction = 'Link failed'
            & $appendActivity ("Connection failed: {0}" -f $probe.error) 'bad'
            & $appendOutput ("Connection failed: {0}" -f $probe.error) 'bad'
            & $setFooter 'Connection failed.' 'bad'
            & $updateSignal
            return $null
        } catch {
            $appState.Healthy = $false
            $appState.LastAction = 'Link failed'
            & $appendActivity ("ERROR testing connection: {0}" -f $_.Exception.Message) 'bad'
            & $appendOutput ("ERROR testing connection: {0}" -f $_.Exception.Message) 'bad'
            & $setFooter 'Connection test failed.' 'bad'
            & $updateSignal
            return $null
        }
    }
    $runControllerAction = {
        param([string]$ControllerMode, [string]$Label)
        try {
            & $saveSettings
            $appState.LastAction = $Label
            & $appendActivity ("Starting {0}..." -f $Label) 'warn'
            & $appendOutput ("Starting {0}..." -f $Label) 'warn'
            & $setFooter ("Running {0}..." -f $Label) 'warn'
            Invoke-ControllerMode -ControllerMode $ControllerMode -State (& $collectState) -OnOutput { param([string]$Line) & $appendOutput $Line 'info' }
            $null = & $ensureHealthyConnection
            $appState.LastAction = ('Completed ' + $Label)
            & $appendActivity ("Completed {0}." -f $Label) 'good'
            & $setFooter ("Completed {0}." -f $Label) 'good'
            & $updateSignal
        } catch {
            $appState.LastAction = ('Failed ' + $Label)
            & $appendActivity ("ERROR during {0}: {1}" -f $Label, $_.Exception.Message) 'bad'
            & $appendOutput ("ERROR during {0}: {1}" -f $Label, $_.Exception.Message) 'bad'
            & $setFooter ("Failed {0}." -f $Label) 'bad'
            & $updateSignal
        }
    }
    $openProjectFolder = { $p = & $selectedProject; if ($p) { Start-Process explorer.exe $p.Path | Out-Null } else { & $setFooter 'Select a project first.' 'warn' } }
    $openProjectShell = { $p = & $selectedProject; if ($p) { Start-ProjectShell -Path $p.Path } else { & $setFooter 'Select a project first.' 'warn' } }
    $openProjectCode = {
        $p = & $selectedProject
        if (-not $p) { & $setFooter 'Select a project first.' 'warn'; return }
        if (-not (Open-PathInCode -Path $p.Path)) { & $appendActivity 'VS Code command not found on PATH.' 'bad'; & $setFooter 'VS Code command not found.' 'bad' }
    }
    $openFeedbackFolder = {
        $p = & $selectedProject
        if (-not $p) { & $setFooter 'Select a project first.' 'warn'; return }
        if (-not (Test-Path $p.FeedbackPath)) { New-Item -ItemType Directory -Path $p.FeedbackPath -Force | Out-Null }
        Start-Process explorer.exe $p.FeedbackPath | Out-Null
    }
    $getLocalOllamaHost = {
        $host = [string]$env:OLLAMA_HOST
        if (-not $host) { $host = 'http://127.0.0.1:11434' }
        return $host.TrimEnd('/')
    }
    $getLocalFastModel = {
        $model = [string]$env:OLLAMA_CHAT_MODEL_FAST
        if (-not $model) { $model = 'qwen2.5:14b' }
        return $model
    }
    $getLocalImageLane = {
        $candidates = @(
            @{ Name = 'Automatic1111'; Probe = 'http://127.0.0.1:7860/sdapi/v1/sd-models'; OpenUrl = 'http://127.0.0.1:7860/' },
            @{ Name = 'ComfyUI'; Probe = 'http://127.0.0.1:8188/system_stats'; OpenUrl = 'http://127.0.0.1:8188/' },
            @{ Name = 'Fooocus'; Probe = 'http://127.0.0.1:7865/'; OpenUrl = 'http://127.0.0.1:7865/' }
        )
        foreach ($candidate in $candidates) {
            try {
                $null = Invoke-WebRequest -MaximumRedirection 0 -Uri $candidate.Probe -UseBasicParsing -TimeoutSec 4
                return [pscustomobject]@{
                    ready = $true
                    name = [string]$candidate.Name
                    open_url = [string]$candidate.OpenUrl
                    detail = 'ready'
                }
            } catch {}
        }
        return [pscustomobject]@{
            ready = $false
            name = ''
            open_url = ''
            detail = 'No local image tool detected. Start ComfyUI, Automatic1111, or Fooocus on this machine.'
        }
    }
    $applyPromptTemplate = {
        param([string]$Text)
        $txtStudioPrompt.Text = [string]$Text
        $tabs.SelectedTab = $tabStudio
        $txtStudioPrompt.Focus()
        $txtStudioPrompt.SelectionStart = $txtStudioPrompt.Text.Length
        $txtStudioPrompt.SelectionLength = 0
    }
    $refreshAssistantTools = {
        $lines = New-Object System.Collections.Generic.List[string]
        $localHost = & $getLocalOllamaHost
        $localModel = & $getLocalFastModel
        $lines.Add('Assistant seat')
        $lines.Add('--------------')
        try {
            $tagPayload = Invoke-CurlJsonGet -Uri "$localHost/api/tags" -TimeoutSec 6
            $localModels = @($tagPayload.models | ForEach-Object { [string]$_.name } | Where-Object { $_ } | Select-Object -First 6)
            $lines.Add('Local Fast: ready')
            $lines.Add(('Local host: {0}' -f $localHost))
            $lines.Add(('Preferred model: {0}' -f $localModel))
            if ($localModels.Count -gt 0) {
                $lines.Add(('Installed: {0}' -f ($localModels -join ', ')))
            }
        } catch {
            $lines.Add('Local Fast: unavailable')
            $lines.Add(('Reason: {0}' -f ([string]$_.Exception.Message).Split([Environment]::NewLine)[0]))
        }
        $imageLane = & $getLocalImageLane
        $appState.LocalImageLaneReady = [bool]$imageLane.ready
        $appState.LocalImageLaneName = [string]$imageLane.name
        $appState.LocalImageLaneUrl = [string]$imageLane.open_url
        $lines.Add('')
        if ($imageLane.ready) {
            $lines.Add(('Local image: {0} ready' -f $imageLane.name))
            $lines.Add(('Open: {0}' -f $imageLane.open_url))
        } else {
            $lines.Add('Local image: unavailable')
            $lines.Add([string]$imageLane.detail)
        }
        try {
            $probe = Test-LuminaxConnection -State (& $collectState)
            if ($probe.ok) {
                $txtBaseUrl.Text = $probe.base_url
                $appState.LastEndpoint = $probe.base_url
                $appState.Healthy = $true
                $toolDoc = Invoke-CurlJsonGet -Uri "$($probe.base_url.TrimEnd('/'))/api/media/tools" -TimeoutSec 12 -UserName ($txtUser.Text.Trim()) -Password $txtPassword.Text
                $imageTools = @($toolDoc.tools | Where-Object { ([string]$_.type).Trim().ToLowerInvariant() -eq 'image' })
                if ($imageTools.Count -gt 0) {
                    $primaryTool = $imageTools[0]
                    $appState.SelectedImageToolKey = [string]$primaryTool.tool_key
                    $lines.Add('')
                    $lines.Add('Image lane')
                    $lines.Add('----------')
                    $lines.Add(('Tool: {0}' -f [string]$primaryTool.tool_key))
                    $lines.Add(('Provider: {0}' -f [string]$primaryTool.provider))
                    $lines.Add(('Model: {0}' -f [string]$primaryTool.model))
                    $lines.Add(('Status: {0}' -f [string]$primaryTool.status))
                } else {
                    $lines.Add('')
                    $lines.Add('Image lane: no image tool advertised by the server.')
                    $appState.SelectedImageToolKey = ''
                }
            } else {
                $appState.Healthy = $false
                $lines.Add('')
                $lines.Add('Mount Olympus server tools: unavailable')
                $lines.Add(($probe.error | Out-String).Trim())
            }
        } catch {
            $appState.Healthy = $false
            $lines.Add('')
            $lines.Add('Mount Olympus server tools: unavailable')
            $lines.Add(([string]$_.Exception.Message).Split([Environment]::NewLine)[0])
        }
        if ($appState.LastAssetUrl) {
            $lines.Add('')
            $lines.Add(('Last asset: {0}' -f $appState.LastAssetUrl))
        }
        $txtAssistantTools.Text = ($lines -join [Environment]::NewLine)
        & $updateSignal
    }
    $invokeLocalFastRequest = {
        param([string]$PromptText)
        $message = [string]$PromptText
        if (-not $message -or -not $message.Trim()) { & $setFooter 'Write a prompt first.' 'warn'; return }
        $project = & $selectedProject
        $finalPrompt = $message.Trim()
        if ($txtStudioLens.Text.Trim()) { $finalPrompt += "`n`nUse this role or lens:`n$($txtStudioLens.Text.Trim())" }
        if ($project) { $finalPrompt += "`n`nProject context:`n$(Get-ProjectContextText -Project $project -DetailLines 8)" }
        $localHost = & $getLocalOllamaHost
        $localModel = & $getLocalFastModel
        $tabs.SelectedTab = $tabStudio
        & $appendChat 'You' $message 'user'
        & $appendActivity ('Sending request to Local Fast model.') 'info'
        try {
            $response = Invoke-CurlJsonPost -Uri "$localHost/api/chat" -Body @{
                model = $localModel
                stream = $false
                messages = @(@{ role = 'user'; content = $finalPrompt })
            } -TimeoutSec 180
            $replyText = [string]$response.message.content
            if (-not $replyText) { $replyText = ($response | ConvertTo-Json -Depth 10) }
            $appState.LastAction = 'Local Fast replied'
            & $appendChat 'Local Fast' $replyText 'assistant'
            & $setFooter 'Local Fast replied.' 'good'
            & $updateSignal
        } catch {
            $appState.LastAction = 'Local Fast failed'
            & $appendChat 'System' ("Local Fast request failed: {0}" -f $_.Exception.Message) 'bad'
            & $appendActivity ("Local Fast request failed: {0}" -f $_.Exception.Message) 'bad'
            & $setFooter 'Local Fast request failed.' 'bad'
            & $updateSignal
        }
    }
    $generateImageAsset = {
        $prompt = $txtStudioPrompt.Text.Trim()
        if (-not $prompt) { & $setFooter 'Write an image prompt first.' 'warn'; return }
        $baseUrl = & $ensureHealthyConnection
        if (-not $baseUrl) { return }
        $controlToken = $txtToken.Text.Trim()
        if (-not $controlToken) { & $setFooter 'Control token is required for image generation.' 'warn'; return }
        $project = & $selectedProject
        $projectKey = Get-ProjectKey -Project $project
        $targetName = 'assistant_{0}.png' -f (Get-Date -Format 'yyyyMMdd_HHmmss')
        $outputTarget = if ($projectKey) { 'project:{0}/generated/{1}' -f $projectKey, $targetName } else { 'generated:images/{0}' -f $targetName }
        try {
            $tabs.SelectedTab = $tabStudio
            & $appendActivity 'Generating image asset through the Mount Olympus media pipeline.' 'warn'
            $response = Invoke-CurlJsonPost -Uri "$($baseUrl.TrimEnd('/'))/api/media/jobs/create" -Body @{
                token = $controlToken
                media_type = 'image'
                prompt = $prompt
                model_key = [string]$appState.SelectedImageToolKey
                output_target = $outputTarget
                wait_for_completion = $true
                settings = @{
                    output_format = 'png'
                    background = 'auto'
                }
            } -TimeoutSec 300 -UserName ($txtUser.Text.Trim()) -Password $txtPassword.Text
            $job = $response.job
            $assetUrl = [string]$job.asset_url
            if ($assetUrl) {
                if ($assetUrl.StartsWith('http')) {
                    $appState.LastAssetUrl = $assetUrl
                } else {
                    $appState.LastAssetUrl = $baseUrl.TrimEnd('/') + $assetUrl
                }
            }
            $summary = @(
                ('Image job: {0}' -f [string]$job.job_id),
                ('Provider: {0}' -f [string]$job.provider),
                ('Model: {0}' -f [string]$job.model),
                ('Status: {0}' -f [string]$job.status),
                ('Asset: {0}' -f [string]$appState.LastAssetUrl)
            ) -join [Environment]::NewLine
            $appState.LastAction = 'Image generated'
            & $appendChat 'Media Studio' $summary 'system'
            & $appendOutput ($job | ConvertTo-Json -Depth 10) 'good'
            & $appendActivity 'Image generation completed.' 'good'
            & $setFooter 'Image generated.' 'good'
            & $refreshAssistantTools
        } catch {
            $appState.LastAction = 'Image generation failed'
            & $appendChat 'System' ("Image generation failed: {0}" -f $_.Exception.Message) 'bad'
            & $appendActivity ("Image generation failed: {0}" -f $_.Exception.Message) 'bad'
            & $setFooter 'Image generation failed.' 'bad'
            & $updateSignal
        }
    }
    $openLastAssistantAsset = {
        if (-not $appState.LastAssetUrl) { & $setFooter 'No generated asset yet.' 'warn'; return }
        Start-Process $appState.LastAssetUrl | Out-Null
    }
    $openLocalImageLane = {
        $lane = & $getLocalImageLane
        $appState.LocalImageLaneReady = [bool]$lane.ready
        $appState.LocalImageLaneName = [string]$lane.name
        $appState.LocalImageLaneUrl = [string]$lane.open_url
        if ($appState.LocalImageLaneReady -and $appState.LocalImageLaneUrl) {
            Start-Process $appState.LocalImageLaneUrl | Out-Null
            & $setFooter ("Opened {0}." -f $appState.LocalImageLaneName) 'good'
            return
        }
        & $appendActivity 'No local image lane is running on this machine right now.' 'warn'
        & $setFooter 'No local image lane detected. Start ComfyUI, Automatic1111, or Fooocus first.' 'warn'
    }
    $invokeStudioRequest = {
        param([string]$PromptText, [string]$ModeName = 'chat')
        $message = [string]$PromptText
        if (-not $message -or -not $message.Trim()) { & $setFooter 'Write a prompt first.' 'warn'; return }
        $project = & $selectedProject
        $finalPrompt = $message.Trim()
        if ($txtStudioLens.Text.Trim()) { $finalPrompt += "`n`nUse this role or lens:`n$($txtStudioLens.Text.Trim())" }
        if ($project) { $finalPrompt += "`n`nProject context:`n$(Get-ProjectContextText -Project $project -DetailLines 10)" }
        $baseUrl = & $ensureHealthyConnection
        if (-not $baseUrl) { return }
        $tabs.SelectedTab = $tabStudio
        & $appendChat 'You' $message 'user'
        $txtStudioPrompt.Clear()
        try {
            $response = Invoke-CurlJsonPost -Uri "$($baseUrl.TrimEnd('/'))/api/chat" -Body @{ prompt = $finalPrompt } -TimeoutSec 300 -UserName ($txtUser.Text.Trim()) -Password $txtPassword.Text
            $replyText = [string]$response.response
            if (-not $replyText) { $replyText = ($response | ConvertTo-Json -Depth 12) }
            $appState.LastAction = ('Replied at ' + (Get-Date).ToString('HH:mm'))
            & $appendChat 'Zeus' $replyText 'assistant'
            & $appendActivity ("Zeus handled a {0} request." -f $ModeName) 'good'
            & $setFooter 'Zeus replied.' 'good'
            & $updateSignal
        } catch {
            $appState.LastAction = 'Chat failed'
            & $appendChat 'System' ("Chat request failed: {0}" -f $_.Exception.Message) 'bad'
            & $appendActivity ("Chat request failed: {0}" -f $_.Exception.Message) 'bad'
            & $setFooter 'Chat request failed.' 'bad'
            & $updateSignal
        }
    }
    $queueTask = {
        $goal = $txtStudioPrompt.Text.Trim()
        if (-not $goal) { & $setFooter 'Write the task goal first.' 'warn'; return }
        $project = & $selectedProject
        $baseUrl = & $ensureHealthyConnection
        if (-not $baseUrl) { return }
        try {
            $tabs.SelectedTab = $tabStudio
            $taskResponse = Invoke-CurlJsonPost -Uri "$($baseUrl.TrimEnd('/'))/api/tasks/create" -Body @{
                goal = $goal; project_key = Get-ProjectKey -Project $project; entails = $txtStudioLens.Text.Trim(); source = 'windows-studio'; requested_by = 'windows-operator'; due_date = ''
            } -TimeoutSec 120 -UserName ($txtUser.Text.Trim()) -Password $txtPassword.Text
            $appState.LastAction = 'Task queued'
            & $appendActivity 'Queued a task for Zeus.' 'good'
            & $appendOutput ("Task queue response: {0}" -f ($taskResponse | ConvertTo-Json -Depth 10 -Compress)) 'good'
            & $setFooter 'Task queued.' 'good'
            & $updateSignal
        } catch {
            $appState.LastAction = 'Task queue failed'
            & $appendActivity ("Task queue failed: {0}" -f $_.Exception.Message) 'bad'
            & $appendOutput ("Task queue failed: {0}" -f $_.Exception.Message) 'bad'
            & $setFooter 'Task queue failed.' 'bad'
            & $updateSignal
        }
    }

    $btnClearActivity.Add_Click({ $rtbActivity.Clear() })
    $tabs.Add_SelectedIndexChanged({ & $updateSignal })
    $btnHeroSave.Add_Click({ & $saveSettings })
    $btnHeroTest.Add_Click({ $null = & $ensureHealthyConnection; & $refreshAssistantTools })
    $btnHeroDeploy.Add_Click({ & $runControllerAction 'all' 'deploy + connect'; & $refreshAssistantTools })
    $btnContextRefresh.Add_Click({ & $refreshProjects })
    $btnProjectsRefresh.Add_Click({ & $refreshProjects })
    $cmbStudioProjects.Add_SelectedIndexChanged({ if (-not $syncingProjects) { & $setSelectedProject ([string]$cmbStudioProjects.SelectedItem); & $refreshProjectViews } })
    $cmbProjectsView.Add_SelectedIndexChanged({ if (-not $syncingProjects) { & $setSelectedProject ([string]$cmbProjectsView.SelectedItem); & $refreshProjectViews } })
    $btnStudioFolder.Add_Click({ & $openProjectFolder })
    $btnStudioCode.Add_Click({ & $openProjectCode })
    $btnStudioShell.Add_Click({ & $openProjectShell })
    $btnProjectFolder.Add_Click({ & $openProjectFolder })
    $btnProjectCode.Add_Click({ & $openProjectCode })
    $btnProjectShell.Add_Click({ & $openProjectShell })
    $btnProjectFeedbackFolder.Add_Click({ & $openFeedbackFolder })
    $btnSave.Add_Click({ & $saveSettings })
    $btnTest.Add_Click({ $null = & $ensureHealthyConnection })
    $btnInstall.Add_Click({ & $runControllerAction 'all' 'deploy + connect' })
    $btnServerOnly.Add_Click({ & $runControllerAction 'server-only' 'server-only deploy' })
    $btnMonitor.Add_Click({ & $runControllerAction 'monitor-only' 'monitor install' })
    $btnDashboard.Add_Click({ & $runControllerAction 'dashboard' 'dashboard open' })
    $btnStatus.Add_Click({ & $runControllerAction 'status' 'status check' })
    $btnLogs.Add_Click({ $reportsPath = Join-Path $PSScriptRoot 'reports'; if (-not (Test-Path $reportsPath)) { New-Item -ItemType Directory -Path $reportsPath -Force | Out-Null }; Start-Process explorer.exe $reportsPath | Out-Null })
    $btnInstallFolder.Add_Click({ $state = & $collectState; if (-not $state.InstallRoot) { & $setFooter 'Install root is blank.' 'warn'; return }; if (-not (Test-Path $state.InstallRoot)) { New-Item -ItemType Directory -Path $state.InstallRoot -Force | Out-Null }; Start-Process explorer.exe $state.InstallRoot | Out-Null })
    $btnStudioSend.Add_Click({ & $invokeStudioRequest $txtStudioPrompt.Text 'chat' })
    $btnStudioLocal.Add_Click({ & $invokeLocalFastRequest $txtStudioPrompt.Text })
    $btnStudioPlan.Add_Click({ $p = $txtStudioPrompt.Text.Trim(); if (-not $p) { & $setFooter 'Write a prompt first.' 'warn'; return }; & $invokeStudioRequest ("Turn this into a concise execution plan with phases, risks, and next actions:`n`n{0}" -f $p) 'plan' })
    $btnStudioTask.Add_Click({ & $queueTask })
    $btnStudioClear.Add_Click({ $rtbChat.Clear(); $tabs.SelectedTab = $tabStudio; & $appendChat 'Zeus' 'Fresh assistant transcript ready.' 'system'; & $setFooter 'Chat cleared.' 'good' })
    $btnStudioImage.Add_Click({ & $generateImageAsset })
    $btnAssistantRefresh.Add_Click({ & $refreshAssistantTools; & $appendActivity 'Assistant tools refreshed.' 'good' })
    $btnAssistantAsset.Add_Click({ & $openLastAssistantAsset })
    $btnOpenLocalImageLane.Add_Click({ & $openLocalImageLane })
    $btnStarterPlan.Add_Click({ & $applyPromptTemplate 'Build my current todo list into a clean, prioritized plan with must-do, should-do, and later sections.' })
    $btnStarterReview.Add_Click({ & $applyPromptTemplate 'Design a realistic exercise plan for me today with warmup, main work, cool down, and a fallback shorter version.' })
    $btnStarterDraft.Add_Click({ & $applyPromptTemplate 'Help me clarify my current life goals, the top three priorities, and the next practical step for each one.' })
    $btnStarterFix.Add_Click({ & $applyPromptTemplate 'Set a reminder draft for me. Ask for the key timing details if they are missing, and give me a clean reminder message I can use.' })
    $btnAddDetail.Add_Click({
        $project = & $selectedProject; $text = $txtDetailEntry.Text.Trim()
        if (-not $project) { & $setFooter 'Select a project first.' 'warn'; return }
        if (-not $text) { & $setFooter 'Write a detail line first.' 'warn'; return }
        $entry = Add-ProjectDetailsLine -Project $project -Text $text
        $txtDetailEntry.Clear(); & $appendActivity ("Added detail line to {0}." -f $project.Name) 'good'; & $appendOutput $entry 'good'; & $setFooter 'Project detail added.' 'good'; & $refreshProjectViews
    })
    $btnAddFeedback.Add_Click({
        $project = & $selectedProject; $text = $txtFeedbackEntry.Text.Trim()
        if (-not $project) { & $setFooter 'Select a project first.' 'warn'; return }
        if (-not $text) { & $setFooter 'Write feedback first.' 'warn'; return }
        $notePath = Write-ProjectFeedbackNote -Project $project -Text $text
        $txtFeedbackEntry.Clear(); & $appendActivity ("Captured feedback for {0}." -f $project.Name) 'good'; & $appendOutput ("Feedback note: {0}" -f $notePath) 'good'; & $setFooter 'Feedback captured.' 'good'
    })
    $txtStudioPrompt.Add_KeyDown({ param($sender, $eventArgs) if ($eventArgs.Control -and $eventArgs.KeyCode -eq [System.Windows.Forms.Keys]::Enter) { $eventArgs.SuppressKeyPress = $true; & $invokeStudioRequest $txtStudioPrompt.Text 'chat' } })
    foreach ($tipInfo in @(
        @{ Control = $txtStudioPrompt; Text = 'Type here from any tab. Ctrl+Enter sends to Zeus.' },
        @{ Control = $btnStudioSend; Text = 'Send the current prompt to Zeus and the Mount Olympus command spine.' },
        @{ Control = $btnStudioLocal; Text = 'Use the local Ollama fast model for quick drafts and project help.' },
        @{ Control = $btnStudioImage; Text = 'Generate an image asset through the live Mount Olympus media lane.' },
        @{ Control = $btnOpenLocalImageLane; Text = 'Open ComfyUI, Automatic1111, or Fooocus if one is running locally.' },
        @{ Control = $btnStarterPlan; Text = 'Prefill a personal todo planning prompt.' },
        @{ Control = $btnStarterFix; Text = 'Prefill a reminder creation prompt.' },
        @{ Control = $btnStarterReview; Text = 'Prefill an exercise-planning prompt.' },
        @{ Control = $btnStarterDraft; Text = 'Prefill a life-goals coaching prompt.' },
        @{ Control = $btnStudioFolder; Text = 'Jump straight into the selected project folder.' },
        @{ Control = $btnStudioCode; Text = 'Open the selected project in VS Code.' }
    )) {
        $toolTips.SetToolTip($tipInfo.Control, $tipInfo.Text)
    }
    $form.Add_Shown({
        $form.WindowState = 'Normal'
        $form.TopMost = $true
        $form.Activate()
        $form.BringToFront()
        [System.Windows.Forms.Application]::DoEvents()
        $form.TopMost = $false
        & $refreshProjects
        $tabs.SelectedTab = $tabStudio
        & $appendActivity 'Windows operator studio ready.' 'good'
    & $appendChat 'Zeus' 'Assistant is ready. The command deck is live, Local Fast is available on-box, and the image lane status is listed to the right.' 'system'
        & $refreshAssistantTools
        & $setFooter 'Assistant ready.' 'good'
        & $updateSignal
    })
    [void]$form.ShowDialog()
}

if ($Mode -eq 'app') {
    try {
        Start-LuminaxWindowsStudioApp
        exit 0
    } catch {
        Write-Error ("Failed to start the Mount Olympus Windows Studio app: " + $_.Exception.Message)
        exit 1
    }
}

$controllerMode = switch ($Mode) {
    'install' { 'all' }
    'server-only' { 'server-only' }
    'monitor' { 'monitor-only' }
    'dashboard' { 'dashboard' }
    'status' { 'status' }
    'files-list' { 'files-list' }
    'file-read' { 'file-read' }
    'file-write' { 'file-write' }
}

$state = @{
    HostName = $HostName
    UserName = $UserName
    KeyPath = $KeyPath
    Password = $Password
    Port = $Port
    RemoteBaseDir = $RemoteBaseDir
    ServerBaseUrl = $ServerBaseUrl
    ControlToken = $ControlToken
    NodeKey = $NodeKey
    FileRoot = $FileRoot
    RemotePath = $RemotePath
    LocalPath = $LocalPath
    SourcePath = $SourcePath
    InstallRoot = $InstallRoot
    ModelDataRoot = $ModelDataRoot
    ModelMode = $ModelMode
    MonitorIntervalSec = $MonitorIntervalSec
    SkipModelBootstrap = [bool]$SkipModelBootstrap
    SkipPrereqInstall = [bool]$SkipPrereqInstall
    NoBrowser = [bool]$NoBrowser
}

Invoke-ControllerMode -ControllerMode $controllerMode -State $state
