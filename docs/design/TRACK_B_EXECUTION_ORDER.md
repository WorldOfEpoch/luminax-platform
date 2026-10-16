> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# Luminax Track B Execution Order (Post A4 Baseline)

This sequence is the implementation order for full TODO burn-down after baseline install reliability is green.

## B2 UI Integrity + Service Modal/File Ops
- Complete file read/write helper routing in UI and assistant-core.
- Remove placeholder-only file operation behavior while preserving controls.
- Add regression checks for chat, tabs, modal actions, and note flow.
- Add Project Light operator panel for personality/feature/voice tuning backed by `config/project-light-personality.json`.

## B3 Admin/API Integrity + Control Governance
- Add aggregate readiness endpoint in admin-api for automation and UI.
- Standardize admin operation envelopes and error shape.
- Tighten control-token flow consistency across UI/core/admin.
- Add endpoint(s) for employee profile/status retrieval and assignment routing hooks.
- Add idle-autonomy dispatch endpoint for assign-if-idle behavior.

## B4 Assistant-Core Integrity + Tool Router
- Convert internal placeholder tools to real routed implementations.
- Stabilize experiment/adopt workflow parsing and sandbox behavior.
- Add route-level tests for tasks, agents, memory, and git surfaces.
- Persist employee prompt profiles, model/provider choice, connector map, idle-autonomy policy, and active project assignment state.

## B5 Realtime Duplex Speech
- Progress realtime session manager toward interruption-aware turns.
- Add sentence-level low-latency playback and barge-in scaffolding.
- Verify coexistence with current voice path and talk_turn flow.

## B6 Discord Governance + Observability
- Harden ambient text behavior and cooldown/anti-spam controls.
- Expand structured bug intake visibility in Discord and UI.
- Ensure voice-originated text never directly executes actions.

## B7 Network/Device/House Orchestration
- Expand discovery, capabilities, and persistent inventory logic.
- Implement room/group/device orchestration linkage across subsystems.
- Build wireframe mapping and rough device placement heuristics.

## B8 Media/Publishing/Fleet/Hive/Security/Hostname
- Integrate image/video/music generation services.
- Add publishing approval workflows for social connectors.
- Implement fleet enrollment + hive-mind callback registry.
- Add security module foundation and Windows hostname trust helpers.

## Completion Rule
- A Track B item is only closed when:
- Its mapped smoke checks pass.
- Its targeted regression checks pass.
- No previously working capability regresses.
