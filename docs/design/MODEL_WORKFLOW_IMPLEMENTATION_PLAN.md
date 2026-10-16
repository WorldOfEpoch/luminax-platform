> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# Project Light Model Hub + Workflow Studio Plan

## Scope
- Install and manage model stacks for video, audio, music, voice, vision, embeddings, and LLM routing.
- Provide full UI per service/model: status, variables, install/upgrade controls, logs, and test-run.
- Add visual workflow studio with drag/drop nodes, arrows, typed inputs/outputs, save/load/versioning.
- Execute workflows with audit logs, retries, artifacts, and safety gates.

## Initial Model Set (from Executive Summary)
- Streaming ASR: WhisperLive, VOSK, Speeches/faster-whisper server.
- Streaming TTS: Chatterbox/Turbo, Kokoro, Dia2.
- Voice cloning/conversion: XTTS-v2, Chatterbox cloning, FishAudio S1-mini, RTVC baseline.
- Voice orchestration: Pipecat and Rhasspy integration profiles.
- Video generation/editing: Stable Video Diffusion, Wan2.1 VACE (1.3B first, 14B optional server profile).
- Video upscaling/interpolation: Video2X, Real-ESRGAN, Real-CUGAN, RIFE.
- Audio separation/enhancement: Demucs, Spleeter, RNNoise.
- Music generation: MusicGen (small/medium first, large optional), Jukebox as experimental.
- Voice: Coqui TTS, Bark, voice conversion adapters.
- Vision: SAM, YOLOv8, CLIP.
- LLM/embeddings: Llama, Gemma, Mixtral/Falcon profile metadata, SBERT/CLIP embeddings.

## Architecture Additions
- `model-manager` service:
  - Model catalog, install state, provider adapters, preflight checks.
  - Device profile matcher (3090 workstation vs Ubuntu server profile).
  - Download/build lifecycle events and rollback.
- `workflow-engine` service:
  - DAG graph validation and execution.
  - Queue, retries, timeout controls, step-by-step artifact persistence.
- `workflow-ui` surfaces in existing command center:
  - Model Hub page.
  - Workflow Studio canvas page.
  - Run Inspector + timeline.

## Data Model
- `model_catalog`:
  - `model_key`, `category`, `provider`, `version`, `license`, `requires_gpu`, `vram_est_gb`, `ram_est_gb`, `install_method`, `status`, `meta_json`.
- `model_install_runs`:
  - install logs, durations, failures, checksum, source URI.
- `workflow_defs`:
  - versioned graph JSON, input/output schema, policy flags.
- `workflow_runs`:
  - run status, started/ended, trigger source, operator, summary.
- `workflow_run_steps`:
  - per-node state, retries, stderr/stdout snippets, artifact refs.

## UI Plan (Jarvis/Project Light Style)
- Model Hub:
  - Service cards with live heartbeat and active task badge.
  - Click card -> modal with install/update, variables, test prompt, logs, and perf metrics.
- Workflow Studio:
  - Node palette (model/service/device nodes), draggable graph canvas, edge connectors.
  - Typed ports and compatibility checks before run.
  - One-click save, duplicate, export, and run.
- Hive Board:
  - Active run windows per service with pulse animation and "stuck" heuristics.
  - Link from each running node to logs, health, and restart actions.

## Workflow Runtime Rules
- Every node declares:
  - Required inputs, optional inputs, outputs, timeout, retry policy, safety policy.
- Execution guarantees:
  - Persist node outputs and metadata after each step.
  - Resume from last successful node.
  - Hard-stop on policy violations unless explicitly marked recoverable.

## Safety / Failsafes
- Preflight checks:
  - Disk, VRAM/RAM, dependency availability, model file integrity.
- Policy gates:
  - Approval-required nodes (desktop control, account actions, publish actions).
- Recovery:
  - Per-service restart hooks.
  - Workflow cancel/rollback semantics for reversible steps.

## Implementation Waves
- Wave 1:
  - Catalog + install manager backend, Model Hub UI read-only + install actions, run logs.
- Wave 2:
  - Workflow schema + studio canvas MVP + save/load + dry-run validator.
- Wave 3:
  - Runtime engine + run inspector + artifact browser.
- Wave 4:
  - Project Light device nodes (camera/light/outlet/security) integrated into same graph.

## Windows Installation Path
- Script: `windows-model-bootstrap.ps1`
  - Installs local runtime prerequisites for voice-first model stack on Windows.
  - Creates dedicated venv and capability JSON for heartbeat/reporting.
  - Supports `voice-core` and `full` bootstrap modes.

## First Build Slice (starting now)
- Add model catalog schema + endpoints in assistant/admin surfaces.
- Add Model Hub UI tab with card list and modal detail.
- Add workflow schema table + API stubs (`create/list/get/run/validate`).
- Seed catalog with priority models:
  - `wan2.1-vace-1.3b`
  - `stable-video-diffusion`
  - `video2x`
  - `demucs`
  - `musicgen-medium`
  - `coqui-xtts`
  - `sam-vit-b`
  - `yolov8n`
  - `clip-vit-base-patch32`
