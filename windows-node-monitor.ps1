param(
    [Parameter(Mandatory = $true)]
    [string]$ServerBaseUrl,
    [string]$ControlToken = "",
    [string]$NodeKey = "",
    [int]$IntervalSec = 30,
    [int]$JobPollMax = 6,
    [string]$LogPath = (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'ProjectLight\windows-node-monitor.log'),
    [string]$PrivateControlUrl = "",
    [string]$PrivateControlMode = "ssh_tunnel",
    [string]$ResourcePolicyPath = (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'ProjectLight\windows-node-policy.json'),
    [int]$ThrottleCpuPercent = 70,
    [int]$ThrottleMemoryPercent = 80,
    [int]$ThrottleGpuPercent = 82,
    [int]$OffloadCpuPercent = 85,
    [int]$OffloadMemoryPercent = 90,
    [int]$OffloadGpuPercent = 92,
    [switch]$EnableToolBroker,
    [switch]$RunOnce
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot 'tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'windows-node-monitor.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:ControlTokenSource = "unknown"

if (-not $ServerBaseUrl) {
    $ServerBaseUrl = $env:LUMINAX_SERVER_BASE_URL
}
if ($ControlToken) {
    $script:ControlTokenSource = "explicit_arg"
}
if (-not $ControlToken) {
    $ControlToken = $env:LUMINAX_CONTROL_TOKEN
    if ($ControlToken) {
        $script:ControlTokenSource = "environment"
    }
}
if (-not $ControlToken) {
    throw "Control token missing. Pass -ControlToken or set LUMINAX_CONTROL_TOKEN."
}
if (-not $NodeKey) {
    if ($env:LUMINAX_NODE_KEY) {
        $NodeKey = $env:LUMINAX_NODE_KEY
    } else {
        throw 'Set an explicit non-personal NodeKey before registering this node.'
    }
}
if (-not $PrivateControlUrl) {
    if ($env:LUMINAX_PRIVATE_CONTROL_URL) {
        $PrivateControlUrl = [string]$env:LUMINAX_PRIVATE_CONTROL_URL
    } else {
        $PrivateControlUrl = "http://127.0.0.1:18040"
    }
}
if ($env:LUMINAX_PRIVATE_CONTROL_MODE) {
    $PrivateControlMode = [string]$env:LUMINAX_PRIVATE_CONTROL_MODE
}
if ($IntervalSec -lt 10) { $IntervalSec = 10 }
if ($JobPollMax -lt 1) { $JobPollMax = 1 }
if ($JobPollMax -gt 25) { $JobPollMax = 25 }
$script:MonitorDebug = $RunOnce.IsPresent
$script:LastResourcePolicyMode = ""

$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$AllowedRoots = @(
    (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax'),
    (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'workspace'),
    ((Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'workspace') -f $env:USERNAME)
)

$AllowedCommandPrefixes = @(
    "Get-Process",
    "Get-Service",
    "Get-ChildItem",
    "Get-Item",
    "Get-CimInstance",
    "Test-Path",
    "whoami",
    "hostname",
    "systeminfo",
    "wsl.exe -l -v",
    "docker ps",
    "ollama list"
)

$AudioBoardRoot = (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'ProjectLight\audio-board')
$AudioBoardSamplesDir = Join-Path $AudioBoardRoot "samples"
$AudioBoardExportsDir = Join-Path $AudioBoardRoot "streamdeck"
$AudioBoardSlotsPath = Join-Path $AudioBoardRoot "slots.json"
$AudioBoardRunnerPath = Join-Path $AudioBoardRoot "Invoke-AudioBoardSlot.ps1"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date).ToString("s"), $Message
    Add-Content -Path $LogPath -Value $line
}

function Write-DebugLog {
    param([string]$Message)
    if ($script:MonitorDebug) {
        Write-Log ("debug: " + $Message)
    }
}

function Write-PolicyReceipt {
    param([hashtable]$Payload)
    $dir = Split-Path -Parent $ResourcePolicyPath
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $Payload | ConvertTo-Json -Depth 10 | Set-Content -Path $ResourcePolicyPath -Encoding UTF8
}

function Test-HttpHealth {
    param(
        [string]$Url,
        [int]$TimeoutSec = 3
    )

    try {
        $response = Invoke-RestMethod -MaximumRedirection 0 -Uri $Url -Method Get -TimeoutSec $TimeoutSec
        return @{
            ok = $true
            payload = $response
        }
    } catch {
        return @{
            ok = $false
            error = $_.Exception.Message
        }
    }
}

function ConvertTo-CompatHashtable {
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Collections.IDictionary]) {
        $table = @{}
        foreach ($key in $Value.Keys) {
            $table[[string]$key] = ConvertTo-CompatHashtable -Value $Value[$key]
        }
        return $table
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $table = @{}
        foreach ($prop in $Value.PSObject.Properties) {
            $table[$prop.Name] = ConvertTo-CompatHashtable -Value $prop.Value
        }
        return $table
    }
    if (($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string])) {
        $items = @()
        foreach ($item in $Value) {
            $items += ,(ConvertTo-CompatHashtable -Value $item)
        }
        return $items
    }
    return $Value
}

function ConvertFrom-JsonCompat {
    param([string]$Json)
    return ConvertTo-CompatHashtable -Value ($Json | ConvertFrom-Json)
}

function Invoke-PrivateControlTunnelJson {
    param([ValidateSet('ensure','status','stop')] [string]$Action = 'status')
    $helperPath = Join-Path $PSScriptRoot 'windows-private-control-tunnel.ps1'
    if (-not (Test-Path $helperPath)) {
        return @{
            action = $Action
            healthy = $false
            status = 'helper_missing'
            error = "Tunnel helper missing: $helperPath"
            url = $PrivateControlUrl
        }
    }
    try {
        $raw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helperPath -Apply -Action $Action 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            throw $raw
        }
        $payload = ConvertFrom-JsonCompat -Json $raw
        if (-not $payload.ContainsKey('healthy')) {
            $payload['healthy'] = [bool]($payload['status'] -in @('started', 'already_healthy'))
        }
        if (-not $payload.ContainsKey('url')) {
            $payload['url'] = $PrivateControlUrl
        }
        return $payload
    } catch {
        return @{
            action = $Action
            healthy = $false
            status = 'error'
            error = $_.Exception.Message
            url = $PrivateControlUrl
        }
    }
}

function Get-GpuRuntimeMetrics {
    $rows = @()
    try {
        $result = & nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>$null
        if ($LASTEXITCODE -eq 0 -and $result) {
            foreach ($line in @($result)) {
                $parts = [string]$line -split ','
                if ($parts.Count -lt 3) { continue }
                $util = 0.0
                $memUsed = 0.0
                $memTotal = 0.0
                [void][double]::TryParse($parts[0].Trim(), [ref]$util)
                [void][double]::TryParse($parts[1].Trim(), [ref]$memUsed)
                [void][double]::TryParse($parts[2].Trim(), [ref]$memTotal)
                $memPct = 0.0
                if ($memTotal -gt 0) {
                    $memPct = [math]::Round(($memUsed / $memTotal) * 100, 2)
                }
                $rows += @{
                    utilization_percent = [math]::Round($util, 2)
                    memory_used_mb = [math]::Round($memUsed, 2)
                    memory_total_mb = [math]::Round($memTotal, 2)
                    memory_percent = $memPct
                }
            }
        }
    } catch {
        $rows = @()
    }

    $peakUtil = 0.0
    $peakMemPct = 0.0
    foreach ($row in $rows) {
        if ([double]$row.utilization_percent -gt $peakUtil) { $peakUtil = [double]$row.utilization_percent }
        if ([double]$row.memory_percent -gt $peakMemPct) { $peakMemPct = [double]$row.memory_percent }
    }
    return @{
        rows = @($rows)
        peak_utilization_percent = [math]::Round($peakUtil, 2)
        peak_memory_percent = [math]::Round($peakMemPct, 2)
    }
}

function Get-PressureSnapshot {
    $cpu = 0.0
    try {
        $cpuCounter = (Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction Stop).CounterSamples[0].CookedValue
        $cpu = [math]::Round([double]$cpuCounter, 2)
    } catch {
        $cpu = 0.0
    }

    $memPct = 0.0
    try {
        $mem = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        if ($mem.TotalVisibleMemorySize -gt 0) {
            $memPct = [math]::Round((($mem.TotalVisibleMemorySize - $mem.FreePhysicalMemory) / $mem.TotalVisibleMemorySize) * 100, 2)
        }
    } catch {
        $memPct = 0.0
    }

    $gpuMetrics = Get-GpuRuntimeMetrics
    return @{
        cpu_percent = $cpu
        memory_percent = $memPct
        gpu = $gpuMetrics
    }
}

