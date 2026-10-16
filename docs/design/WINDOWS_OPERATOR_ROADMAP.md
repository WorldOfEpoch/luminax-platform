> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# Windows Operator Roadmap (Project Light / Luminax)

## Objective
Enable a Windows-side companion that can assist users directly with screen-aware help, optional desktop control, and task automation while enforcing strict operator consent and failsafes.

## Scope (Planned)
- Screenshot uplink from Windows app to server for contextual assistance.
- Browser attach/assist workflow for user tasks (mail sort/send/declutter).
- Desktop control channel (explicit opt-in only).
- Foreground app/program awareness as context signal.
- Webcam + microphone session input pipeline (staged, opt-in).
- File/program access broker for local Windows operations.

## Hard Safety Requirements
- Consent-first: no control path without explicit user grant per session.
- Visible indicator in Windows app when capture/control is active.
- Kill switch available both in Windows app and web UI.
- Session TTL auto-expiry and re-auth required after expiry.
- Action-rate limits + cooldowns.
- Full immutable audit log of actions and outcomes.
- Default mode for risky capabilities: observe-only.

## Execution Waves
1. `W1` Screenshot + app awareness
- Upload screenshots on-demand and interval mode.
- Send active app/window metadata with screenshot payload.
- UI surface: live assistance panel with recent captures.

2. `W2` Guided browser assist
- Attach to supported browser session.
- Suggested action cards before execution.
- Approval checkpoints before side-effect actions (send/delete/move).

3. `W3` Desktop control (opt-in)
- Input bridge with bounded command set.
- Mandatory confirmation for high-impact actions.
- Auto-disable on inactivity or repeated failures.

4. `W4` Webcam/mic ingestion (opt-in)
- Session badge + consent banner.
- Stream metadata and selective snapshots/transcripts.
- Policy-controlled retention.

5. `W5` File/program broker
- Scoped folders/program allowlist.
- Dry-run preview for write/move/delete.
- Rollback support where possible.

6. `W6` Task packs
- Mail triage workflow.
- Declutter workflow.
- Program-specific helper recipes.

## Suggested API Surfaces (Planned)
- `/admin/windows/operator/session/start|stop|status`
- `/admin/windows/operator/screenshot/upload`
- `/admin/windows/operator/app-state`
- `/admin/windows/operator/control/execute`
- `/admin/windows/operator/camera/*`
- `/admin/windows/operator/mic/*`
- `/admin/windows/operator/files/*`
- `/admin/windows/operator/logs`

## UI Surfaces (Planned)
- Ops: Windows Operator control center with session state + kill switch.
- Conversation: attach screenshot context to chat turns.
- Network/Project Light: shared awareness map for user endpoints and room devices.

## Notes
- These capabilities remain planned/scaffolded until consent, policy, and rollback checks are complete.
- Security hardening can remain deferred in private build mode, but safety controls remain mandatory.
