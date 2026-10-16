> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# Windows Client Link Research (2026-03-07)

## Goal
Use a Windows client as a one-stop operator surface that:
- opens/hosts the Luminax UI,
- reports local capabilities/tools to server,
- executes approved local tool jobs requested by server,
- returns results and logs for full observability.

## What We Need

1. Transport and identity
- Persistent HTTPS + token/cert enrollment for Windows node identity.
- Server-side queue for node-targeted jobs (`queued -> dispatched -> completed/failed`).
- Signed/traceable execution receipts for every local action.

2. Desktop shell
- Web-hosted UI plus native bridge in a Windows desktop shell.
- Candidate: WebView2-based desktop app for integrated UI + native broker.

3. Local tool broker (Windows)
- Guarded execution of approved actions (`service status`, `tool probe`, `file list`, `open URL`, guarded PowerShell, screen capture).
- Path allowlists and command allowlists.
- Dry-run mode default for potentially sensitive actions.

4. Safety and audit
- Per-action policy checks before execution.
- Immutable job history in server DB and operator-visible log views.
- Explicit denylist for fraud/evasion/deceptive automation.

## Primary Source Notes

- PowerShell remoting requirements and WinRM dependencies:
  - https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_remote_requirements?view=powershell-7.5
- Enable-PSRemoting behavior (starts WinRM, listener, firewall exception):
  - https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/enable-psremoting?view=powershell-7.5
- WinRM install/config + standard ports (HTTP 5985 / HTTPS 5986):
  - https://learn.microsoft.com/en-us/windows/win32/winrm/installation-and-configuration-for-windows-remote-management
- .NET WebSockets (ClientWebSocket patterns):
  - https://learn.microsoft.com/en-us/dotnet/fundamentals/networking/websockets
- WebView2 desktop embedding path:
  - https://learn.microsoft.com/en-us/microsoft-edge/webview2/get-started/wpf
- Windows UI Automation baseline:
  - https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32
- Transcript logging in PowerShell:
  - https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.host/start-transcript?view=powershell-7.5

## Implementation Sequence (next)

1. Stabilize Windows node job queue APIs and UI controls (now in progress).
2. Add Windows-side job executor with guarded allowlists and result upload.
3. Add enrollment mode (short-lived bootstrap token -> rotated node token/cert).
4. Package desktop shell (WebView2) with native broker process.
5. Add policy UI for per-tool permissions and approval workflows.
6. Add signed receipts + periodic integrity checks.

## Non-negotiables
- No fake viewers, anti-detection evasion, or deceptive engagement automation.
- Every autonomous/remote action must remain auditable and operator-overridable.