function Resolve-ResourcePolicy {
    param(
        [hashtable]$PressureSnapshot,
        [hashtable]$TunnelState
    )

    $cpu = [double]$PressureSnapshot.cpu_percent
    $memory = [double]$PressureSnapshot.memory_percent
    $gpu = if ($PressureSnapshot.gpu) { [double]$PressureSnapshot.gpu.peak_utilization_percent } else { 0.0 }
    $privateHealthy = [bool]$TunnelState.healthy
    $mode = 'observe'
    $reason = 'Within normal thresholds.'

    if ($cpu -ge $OffloadCpuPercent -or $memory -ge $OffloadMemoryPercent -or $gpu -ge $OffloadGpuPercent) {
        if ($privateHealthy) {
            $mode = 'offload'
            $reason = 'High resource pressure detected; background work should offload to the server path.'
        } else {
            $mode = 'degraded-local-only'
            $reason = 'High resource pressure detected while the private control path is unavailable.'
        }
    } elseif ($cpu -ge $ThrottleCpuPercent -or $memory -ge $ThrottleMemoryPercent -or $gpu -ge $ThrottleGpuPercent) {
        $mode = 'throttle'
        $reason = 'Elevated resource pressure detected; background work should be throttled locally.'
    }

    $effectiveJobPollMax = $JobPollMax
    $allowToolBroker = [bool]$EnableToolBroker
    switch ($mode) {
        'throttle' {
            $effectiveJobPollMax = [Math]::Max(1, [Math]::Floor($JobPollMax / 2))
        }
        'offload' {
            $effectiveJobPollMax = 0
            $allowToolBroker = $false
        }
        'degraded-local-only' {
            $effectiveJobPollMax = 0
            $allowToolBroker = $false
        }
    }

    return @{
        mode = $mode
        reason = $reason
        private_control_healthy = $privateHealthy
        allow_tool_broker = $allowToolBroker
        effective_job_poll_max = $effectiveJobPollMax
        offload_preferred = [bool]($mode -eq 'offload')
        interactive_local = $true
        cpu_percent = $cpu
        memory_percent = $memory
        gpu_percent = $gpu
    }
}

function Ensure-AudioBoardPaths {
    foreach ($path in @($AudioBoardRoot, $AudioBoardSamplesDir, $AudioBoardExportsDir)) {
        if (-not (Test-Path $path)) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
    }
    if (-not (Test-Path $AudioBoardSlotsPath)) {
        '{}' | Set-Content -Path $AudioBoardSlotsPath -Encoding UTF8
    }
    if (-not (Test-Path $AudioBoardRunnerPath)) {
        @'
param(
    [Parameter(Mandatory = $true)]
    [string]$SlotKey
)

$monitor = Join-Path $PSScriptRoot "..\windows-node-monitor.ps1"
if (-not (Test-Path $monitor)) {
    throw "windows-node-monitor.ps1 not found near the audio board."
}

$slotsPath = Join-Path $PSScriptRoot "slots.json"
if (-not (Test-Path $slotsPath)) {
    throw "slots.json not found."
}

$json = Get-Content -Path $slotsPath -Raw
$slots = @{}
if ($json) {
    try { $slots = ConvertFrom-JsonCompat -Json $json } catch { $slots = @{} }
}
$slot = $slots[$SlotKey]
if (-not $slot) {
    throw "Slot '$SlotKey' not found."
}

Write-Host ("Slot ready: " + ($(if ($slot.label) { $slot.label } else { $SlotKey })))
Write-Host "Use Luminax Audio Board or windows node action polling to execute this slot."
'@ | Set-Content -Path $AudioBoardRunnerPath -Encoding UTF8
    }
}

function Initialize-AudioInterop {
    if ("LuminaxAudio.AudioDeviceManager" -as [type]) {
        return
    }
    Add-Type -Language CSharp -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace LuminaxAudio {
    public enum EDataFlow { eRender = 0, eCapture = 1, eAll = 2, EDataFlow_enum_count = 3 }
    public enum ERole { eConsole = 0, eMultimedia = 1, eCommunications = 2, ERole_enum_count = 3 }
    [Flags]
    public enum DEVICE_STATE : uint { ACTIVE = 0x1, DISABLED = 0x2, NOTPRESENT = 0x4, UNPLUGGED = 0x8, MASK_ALL = 0xF }
    public enum STGM : uint { STGM_READ = 0x00000000 }

    [StructLayout(LayoutKind.Sequential)]
    public struct PROPERTYKEY {
        public Guid fmtid;
        public uint pid;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct PROPVARIANT {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr pointerValue;
        public string GetValue() {
            if (vt == 31 && pointerValue != IntPtr.Zero) {
                return Marshal.PtrToStringUni(pointerValue);
            }
            return null;
        }
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    internal class MMDeviceEnumeratorComObject {}

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
    interface IMMDeviceEnumerator {
        int EnumAudioEndpoints(EDataFlow dataFlow, DEVICE_STATE dwStateMask, out IMMDeviceCollection ppDevices);
        int GetDefaultAudioEndpoint(EDataFlow dataFlow, ERole role, out IMMDevice ppDevice);
        int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string pwstrId, out IMMDevice ppDevice);
        int RegisterEndpointNotificationCallback(IntPtr pClient);
        int UnregisterEndpointNotificationCallback(IntPtr pClient);
    }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("0BD7A1BE-7A1A-44DB-8397-C0A176496AC9")]
    interface IMMDeviceCollection {
        int GetCount(out uint pcDevices);
        int Item(uint nDevice, out IMMDevice ppDevice);
    }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("D666063F-1587-4E43-81F1-B948E807363F")]
    interface IMMDevice {
        int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
        int OpenPropertyStore(STGM stgmAccess, out IPropertyStore ppProperties);
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
        int GetState(out DEVICE_STATE pdwState);
    }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")]
    interface IPropertyStore {
        int GetCount(out uint cProps);
        int GetAt(uint iProp, out PROPERTYKEY pkey);
        int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        int SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
        int Commit();
    }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("F8679F50-850A-41CF-9C72-430F290290C8")]
    interface IPolicyConfig {
        int GetMixFormat();
        int GetDeviceFormat();
        int ResetDeviceFormat();
        int SetDeviceFormat();
        int GetProcessingPeriod();
        int SetProcessingPeriod();
        int GetShareMode();
        int SetShareMode();
        int GetPropertyValue();
        int SetPropertyValue();
        int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string wszDeviceId, ERole role);
        int SetEndpointVisibility();
    }

    [ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")]
    class PolicyConfigClient {}

    public class AudioDeviceInfo {
        public string Id { get; set; }
        public string FriendlyName { get; set; }
        public string Flow { get; set; }
        public string State { get; set; }
        public bool DefaultConsole { get; set; }
        public bool DefaultMultimedia { get; set; }
        public bool DefaultCommunications { get; set; }
    }

    public static class AudioDeviceManager {
        static readonly PROPERTYKEY FriendlyNameKey = new PROPERTYKEY { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };

        static IMMDeviceEnumerator Enumerator() {
            return (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        }

        static string ReadFriendlyName(IMMDevice device) {
            IPropertyStore store;
            Marshal.ThrowExceptionForHR(device.OpenPropertyStore(STGM.STGM_READ, out store));
            PROPVARIANT value;
            Marshal.ThrowExceptionForHR(store.GetValue(ref FriendlyNameKey, out value));
            return value.GetValue() ?? "";
        }

        static Dictionary<string, HashSet<ERole>> DefaultMap() {
            var map = new Dictionary<string, HashSet<ERole>>(StringComparer.OrdinalIgnoreCase);
            var enumerator = Enumerator();
            foreach (EDataFlow flow in new[] { EDataFlow.eRender, EDataFlow.eCapture }) {
                foreach (ERole role in new[] { ERole.eConsole, ERole.eMultimedia, ERole.eCommunications }) {
                    try {
                        IMMDevice device;
                        Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(flow, role, out device));
                        string id;
                        Marshal.ThrowExceptionForHR(device.GetId(out id));
                        if (!map.ContainsKey(id)) {
                            map[id] = new HashSet<ERole>();
                        }
                        map[id].Add(role);
                    } catch { }
                }
            }
            return map;
        }

        public static List<AudioDeviceInfo> ListDevices() {
            var list = new List<AudioDeviceInfo>();
            var defaults = DefaultMap();
            var enumerator = Enumerator();
            foreach (var pair in new[] {
                new KeyValuePair<EDataFlow,string>(EDataFlow.eRender, "render"),
                new KeyValuePair<EDataFlow,string>(EDataFlow.eCapture, "capture"),
            }) {
                IMMDeviceCollection devices;
                Marshal.ThrowExceptionForHR(enumerator.EnumAudioEndpoints(pair.Key, DEVICE_STATE.MASK_ALL, out devices));
                uint count;
                Marshal.ThrowExceptionForHR(devices.GetCount(out count));
                for (uint i = 0; i < count; i++) {
                    IMMDevice device;
                    Marshal.ThrowExceptionForHR(devices.Item(i, out device));
                    string id;
                    Marshal.ThrowExceptionForHR(device.GetId(out id));
                    DEVICE_STATE state;
                    Marshal.ThrowExceptionForHR(device.GetState(out state));
                    HashSet<ERole> roles;
                    defaults.TryGetValue(id, out roles);
                    list.Add(new AudioDeviceInfo {
                        Id = id,
                        FriendlyName = ReadFriendlyName(device),
                        Flow = pair.Value,
                        State = state.ToString(),
                        DefaultConsole = roles != null && roles.Contains(ERole.eConsole),
                        DefaultMultimedia = roles != null && roles.Contains(ERole.eMultimedia),
                        DefaultCommunications = roles != null && roles.Contains(ERole.eCommunications),
                    });
                }
            }
            return list;
        }

        public static void SetDefaultEndpoint(string deviceId, string roleName) {
            if (string.IsNullOrWhiteSpace(deviceId)) {
                throw new ArgumentException("deviceId is required");
            }
            ERole role;
            switch ((roleName ?? "multimedia").Trim().ToLowerInvariant()) {
                case "console":
                    role = ERole.eConsole;
                    break;
                case "communications":
                    role = ERole.eCommunications;
                    break;
                default:
                    role = ERole.eMultimedia;
                    break;
            }
            var policy = (IPolicyConfig)(new PolicyConfigClient());
            Marshal.ThrowExceptionForHR(policy.SetDefaultEndpoint(deviceId, role));
        }
    }
}
"@
}

