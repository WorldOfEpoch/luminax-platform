param(
  [string]$HostName = ([string]$env:LUMINAX_HOSTNAME),
  [int]$WorldPort = 8450,
  [string]$WebBase = ([string]$env:LUMINAX_WEB_BASE_URL),
  [string]$WebUser = $(if ($env:LUMINAX_WEB_USER) { $env:LUMINAX_WEB_USER } elseif ($env:EXTERNAL_ACCESS_USER) { $env:EXTERNAL_ACCESS_USER } else { "" }),
  [string]$WebPassword = $(if ($env:LUMINAX_WEB_PASSWORD) { $env:LUMINAX_WEB_PASSWORD } else { $env:EXTERNAL_ACCESS_PASSWORD }),
  [switch]$SkipPublishedSite
,
    [switch]$Apply,
    [switch]$DryRun
)
. (Join-Path $PSScriptRoot '../tools/PublicToolSafety.ps1')
if (-not (Test-PublicToolApply -Tool 'tools\underfoot_smoke.ps1' -Effects 'Inspect the authored script before applying: this action may read configured services, change local files, install tools, or operate explicitly configured processes.' -Apply:$Apply -DryRun:$DryRun)) { return }


$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Invoke-JsonGet {
  param([string]$Uri)
  return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method Get -TimeoutSec 20
}

function Invoke-JsonPost {
  param(
    [string]$Uri,
    [hashtable]$Body
  )
  return Invoke-RestMethod -MaximumRedirection 0 -Uri $Uri -Method Post -ContentType "application/json" -Body ($Body | ConvertTo-Json) -TimeoutSec 20
}

function Get-BasicAuthHeader {
  param(
    [string]$User,
    [string]$Password
  )
  $bytes = [Text.Encoding]::ASCII.GetBytes("${User}:${Password}")
  return @{ Authorization = "Basic " + [Convert]::ToBase64String($bytes) }
}

function Test-CurlPage {
  param(
    [string]$Uri,
    [string]$User,
    [string]$Password
  )
  $tmp = Join-Path $env:TEMP ("underfoot-page-" + [guid]::NewGuid().ToString("N") + ".html")
  try {
    $status = & curl.exe -sS -u "${User}:${Password}" -o $tmp -w "%{http_code}" $Uri
    return @{
      status_code = [int]$status
      content_length = ((Get-Item $tmp).Length)
    }
  } finally {
    if (Test-Path $tmp) {
      Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
  }
}

function Test-WebSocketSnapshot {
  param(
    [string]$Uri
  )
  $socket = [System.Net.WebSockets.ClientWebSocket]::new()
  $cts = [System.Threading.CancellationTokenSource]::new()
  $cts.CancelAfter([TimeSpan]::FromSeconds(12))
  try {
    $socket.ConnectAsync([Uri]$Uri, $cts.Token).GetAwaiter().GetResult()
    $buffer = New-Object byte[] 8192
    $segment = [ArraySegment[byte]]::new($buffer)
    $result = $socket.ReceiveAsync($segment, $cts.Token).GetAwaiter().GetResult()
    $text = [Text.Encoding]::UTF8.GetString($buffer, 0, $result.Count)
    return ($text | ConvertFrom-Json)
  } finally {
    if ($socket.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
      $closeCts = [System.Threading.CancellationTokenSource]::new()
      $closeCts.CancelAfter([TimeSpan]::FromSeconds(3))
      $socket.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, "done", $closeCts.Token).GetAwaiter().GetResult()
    }
    $socket.Dispose()
  }
}

$worldBase = "http://${HostName}:${WorldPort}"
$report = [ordered]@{
  world_base = $worldBase
  started_at = (Get-Date).ToString("s")
  checks = [ordered]@{}
}

try {
  $health = Invoke-JsonGet -Uri "${worldBase}/api/health"
  if ($health.status -ne "ok") { throw "Health returned unexpected status." }
  $report.checks.health = $health

  $maps = Invoke-JsonGet -Uri "${worldBase}/api/maps"
  if (-not $maps.maps -or $maps.maps.Count -lt 1) { throw "No maps returned." }
  $report.checks.maps = @{ count = $maps.maps.Count; first_map = $maps.maps[0].map_id }

  $join = Invoke-JsonPost -Uri "${worldBase}/api/session/guest" -Body @{
    name = "SmokePip"
    map_id = $maps.maps[0].map_id
    spawn_preference = $maps.maps[0].spawns[0].id
  }
  if (-not $join.session_id) { throw "Guest session was not created." }
  $report.checks.join = @{ session_id = $join.session_id; map_id = $join.map_id; spawn = $join.spawn.id }

  $forage = Invoke-JsonPost -Uri "${worldBase}/api/session/$($join.session_id)/action" -Body @{
    action = "forage"
    value = 2
  }
  if ($forage.status -ne "ok") { throw "Forage action failed." }
  $report.checks.forage = @{
    note = $forage.note
    points = $forage.snapshot.session.stats.points
    resources = $forage.snapshot.session.stats.resources
  }

  $respawn = Invoke-JsonPost -Uri "${worldBase}/api/session/$($join.session_id)/action" -Body @{
    action = "respawn"
  }
  if ($respawn.status -ne "ok") { throw "Respawn action failed." }
  $report.checks.respawn = @{
    note = $respawn.note
    deaths = $respawn.snapshot.session.stats.deaths
    spawn = $respawn.snapshot.session.spawn.id
  }

  $ws = Test-WebSocketSnapshot -Uri "ws://${HostName}:${WorldPort}/ws/world/$($join.session_id)"
  if (-not $ws.session.session_id) { throw "WebSocket snapshot did not include a session." }
  $report.checks.websocket = @{
    kind = $ws.kind
    session_id = $ws.session.session_id
    presence_count = @($ws.presence).Count
  }

  if (-not $SkipPublishedSite) {
    $site = Test-CurlPage -Uri "${WebBase}/sites/underfoot/" -User $WebUser -Password $WebPassword
    $project = Test-CurlPage -Uri "${WebBase}/sites/underfoot/project.html" -User $WebUser -Password $WebPassword
    if ($site.status_code -ne 200 -or $project.status_code -ne 200) {
      throw "Published site pages did not return 200."
    }
    $report.checks.published_site = @{
      home_status = $site.status_code
      project_status = $project.status_code
      home_bytes = $site.content_length
      project_bytes = $project.content_length
    }
  }

  $report.result = "ok"
  $report.finished_at = (Get-Date).ToString("s")
  $report | ConvertTo-Json -Depth 8
  exit 0
} catch {
  $report.result = "failed"
  $report.error = $_.Exception.Message
  $report.finished_at = (Get-Date).ToString("s")
  $report | ConvertTo-Json -Depth 8
  exit 1
}