function Get-ServerUrlCandidates {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($PrivateControlUrl) {
        $candidates.Add($PrivateControlUrl.TrimEnd('/'))
    }
    if ($ServerBaseUrl) {
        $base = $ServerBaseUrl.TrimEnd('/')
        if (-not $candidates.Contains($base)) {
            $candidates.Add($base)
        }
    }

    $resolvedHost = ""
    try {
        $parsed = [Uri]$ServerBaseUrl
        $resolvedHost = $parsed.Host
    } catch {
        $resolvedHost = ($ServerBaseUrl -replace '^https?://', '').Split('/')[0].Split(':')[0]
    }

    if (-not $resolvedHost) {
        $resolvedHost = $env:LUMINAX_HOSTNAME
    }

    if ($resolvedHost) {
        foreach ($candidate in @(
            ('http://{0}:8040' -f $resolvedHost),
            "https://$resolvedHost",
            ('https://{0}:443' -f $resolvedHost)
        )) {
            if (-not $candidates.Contains($candidate)) {
                $candidates.Add($candidate)
            }
        }
    }

    return @($candidates)
}

function Invoke-CurlJson {
    param(
        [string]$Method,
        [string]$Uri,
        [hashtable]$Body = @{},
        [int]$TimeoutSec = 20
    )
    $args = @(
        '--silent',
        '--show-error',
        '--http1.1',
        '--connect-timeout', '8',
        '--max-time', "$TimeoutSec",
        '--output', '-',
        '--write-out', "`n%{http_code}",
        '--request', $Method,
        '--url', $Uri
    )
    $tempBodyPath = ""
    if ($Method -ne "GET") {
        $jsonBody = $Body | ConvertTo-Json -Depth 16 -Compress
        $tempBodyPath = [System.IO.Path]::GetTempFileName()
        [System.IO.File]::WriteAllText($tempBodyPath, $jsonBody, [System.Text.UTF8Encoding]::new($false))
        $args += @('--header', 'Content-Type: application/json', '--data-binary', ("@{0}" -f $tempBodyPath))
    }
    try {
        $rawLines = & curl.exe @args 2>&1
        $exitCode = $LASTEXITCODE
        $raw = (($rawLines | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
    } finally {
        if ($tempBodyPath -and (Test-Path $tempBodyPath)) {
            Remove-Item -Path $tempBodyPath -Force -ErrorAction SilentlyContinue
        }
    }
    if ($exitCode -ne 0) {
        throw "curl.exe failed with exit code $exitCode for ${Uri}: $raw"
    }
    $statusCode = 0
    $body = $raw
    if ($raw -match "(?s)^(.*)\r?\n(\d{3})$") {
        $body = ($matches[1] -as [string]).Trim()
        $statusCode = [int]$matches[2]
    }
    if ($statusCode -lt 200 -or $statusCode -ge 300) {
        throw ("curl.exe returned HTTP {0} for {1}: {2}" -f $statusCode, $Uri, ($body -as [string]))
    }
    if (-not $body) {
        return $null
    }
    return ($body | ConvertFrom-Json)
}

$script:LastServerCandidateErrors = @{}
$script:ResolvedServerBaseUrl = ""

function Test-ServerCandidate {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSec = 8
    )
    $paths = @('/api/health', '/routes/catalog')
    foreach ($path in $paths) {
        $uri = "{0}{1}" -f $BaseUrl.TrimEnd('/'), $path
        try {
            if ($PSVersionTable.PSVersion.Major -lt 7) {
                $null = Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method Get -TimeoutSec $TimeoutSec -UseBasicParsing
            } else {
                $null = Invoke-RestMethod -MaximumRedirection 0 -Uri $uri -Method Get -TimeoutSec $TimeoutSec
            }
            if ($script:LastServerCandidateErrors.ContainsKey($BaseUrl)) {
                $script:LastServerCandidateErrors.Remove($BaseUrl) | Out-Null
            }
            return $true
        } catch {
            $script:LastServerCandidateErrors[$BaseUrl] = ("{0} => {1}" -f $path, $_.Exception.Message)
        }
    }
    return $false
}

function Resolve-ServerBaseUrl {
    param([switch]$ForceRefresh)
    if (-not $ForceRefresh -and $script:ResolvedServerBaseUrl) {
        return $script:ResolvedServerBaseUrl
    }

    $script:LastServerCandidateErrors = @{}
    foreach ($candidate in Get-ServerUrlCandidates) {
        if (Test-ServerCandidate -BaseUrl $candidate) {
            if ($script:ResolvedServerBaseUrl -ne $candidate) {
                Write-Log ("resolved server base url: " + $candidate)
            }
            $script:ResolvedServerBaseUrl = $candidate
            return $candidate
        }
    }

    $diagnostic = ""
    if ($script:LastServerCandidateErrors.Count -gt 0) {
        $parts = @()
        foreach ($pair in $script:LastServerCandidateErrors.GetEnumerator()) {
            $parts += ("{0} => {1}" -f [string]$pair.Key, [string]$pair.Value)
        }
        $diagnostic = " Details: " + ($parts -join " | ")
    }
    throw ("Unable to resolve a reachable Luminax server URL from configured candidates." + $diagnostic)
}

function Invoke-ServerJson {
    param(
        [string]$Path,
        [string]$Method = "GET",
        [hashtable]$Body = @{},
        [int]$TimeoutSec = 20
    )
    $baseAttempts = New-Object System.Collections.Generic.List[string]
    if ($ServerBaseUrl) {
        $explicitBase = $ServerBaseUrl.TrimEnd("/")
        if ($explicitBase -and -not $baseAttempts.Contains($explicitBase)) {
            $baseAttempts.Add($explicitBase)
        }
    }
    foreach ($candidate in @(
        $(try { Resolve-ServerBaseUrl } catch { $null }),
        $(try { Resolve-ServerBaseUrl -ForceRefresh } catch { $null })
    )) {
        if ($candidate -and -not $baseAttempts.Contains($candidate)) {
            $baseAttempts.Add($candidate)
        }
    }

    $lastError = $null
    foreach ($baseUrl in $baseAttempts) {
        $url = "{0}{1}" -f $baseUrl.TrimEnd("/"), $Path
        $invokeParams = @{
            Uri = $url
            Method = $Method
            TimeoutSec = $TimeoutSec
        }
        if ($Method -ne "GET") {
            $invokeParams["ContentType"] = "application/json"
            $invokeParams["Body"] = ($Body | ConvertTo-Json -Depth 16)
        }
        try {
            if ($PSVersionTable.PSVersion.Major -lt 7) {
                return Invoke-RestMethod @invokeParams -UseBasicParsing
            }
            return Invoke-RestMethod @invokeParams
        } catch {
            $lastError = $_
            $script:ResolvedServerBaseUrl = ""
        }
    }

    $diagnostic = ""
    if ($script:LastServerCandidateErrors.Count -gt 0) {
        $parts = @()
        foreach ($pair in $script:LastServerCandidateErrors.GetEnumerator()) {
            $parts += ("{0} => {1}" -f [string]$pair.Key, [string]$pair.Value)
        }
        $diagnostic = " Details: " + ($parts -join " | ")
    }
    if ($lastError) {
        $lastMessage = if ($lastError.Exception) { $lastError.Exception.Message } else { [string]$lastError }
        throw ($lastMessage + $diagnostic)
    }
    throw ("Unable to reach Luminax server." + $diagnostic)
}

function Get-ServiceStateMap {
    param([string[]]$Patterns)
    $map = @{}
    $services = Get-Service | ForEach-Object { $_ }
    foreach ($p in $Patterns) {
        $hits = $services | Where-Object { $_.Name -like $p -or $_.DisplayName -like $p }
        $map[$p] = @($hits | ForEach-Object {
            @{
                name = $_.Name
                display_name = $_.DisplayName
                status = "$($_.Status)"
            }
        })
    }
    return $map
}

function Get-ProcessPresent {
    param([string[]]$Names)
    $present = @{}
    foreach ($n in $Names) {
        $hit = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like $n }
        $present[$n] = [bool]($hit)
    }
    return $present
}

function Get-SafeAudioFileName {
    param([string]$Name, [string]$DefaultStem = "sample")
    $rawInput = ""
    if ($null -ne $Name) { $rawInput = [string]$Name }
    $raw = [System.IO.Path]::GetFileNameWithoutExtension($rawInput.Trim())
    if (-not $raw) { $raw = $DefaultStem }
    $safe = ($raw.ToCharArray() | Where-Object { [char]::IsLetterOrDigit($_) -or $_ -in @('_','-','.') }) -join ''
    if (-not $safe) { $safe = $DefaultStem }
    return ($safe.Trim('.'))
}

function Read-AudioBoardSlots {
    Ensure-AudioBoardPaths
    if (-not (Test-Path $AudioBoardSlotsPath)) { return @{} }
    $raw = Get-Content -Path $AudioBoardSlotsPath -Raw -ErrorAction SilentlyContinue
    if (-not $raw) { return @{} }
    try {
        return (ConvertFrom-JsonCompat -Json $raw)
    } catch {
        return @{}
    }
}

function Write-AudioBoardSlots {
    param([hashtable]$Slots)
    Ensure-AudioBoardPaths
    ($Slots | ConvertTo-Json -Depth 16) | Set-Content -Path $AudioBoardSlotsPath -Encoding UTF8
}

function Get-AudioDeviceInventory {
    Initialize-AudioInterop
    $devices = [LuminaxAudio.AudioDeviceManager]::ListDevices() | ForEach-Object {
        @{
            id = $_.Id
            name = $_.FriendlyName
            flow = $_.Flow
            state = $_.State
            default_console = [bool]$_.DefaultConsole
            default_multimedia = [bool]$_.DefaultMultimedia
            default_communications = [bool]$_.DefaultCommunications
        }
    }
    $render = @($devices | Where-Object { $_.flow -eq "render" })
    $capture = @($devices | Where-Object { $_.flow -eq "capture" })
    return @{
        render = $render
        capture = $capture
        defaults = @{
            render = @{
                console = ($render | Where-Object { $_.default_console } | Select-Object -First 1)
                multimedia = ($render | Where-Object { $_.default_multimedia } | Select-Object -First 1)
                communications = ($render | Where-Object { $_.default_communications } | Select-Object -First 1)
            }
            capture = @{
                console = ($capture | Where-Object { $_.default_console } | Select-Object -First 1)
                multimedia = ($capture | Where-Object { $_.default_multimedia } | Select-Object -First 1)
                communications = ($capture | Where-Object { $_.default_communications } | Select-Object -First 1)
            }
        }
    }
}

function Set-AudioDefaultDevice {
    param(
        [string]$DeviceId,
        [string[]]$Roles
    )
    Initialize-AudioInterop
    if (-not $DeviceId) { throw "device_id is required" }
    $rolesToApply = @($Roles | Where-Object { $_ })
    if (-not $rolesToApply.Count) { $rolesToApply = @("multimedia", "communications") }
    foreach ($role in $rolesToApply) {
        [LuminaxAudio.AudioDeviceManager]::SetDefaultEndpoint($DeviceId, [string]$role)
    }
    $inventory = Get-AudioDeviceInventory
    $all = @($inventory.render + $inventory.capture)
    $selected = $all | Where-Object { $_.id -eq $DeviceId } | Select-Object -First 1
    return @{
        device_id = $DeviceId
        roles = @($rolesToApply)
        selected = $selected
    }
}

function Resolve-AudioSamplePath {
    param([string]$SampleName)
    Ensure-AudioBoardPaths
    $safeStem = Get-SafeAudioFileName -Name $SampleName -DefaultStem "sample"
    return (Join-Path $AudioBoardSamplesDir ($safeStem + ".wav"))
}

function Get-AudioBoardState {
    Ensure-AudioBoardPaths
    $slots = Read-AudioBoardSlots
    $inventory = $null
    try {
        $inventory = Get-AudioDeviceInventory
    } catch {
        $inventory = @{
            render = @()
            capture = @()
            defaults = @{ render = @{}; capture = @{} }
            error = $_.Exception.Message
        }
    }
    $samples = @()
    Get-ChildItem -Path $AudioBoardSamplesDir -Filter *.wav -Force -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 60 | ForEach-Object {
        $samples += @{
            name = $_.Name
            full_name = $_.FullName
            bytes = [int64]$_.Length
            modified = $_.LastWriteTime.ToString("s")
        }
    }
    $exports = @()
    Get-ChildItem -Path $AudioBoardExportsDir -Force -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 40 | ForEach-Object {
        $exports += @{
            name = $_.Name
            full_name = $_.FullName
            modified = $_.LastWriteTime.ToString("s")
        }
    }
    return @{
        root = $AudioBoardRoot
        samples = $samples
        sample_count = @($samples).Count
        slots = $slots
        slot_keys = @($slots.Keys | Sort-Object)
        export_files = $exports
        export_count = @($exports).Count
        devices = $inventory
        updated_at = (Get-Date).ToString("o")
    }
}

function Invoke-ServerBinaryRequest {
    param(
        [string]$Path,
        [hashtable]$Body,
        [string]$OutFile,
        [int]$TimeoutSec = 60
    )
    $baseAttempts = New-Object System.Collections.Generic.List[string]
    foreach ($candidate in @(
        $(try { Resolve-ServerBaseUrl } catch { $null }),
        $(try { Resolve-ServerBaseUrl -ForceRefresh } catch { $null })
    )) {
        if ($candidate -and -not $baseAttempts.Contains($candidate)) {
            $baseAttempts.Add($candidate)
        }
    }
    $lastError = $null
    foreach ($baseUrl in $baseAttempts) {
        $url = "{0}{1}" -f $baseUrl.TrimEnd("/"), $Path
        $jsonBody = $Body | ConvertTo-Json -Depth 16
        try {
            if ($PSVersionTable.PSVersion.Major -lt 7) {
                try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
                try { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } } catch {}
                Invoke-WebRequest -MaximumRedirection 0 -Uri $url -Method Post -TimeoutSec $TimeoutSec -ContentType "application/json" -Body $jsonBody -OutFile $OutFile | Out-Null
            } else {
                Invoke-WebRequest -MaximumRedirection 0 -Uri $url -Method Post -TimeoutSec $TimeoutSec -ContentType "application/json" -Body $jsonBody -OutFile $OutFile | Out-Null
            }
            return @{ status = "ok"; url = $url; out_file = $OutFile }
        } catch {
            $lastError = $_
            $script:ResolvedServerBaseUrl = ""
        }
    }
    if ($lastError) { throw $lastError }
    throw "Unable to reach Luminax server for binary request."
}

function Invoke-PlayWavFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { throw "Audio file not found: $Path" }
    Add-Type -AssemblyName System
    $player = New-Object System.Media.SoundPlayer $Path
    $player.Load()
    $player.PlaySync()
    return @{
        played = $true
        path = $Path
        bytes = [int64](Get-Item $Path).Length
    }
}

function Invoke-AudioTtsRender {
    param(
        [string]$Text,
        [string]$Voice,
        [string]$Emotion,
        [string]$SaveName
    )
    if (-not $Text) { throw "text is required" }
    $targetName = if ($SaveName) { [string]$SaveName } else { "tts_sample" }
    $targetPath = Resolve-AudioSamplePath -SampleName $targetName
    Invoke-ServerBinaryRequest -Path "/voice/speak" -Body @{
        text = $Text
        voice = $Voice
        emotion = if ($Emotion) { $Emotion } else { "warm" }
        format = "wav"
    } -OutFile $targetPath -TimeoutSec 120 | Out-Null
    return @{
        status = "rendered"
        sample_name = [System.IO.Path]::GetFileName($targetPath)
        saved_path = $targetPath
    }
}

function Invoke-AudioCloneRender {
    param(
        [string]$Text,
        [string]$VoiceRef,
        [string]$SaveName
    )
    if (-not $Text) { throw "text is required" }
    if (-not $VoiceRef) { throw "voice_ref is required" }
    $targetName = if ($SaveName) { [string]$SaveName } else { "clone_sample" }
    $targetPath = Resolve-AudioSamplePath -SampleName $targetName
    Invoke-ServerBinaryRequest -Path "/voice/clone" -Body @{
        text = $Text
        voice_ref = $VoiceRef
    } -OutFile $targetPath -TimeoutSec 180 | Out-Null
    return @{
        status = "rendered"
        sample_name = [System.IO.Path]::GetFileName($targetPath)
        saved_path = $targetPath
        voice_ref = $VoiceRef
    }
}

function Invoke-AudioSamplePlay {
    param(
        [string]$SampleName,
        [string]$DeviceId,
        [string[]]$Roles
    )
    if (-not $SampleName) { throw "sample_name is required" }
    $samplePath = Resolve-AudioSamplePath -SampleName $SampleName
    if (-not (Test-Path $samplePath)) { throw "sample not found: $SampleName" }
    $route = $null
    if ($DeviceId) {
        $route = Set-AudioDefaultDevice -DeviceId $DeviceId -Roles $Roles
    }
    $play = Invoke-PlayWavFile -Path $samplePath
    return @{
        sample_name = [System.IO.Path]::GetFileName($samplePath)
        saved_path = $samplePath
        route = $route
        play = $play
    }
}

function Save-AudioSoundboardSlot {
    param(
        [string]$SlotKey,
        [string]$Label,
        [string]$Mode,
        [string]$SampleName,
        [string]$Text,
        [string]$Voice,
        [string]$Emotion,
        [string]$VoiceRef,
        [string]$DeviceId,
        [string[]]$Roles
    )
    if (-not $SlotKey) { throw "slot_key is required" }
    $slots = Read-AudioBoardSlots
    $safeSlot = Get-SafeAudioFileName -Name $SlotKey -DefaultStem "slot"
    $slots[$safeSlot] = @{
        slot_key = $safeSlot
        label = if ($Label) { $Label } else { $safeSlot }
        mode = if ($Mode) { $Mode } else { "sample" }
        sample_name = $SampleName
        text = $Text
        voice = $Voice
        emotion = $Emotion
        voice_ref = $VoiceRef
        device_id = $DeviceId
        roles = @($Roles | Where-Object { $_ })
        updated_at = (Get-Date).ToString("o")
    }
    Write-AudioBoardSlots -Slots $slots
    return $slots[$safeSlot]
}

function Invoke-AudioSoundboardSlot {
    param([string]$SlotKey)
    if (-not $SlotKey) { throw "slot_key is required" }
    $slots = Read-AudioBoardSlots
    $safeSlot = Get-SafeAudioFileName -Name $SlotKey -DefaultStem "slot"
    $slot = $slots[$safeSlot]
    if (-not $slot) { throw "soundboard slot not found: $safeSlot" }
    $mode = if ($slot.mode) { [string]$slot.mode } else { "sample" }
    switch ($mode) {
        "tts" {
            $render = Invoke-AudioTtsRender -Text ([string]$slot.text) -Voice ([string]$slot.voice) -Emotion ([string]$slot.emotion) -SaveName ($(if ($slot.sample_name) { [string]$slot.sample_name } else { $safeSlot }))
            $play = Invoke-AudioSamplePlay -SampleName $render.sample_name -DeviceId ([string]$slot.device_id) -Roles @($slot.roles)
            return @{ slot = $slot; render = $render; play = $play }
        }
        "clone" {
            $render = Invoke-AudioCloneRender -Text ([string]$slot.text) -VoiceRef ([string]$slot.voice_ref) -SaveName ($(if ($slot.sample_name) { [string]$slot.sample_name } else { $safeSlot }))
            $play = Invoke-AudioSamplePlay -SampleName $render.sample_name -DeviceId ([string]$slot.device_id) -Roles @($slot.roles)
            return @{ slot = $slot; render = $render; play = $play }
        }
        default {
            $play = Invoke-AudioSamplePlay -SampleName ([string]$slot.sample_name) -DeviceId ([string]$slot.device_id) -Roles @($slot.roles)
            return @{ slot = $slot; play = $play }
        }
    }
}

function Export-StreamDeckLauncher {
    param(
        [string]$SlotKey,
        [string]$Label
    )
    if (-not $SlotKey) { throw "slot_key is required" }
    Ensure-AudioBoardPaths
    $safeSlot = Get-SafeAudioFileName -Name $SlotKey -DefaultStem "slot"
    $labelValue = if ($Label) { [string]$Label } else { $safeSlot }
    $safeLabel = Get-SafeAudioFileName -Name $labelValue -DefaultStem $safeSlot
    $cmdPath = Join-Path $AudioBoardExportsDir ($safeLabel + ".cmd")
    @"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "$AudioBoardRunnerPath" -SlotKey "$safeSlot"
"@ | Set-Content -Path $cmdPath -Encoding ASCII
    return @{
        slot_key = $safeSlot
        export_label = $safeLabel
        launcher_path = $cmdPath
        runner_path = $AudioBoardRunnerPath
    }
}

function Get-PidFileProcessState {
    param(
        [string]$RuntimeRoot,
        [string]$PidFileName
    )

    $pidPath = Join-Path (Join-Path $RuntimeRoot 'pids') $PidFileName
    if (-not (Test-Path $pidPath)) {
        return @{
            status = 'missing'
            healthy = $false
            detail = 'pid file missing'
            pid = $null
        }
    }

    $rawPid = Get-Content -Path $pidPath -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $rawPid -or -not ($rawPid -as [int])) {
        return @{
            status = 'invalid_pid'
            healthy = $false
            detail = 'pid file invalid'
            pid = $null
        }
    }

    $processId = [int]$rawPid
    $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $proc) {
        return @{
            status = 'stale_pid'
            healthy = $false
            detail = 'process not running'
            pid = $processId
        }
    }

    return @{
        status = 'ok'
        healthy = $true
        detail = 'process running'
        pid = $processId
        process_name = $proc.ProcessName
    }
}

function Get-LocalBrainServiceSummary {
    param([string]$RuntimeRoot)

    $services = @(
        @{ key = 'private_control_tunnel'; pid = 'private-control-tunnel.pid'; health = 'http://127.0.0.1:18040/olympus/admin/health?skip_edge_bridge=1&edge_safe=1'; timeout = 4 },
        @{ key = 'affect_controller'; pid = 'affect-controller.pid'; health = 'http://127.0.0.1:8502/health'; timeout = 3 },
        @{ key = 'luminax_brain'; pid = 'luminax-brain.pid'; health = 'http://127.0.0.1:8501/health'; timeout = 3 },
        @{ key = 'mcp_router'; pid = 'mcp-router.pid'; health = 'http://127.0.0.1:8503/health'; timeout = 3 },
        @{ key = 'langgraph_orchestrator'; pid = 'langgraph-orchestrator.pid'; health = 'http://127.0.0.1:8504/health'; timeout = 3 },
        @{ key = 'voice_agent'; pid = 'voice-agent.pid'; health = 'http://127.0.0.1:8030/health'; timeout = 3 },
        @{ key = 'olympus_edge_bridge'; pid = 'olympus-edge-bridge.pid'; health = 'http://127.0.0.1:8150/health'; timeout = 8 },
        @{ key = 'desktop_shell'; pid = 'desktop-shell.pid'; health = ''; timeout = 0 }
    )

    $summary = @{}
    foreach ($service in $services) {
        $state = Get-PidFileProcessState -RuntimeRoot $RuntimeRoot -PidFileName ([string]$service.pid)
        if ($service.health) {
            $probe = Test-HttpHealth -Url ([string]$service.health) -TimeoutSec ([int]$service.timeout)
            $state.health_url = [string]$service.health
            if ($probe.ok) {
                $state.healthy = $true
                $state.detail = 'health probe ok'
            } elseif ($state.healthy) {
                $state.healthy = $false
                $state.status = 'probe_failed'
                $state.detail = [string]$probe.error
            } else {
                $state.detail = [string]$probe.error
            }
        }
        $summary[[string]$service.key] = $state
    }

    return $summary
}

function Ensure-DesktopShellRunning {
    param(
        [string]$RuntimeRoot,
        [bool]$InteractiveLocal
    )

    $state = Get-PidFileProcessState -RuntimeRoot $RuntimeRoot -PidFileName 'desktop-shell.pid'
    if ($state.healthy -or -not $InteractiveLocal) {
        return $state
    }

    $starterPath = Join-Path $PSScriptRoot 'windows-start-local-brain.ps1'
    if (-not (Test-Path $starterPath)) {
        $state.detail = "shell starter missing: $starterPath"
        return $state
    }

    Write-Log 'desktop shell missing; restarting local brain shell'
    $null = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $starterPath, '-RuntimeRoot', $RuntimeRoot, '-StartShell') `
        -WindowStyle Hidden

    Start-Sleep -Seconds 3
    return (Get-PidFileProcessState -RuntimeRoot $RuntimeRoot -PidFileName 'desktop-shell.pid')
}

function Build-Payload {
    param(
        [hashtable]$PressureSnapshot = @{},
        [hashtable]$ResourcePolicy = @{},
        [hashtable]$TunnelState = @{}
    )
    Ensure-AudioBoardPaths
    Write-DebugLog "Build-Payload start"
    $svcPatterns = @(
        "MySQL*",
        "*MariaDB*",
        "*Ollama*",
        "*ComfyUI*",
        "*Stable*Diffusion*",
        "*Automatic1111*",
        "*InvokeAI*",
        "*Fooocus*"
    )
    $procNames = @(
        "ollama*",
        "python*",
        "comfyui*",
        "webui*",
        "invokeai*",
        "fooocus*",
        "ffmpeg*"
    )

    Write-DebugLog "Collecting service state map"
    $services = Get-ServiceStateMap -Patterns $svcPatterns
    Write-DebugLog "Collecting process presence"
    $processes = Get-ProcessPresent -Names $procNames
    Write-DebugLog "Collecting GPU inventory"
    $gpu = @()
    try {
        $gpu = Get-CimInstance Win32_VideoController -ErrorAction Stop | ForEach-Object {
            @{
                name = $_.Name
                driver_version = $_.DriverVersion
                adapter_ram_bytes = [int64]($_.AdapterRAM)
            }
        }
    } catch {
        $gpu = @()
    }
    Write-DebugLog ("GPU inventory complete count={0}" -f @($gpu).Count)

    $ffmpegPresent = [bool](Get-Command ffmpeg -ErrorAction SilentlyContinue)
    $pythonPresent = [bool](Get-Command python -ErrorAction SilentlyContinue)
    $ollamaHost = if ($env:OLLAMA_HOST) { $env:OLLAMA_HOST } else { "http://127.0.0.1:11434" }
    if ($ollamaHost -match '^(https?://)?0\.0\.0\.0(?::|/|$)') {
        $ollamaHost = 'http://127.0.0.1:11434'
    }
    $ollamaTags = @()
    $ollamaReachable = $false
    Write-DebugLog ("Probing Ollama at {0}" -f $ollamaHost)
    try {
        $tagResp = Invoke-RestMethod -MaximumRedirection 0 -Uri "$ollamaHost/api/tags" -Method Get -TimeoutSec 8
        $ollamaReachable = $true
        if ($tagResp.models) {
            $ollamaTags = @($tagResp.models | ForEach-Object {
                @{
                    name = $_.name
                    size = $_.size
                    modified_at = $_.modified_at
                }
            })
        }
    } catch {
        $ollamaReachable = $false
    }
    Write-DebugLog ("Ollama probe complete reachable={0} count={1}" -f $ollamaReachable, @($ollamaTags).Count)

    Write-DebugLog "Collecting audio board state"
    $audioBoard = Get-AudioBoardState
    Write-DebugLog ("Audio board state complete samples={0} slots={1}" -f @($audioBoard.samples).Count, @($audioBoard.slot_keys).Count)
    $ollamaSummary = @{
        host = $ollamaHost
        reachable = $ollamaReachable
        model_count = @($ollamaTags).Count
        model_names = @($ollamaTags | Select-Object -First 12 | ForEach-Object { $_.name })
    }
    $audioBoardSummary = @{
        sample_count = @($audioBoard.samples).Count
        slot_count = @($audioBoard.slot_keys).Count
        render_device_count = @($audioBoard.devices.render).Count
        capture_device_count = @($audioBoard.devices.capture).Count
    }
    $localServiceSummary = @{
        mysql = [bool]((@($services["MySQL*"]).Count -gt 0) -or (@($services["*MariaDB*"]).Count -gt 0))
        ollama = [bool](@($services["*Ollama*"]).Count -gt 0)
        comfyui = [bool](@($services["*ComfyUI*"]).Count -gt 0)
        stable_diffusion = [bool](@($services["*Stable*Diffusion*"]).Count -gt 0)
        automatic1111 = [bool](@($services["*Automatic1111*"]).Count -gt 0)
        invokeai = [bool](@($services["*InvokeAI*"]).Count -gt 0)
        fooocus = [bool](@($services["*Fooocus*"]).Count -gt 0)
    }
    $processSummary = @{
        ollama = [bool]$processes["ollama*"]
        python = [bool]$processes["python*"]
        comfyui = [bool]$processes["comfyui*"]
        webui = [bool]$processes["webui*"]
        invokeai = [bool]$processes["invokeai*"]
        fooocus = [bool]$processes["fooocus*"]
        ffmpeg = [bool]$processes["ffmpeg*"]
    }
    $localRuntimeSummary = Get-LocalBrainServiceSummary -RuntimeRoot (Join-Path $PSScriptRoot '_local_runtime\pc-brain')

    $toolBrokerEnabled = if ($ResourcePolicy -and $ResourcePolicy.ContainsKey('allow_tool_broker')) {
        [bool]$ResourcePolicy.allow_tool_broker
    } else {
        [bool]$EnableToolBroker
    }

    $capabilities = @{
        mysql_local = [bool]((@($services["MySQL*"]).Count -gt 0) -or (@($services["*MariaDB*"]).Count -gt 0))
        ollama_local = [bool]((@($services["*Ollama*"]).Count -gt 0) -or $processes["ollama*"] -or $ollamaReachable)
        image_generation = [bool]((@($services["*ComfyUI*"]).Count -gt 0) -or (@($services["*Stable*Diffusion*"]).Count -gt 0) -or $processes["comfyui*"] -or $processes["webui*"] -or $processes["fooocus*"])
        video_generation = [bool]($processes["ffmpeg*"] -or $processes["python*"] -or $ffmpegPresent -or $pythonPresent)
        audio_generation = [bool]($processes["ffmpeg*"] -or $processes["python*"] -or $ffmpegPresent -or $pythonPresent)
        audio_board = $true
        ffmpeg_present = $ffmpegPresent
        python_present = $pythonPresent
        gpu_present = [bool](@($gpu).Count -gt 0)
        ollama_reachable = $ollamaReachable
        ollama_model_count = @($ollamaTags).Count
        tool_broker = $toolBrokerEnabled
        audio_sample_count = @($audioBoard.samples).Count
        audio_slot_count = @($audioBoard.slot_keys).Count
        audio_render_device_count = @($audioBoard.devices.render).Count
        audio_capture_device_count = @($audioBoard.devices.capture).Count
    }

    if (-not $PressureSnapshot -or @($PressureSnapshot.Keys).Count -eq 0) {
        $PressureSnapshot = Get-PressureSnapshot
    }
    $cpu = [math]::Round([double]$PressureSnapshot.cpu_percent, 2)
    $memPct = [math]::Round([double]$PressureSnapshot.memory_percent, 2)
    $gpuMetrics = if ($PressureSnapshot.gpu) { $PressureSnapshot.gpu } else { Get-GpuRuntimeMetrics }
    $gpuPeakPct = [math]::Round([double]$gpuMetrics.peak_utilization_percent, 2)
    $gpuMemoryPeakPct = [math]::Round([double]$gpuMetrics.peak_memory_percent, 2)
    Write-DebugLog "Build-Payload complete"

    $capabilities.private_control_mode = $PrivateControlMode
    $capabilities.private_control_healthy = [bool]$TunnelState.healthy
    $capabilities.private_control_url = $PrivateControlUrl
    $capabilities.accepting_background_jobs = $toolBrokerEnabled
    $capabilities.offload_preferred = [bool]$ResourcePolicy.offload_preferred

    return @{
        token = $ControlToken
        node_key = $NodeKey
        hostname = $env:COMPUTERNAME
        os_version = [System.Environment]::OSVersion.VersionString
        capabilities = $capabilities
        services = @{
            local_services = $localServiceSummary
            process_presence = $processSummary
            local_runtime = $localRuntimeSummary
            host_tools = @{
                ffmpeg = $ffmpegPresent
                python = $pythonPresent
            }
            audio_board = $audioBoardSummary
        }
        metrics = @{
            cpu_percent = [math]::Round([double]$cpu, 2)
            memory_percent = $memPct
            gpu_percent = $gpuPeakPct
            gpu_memory_percent = $gpuMemoryPeakPct
        }
        inventory = @{
            gpus = $gpu
            ollama = $ollamaSummary
            gpu_runtime = $gpuMetrics
        }
        meta = @{
            user = $env:USERNAME
            source = "windows-node-monitor"
            control_token_source = $script:ControlTokenSource
            resolved_server_base_url = $script:ResolvedServerBaseUrl
            sent_at = (Get-Date).ToString("o")
            audio_board = $audioBoardSummary
            local_runtime = $localRuntimeSummary
            private_control = @{
                mode = $PrivateControlMode
                url = $PrivateControlUrl
                tunnel = $TunnelState
            }
            resource_policy = $ResourcePolicy
            inventory = @{
                gpus = $gpu
                ollama = $ollamaSummary
            }
        }
    }
}

function Test-IsAllowedPath {
    param([string]$PathValue)
    if (-not $PathValue) { return $false }
    try {
        $resolved = [System.IO.Path]::GetFullPath($PathValue)
        foreach ($root in $AllowedRoots) {
            $base = [System.IO.Path]::GetFullPath($root)
            if ($resolved.StartsWith($base, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
    } catch {
        return $false
    }
    return $false
}

function Test-IsAllowedCommand {
    param([string]$Command)
    if (-not $Command) { return $false }
    $trimmed = $Command.Trim()
    foreach ($prefix in $AllowedCommandPrefixes) {
        if ($trimmed.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Poll-NodeJobs {
    param([int]$MaxJobs = $JobPollMax)
    try {
        $resp = Invoke-ServerJson -Path "/windows-node/action/poll" -Method "POST" -Body @{
            token = $ControlToken
            node_key = $NodeKey
            max_jobs = $MaxJobs
        } -TimeoutSec 15
        return @($resp.rows)
    } catch {
        Write-Log ("job poll failed: " + $_.Exception.Message)
        return @()
    }
}

function Send-JobResult {
    param(
        [int]$JobId,
        [string]$Status,
        [hashtable]$Result,
        [string]$ErrorText = ""
    )
    try {
        $resp = Invoke-ServerJson -Path "/windows-node/action/complete" -Method "POST" -Body @{
            token = $ControlToken
            node_key = $NodeKey
            job_id = $JobId
            status = $Status
            result = $Result
            error = $ErrorText
        } -TimeoutSec 20
        Write-Log ("job complete ack: " + ($resp | ConvertTo-Json -Compress))
    } catch {
        Write-Log ("job complete send failed (job " + $JobId + "): " + $_.Exception.Message)
    }
}

function Invoke-GuardedCommand {
    param([string]$Command)
    if (-not (Test-IsAllowedCommand -Command $Command)) {
        throw "Command blocked by allowlist."
    }
    $output = & powershell -NoProfile -ExecutionPolicy Bypass -Command $Command 2>&1 | Out-String
    return @{
        command = $Command
        output = ($output | Select-Object -First 1)
    }
}

function Invoke-CaptureScreen {
    param([string]$SavePath)
    $targetPath = $SavePath
    if (-not $targetPath) {
        $dir = (Join-Path (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Luminax') 'ProjectLight\captures')
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $targetPath = Join-Path $dir ("capture_{0}.png" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
    }
    if (-not (Test-IsAllowedPath -PathValue $targetPath)) {
        throw "Capture path is outside allowed roots."
    }
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName System.Windows.Forms
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $bmp.Save($targetPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose()
    $bmp.Dispose()
    return @{ saved_path = $targetPath; width = $bounds.Width; height = $bounds.Height }
}

function Execute-NodeJob {
    param([hashtable]$Job)
    $jobId = [int]($Job.job_id)
    $action = "$($Job.action_key)"
    $payload = $Job.payload
    if (-not $payload) { $payload = @{} }
    $args = $payload.args
    if (-not $args) { $args = @{} }
    $dryRun = [bool]$payload.dry_run

    Write-Log ("job start id={0} action={1} dry_run={2}" -f $jobId, $action, $dryRun)
    try {
        $result = @{}
        switch ($action) {
            "local.tool_probe" {
                $probe = Build-Payload
                $result = @{
                    summary = $probe.capabilities
                    inventory = if ($args.include_inventory) { $probe.inventory } else { @{} }
                }
            }
            "local.service_status" {
                $patterns = @("MySQL*", "*Ollama*", "*ComfyUI*")
                if ($args.patterns) { $patterns = @($args.patterns) }
                $result = @{ services = (Get-ServiceStateMap -Patterns $patterns) }
            }
            "local.open_url" {
                $url = "$($args.url)"
                if (-not $url) { throw "url is required" }
                if ($dryRun) {
                    $result = @{ status = "dry_run"; url = $url }
                } else {
                    Start-Process $url | Out-Null
                    $result = @{ status = "opened"; url = $url }
                }
            }
            "local.file_list" {
                $path = "$($args.path)"
                if (-not $path) { throw "path is required" }
                if (-not (Test-IsAllowedPath -PathValue $path)) { throw "path is outside allowed roots" }
                $maxItems = 120
                if ($args.max_items) { $maxItems = [int]$args.max_items }
                if ($maxItems -lt 1) { $maxItems = 1 }
                if ($maxItems -gt 500) { $maxItems = 500 }
                $items = Get-ChildItem -Path $path -Force -ErrorAction Stop | Select-Object -First $maxItems | ForEach-Object {
                    @{
                        name = $_.Name
                        full_name = $_.FullName
                        is_dir = [bool]$_.PSIsContainer
                        length = if ($_.PSIsContainer) { 0 } else { [int64]$_.Length }
                        modified = $_.LastWriteTime.ToString("s")
                    }
                }
                $result = @{ path = $path; items = @($items) }
            }
            "local.ps" {
                $command = "$($args.command)"
                if (-not $command) { throw "command is required" }
                if ($dryRun) {
                    if (-not (Test-IsAllowedCommand -Command $command)) {
                        throw "command blocked by allowlist"
                    }
                    $result = @{ status = "dry_run"; command = $command; allowed = $true }
                } else {
                    $result = Invoke-GuardedCommand -Command $command
                }
            }
            "local.capture_screen" {
                if ($dryRun) {
                    $result = @{ status = "dry_run"; save_path = "$($args.save_path)" }
                } else {
                    $result = Invoke-CaptureScreen -SavePath "$($args.save_path)"
                }
            }
            "audio.device_inventory" {
                $result = Get-AudioBoardState
            }
            "audio.device_set_default" {
                $deviceId = "$($args.device_id)"
                $roles = @()
                if ($args.roles) { $roles = @($args.roles) }
                if ($dryRun) {
                    $result = @{ status = "dry_run"; device_id = $deviceId; roles = $roles }
                } else {
                    $result = Set-AudioDefaultDevice -DeviceId $deviceId -Roles $roles
                }
            }
            "audio.sample_list" {
                $result = Get-AudioBoardState
            }
            "audio.sample_play" {
                $sampleName = "$($args.sample_name)"
                $deviceId = "$($args.device_id)"
                $roles = @()
                if ($args.roles) { $roles = @($args.roles) }
                if ($dryRun) {
                    $result = @{ status = "dry_run"; sample_name = $sampleName; device_id = $deviceId; roles = $roles }
                } else {
                    $result = Invoke-AudioSamplePlay -SampleName $sampleName -DeviceId $deviceId -Roles $roles
                }
            }
            "audio.tts_render" {
                $text = "$($args.text)"
                $voice = "$($args.voice)"
                $emotion = "$($args.emotion)"
                $saveName = "$($args.save_name)"
                $autoPlay = [bool]$args.auto_play
                $deviceId = "$($args.device_id)"
                $roles = @()
                if ($args.roles) { $roles = @($args.roles) }
                if ($dryRun) {
                    $result = @{ status = "dry_run"; text = $text; voice = $voice; emotion = $emotion; save_name = $saveName; auto_play = $autoPlay; device_id = $deviceId; roles = $roles }
                } else {
                    $render = Invoke-AudioTtsRender -Text $text -Voice $voice -Emotion $emotion -SaveName $saveName
                    $result = @{ render = $render }
                    if ($autoPlay) {
                        $result.play = Invoke-AudioSamplePlay -SampleName $render.sample_name -DeviceId $deviceId -Roles $roles
                    }
                }
            }
            "audio.clone_render" {
                $text = "$($args.text)"
                $voiceRef = "$($args.voice_ref)"
                $saveName = "$($args.save_name)"
                $autoPlay = [bool]$args.auto_play
                $deviceId = "$($args.device_id)"
                $roles = @()
                if ($args.roles) { $roles = @($args.roles) }
                if ($dryRun) {
                    $result = @{ status = "dry_run"; text = $text; voice_ref = $voiceRef; save_name = $saveName; auto_play = $autoPlay; device_id = $deviceId; roles = $roles }
                } else {
                    $render = Invoke-AudioCloneRender -Text $text -VoiceRef $voiceRef -SaveName $saveName
                    $result = @{ render = $render }
                    if ($autoPlay) {
                        $result.play = Invoke-AudioSamplePlay -SampleName $render.sample_name -DeviceId $deviceId -Roles $roles
                    }
                }
            }
            "audio.soundboard_slot_save" {
                $roles = @()
                if ($args.roles) { $roles = @($args.roles) }
                if ($dryRun) {
                    $result = @{
                        status = "dry_run"
                        slot_key = "$($args.slot_key)"
                        mode = "$($args.mode)"
                        sample_name = "$($args.sample_name)"
                    }
                } else {
                    $result = Save-AudioSoundboardSlot -SlotKey "$($args.slot_key)" -Label "$($args.label)" -Mode "$($args.mode)" -SampleName "$($args.sample_name)" -Text "$($args.text)" -Voice "$($args.voice)" -Emotion "$($args.emotion)" -VoiceRef "$($args.voice_ref)" -DeviceId "$($args.device_id)" -Roles $roles
                }
            }
            "audio.soundboard_slot_fire" {
                $slotKey = "$($args.slot_key)"
                if ($dryRun) {
                    $result = @{ status = "dry_run"; slot_key = $slotKey }
                } else {
                    $result = Invoke-AudioSoundboardSlot -SlotKey $slotKey
                }
            }
            "audio.streamdeck_export" {
                $slotKey = "$($args.slot_key)"
                $label = "$($args.label)"
                if ($dryRun) {
                    $result = @{ status = "dry_run"; slot_key = $slotKey; label = $label }
                } else {
                    $result = Export-StreamDeckLauncher -SlotKey $slotKey -Label $label
                }
            }
            default {
                throw ("unsupported action: " + $action)
            }
        }
        Send-JobResult -JobId $jobId -Status "completed" -Result @{
            action_key = $action
            dry_run = $dryRun
            result = $result
            completed_at = (Get-Date).ToString("o")
        }
    } catch {
        $msg = $_.Exception.Message
        Write-Log ("job failed id={0} action={1} error={2}" -f $jobId, $action, $msg)
        Send-JobResult -JobId $jobId -Status "failed" -Result @{
            action_key = $action
            dry_run = $dryRun
            completed_at = (Get-Date).ToString("o")
        } -ErrorText $msg
    }
}

try {
    $resolvedStartupUrl = Resolve-ServerBaseUrl -ForceRefresh
    $startupTunnel = Invoke-PrivateControlTunnelJson -Action 'ensure'
    Write-Log ("Windows node monitor starting. server={0} resolved={1} node={2} interval={3}s broker={4} private_control={5} mode={6}" -f $ServerBaseUrl, $resolvedStartupUrl, $NodeKey, $IntervalSec, [bool]$EnableToolBroker, [bool]$startupTunnel.healthy, $PrivateControlMode)
} catch {
    Write-Log ("Windows node monitor starting. server={0} node={1} interval={2}s broker={3} resolve_error={4}" -f $ServerBaseUrl, $NodeKey, $IntervalSec, [bool]$EnableToolBroker, $_.Exception.Message)
}

while ($true) {
    $policy = @{
        mode = 'observe'
        allow_tool_broker = [bool]$EnableToolBroker
        effective_job_poll_max = $JobPollMax
    }
    try {
        $tunnelState = Invoke-PrivateControlTunnelJson -Action 'status'
        if (-not $tunnelState.healthy) {
            $tunnelState = Invoke-PrivateControlTunnelJson -Action 'ensure'
        }
        $pressure = Get-PressureSnapshot
        $policy = Resolve-ResourcePolicy -PressureSnapshot $pressure -TunnelState $tunnelState
        $desktopShellState = Ensure-DesktopShellRunning -RuntimeRoot (Join-Path $PSScriptRoot '_local_runtime\pc-brain') -InteractiveLocal ([bool]$policy.interactive_local)
        if ($policy.mode -ne $script:LastResourcePolicyMode) {
            Write-Log ("resource policy mode={0} reason={1} cpu={2} mem={3} gpu={4} private_control={5}" -f $policy.mode, $policy.reason, $policy.cpu_percent, $policy.memory_percent, $policy.gpu_percent, $policy.private_control_healthy)
            $script:LastResourcePolicyMode = $policy.mode
        }
        Write-PolicyReceipt -Payload @{
            generated_at = (Get-Date).ToString("o")
            node_key = $NodeKey
            server_base_url = $ServerBaseUrl
            resolved_server_base_url = $script:ResolvedServerBaseUrl
            private_control_url = $PrivateControlUrl
            private_control_mode = $PrivateControlMode
            tunnel = $tunnelState
            route_selection = @{
                public_app_url = $ServerBaseUrl
                selected_control_route = if ($tunnelState.healthy) { 'private_control' } else { 'degraded_local_only' }
                fallback_reason = if ($tunnelState.healthy) { '' } else { 'private_control_unavailable' }
            }
            policy = $policy
            pressure = $pressure
            local_runtime = @{
                desktop_shell = $desktopShellState
            }
        }
        $payload = Build-Payload -PressureSnapshot $pressure -ResourcePolicy $policy -TunnelState $tunnelState
        $resp = Invoke-ServerJson -Path "/windows-node/heartbeat" -Method "POST" -Body $payload -TimeoutSec 20
        Write-Log ("heartbeat ok: " + ($resp | ConvertTo-Json -Compress))
        if ($RunOnce) {
            Write-Output ($resp | ConvertTo-Json -Depth 8)
        }
    } catch {
        Write-Log ("heartbeat failed: " + $_.Exception.Message)
        if ($RunOnce) {
            throw
        }
    }

    if ($EnableToolBroker -and $policy.allow_tool_broker -and [int]$policy.effective_job_poll_max -gt 0) {
        $jobs = Poll-NodeJobs -MaxJobs ([int]$policy.effective_job_poll_max)
        foreach ($job in $jobs) {
            Execute-NodeJob -Job $job
        }
    } elseif ($EnableToolBroker) {
        Write-DebugLog ("job polling skipped mode={0} allow_tool_broker={1} max_jobs={2}" -f $policy.mode, $policy.allow_tool_broker, $policy.effective_job_poll_max)
    }

    if ($RunOnce) {
        break
    }

    Start-Sleep -Seconds $IntervalSec
}
