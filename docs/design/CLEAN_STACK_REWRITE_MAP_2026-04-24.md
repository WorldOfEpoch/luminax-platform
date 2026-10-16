> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# Luminax Clean Stack Rewrite Map - 2026-04-24

## Active platform after cleanup

Core server/runtime:
- `assistant-core`
- `admin-api`
- `redis`
- `mysql`
- `qdrant`
- `realtime-gateway`
- `browser-agent`
- `broadcast-conductor`
- `lumi-daemon`
- `lumi-upgrade-agent`
- `discord-bot`
- `discord-ops-bot`
- `zeus-discord`
- `community-discord`
- `luminax-ui`

PC local brain:
- `voice-agent`
- `affect-controller`
- `luminax-brain`
- `mcp-router`
- `olympus-edge-bridge`
- `luminax-companion-ui`
- desktop shell
- Ollama

Optional specialist lanes still present:
- `research-worker`
- `site-manager`
- `account-worker`
- `network-manager`
- `lumi-hive-dreamer`
- `lumi-hive-engineer`
- `lumi-hive-critic`
- `lumi-finance-swarm`

## What was removed from the active stack

- `langgraph-orchestrator`
- `lumi-3d-engineer`
- `underfoot-world`
- `underfoot-match-allocator`
- `underfoot-voice-gateway`
- `underfoot-voice-turn`

Archive location:
- `<your-workspace>`

## Immediate rewrite targets

### 1. One source tree only

Replace the current three-tree layout:
- `deploy/ubuntu`
- `payload`
- `payload/generated`

Target:
- `services/`
- `infra/`
- `runtime/`

Rules:
- `services/` is the only source-owned service code
- `infra/` holds compose, nginx, and deploy scripts
- `runtime/` holds generated output, logs, temp state, and local PC runtime receipts

### 2. Break up `assistant-core`

Current issue:
- `assistant-core/main.py` is acting as a giant monolith

Rewrite into modules:
- `assistant_core/api/`
- `assistant_core/codex/`
- `assistant_core/memory/`
- `assistant_core/media/`
- `assistant_core/orchestration/`
- `assistant_core/platform/`

### 3. Collapse role-clone workers

Current issue:
- `lumi-hive-dreamer`
- `lumi-hive-engineer`
- `lumi-hive-critic`

Rewrite target:
- one `hive-worker` service
- role selected by env/config

### 4. Collapse narrow CRUD workers

Current issue:
- `site-manager`
- `account-worker`
- `network-manager`

Rewrite target:
- one `ops-worker` service or fold these domains into `admin-api`

### 5. Decide whether affect stays separate

Current issue:
- `affect-controller` is small enough to merge into `voice-agent`

Keep separate only if:
- you want strict process isolation between reasoning and prosody

## Services that should stay

- `assistant-core`
- `admin-api`
- `voice-agent`
- `luminax-brain`
- `mcp-router`
- `olympus-edge-bridge`
- `luminax-companion-ui`
- `browser-agent`
- `realtime-gateway`
- `lumi-daemon`

## Services that should be reviewed next

- `lumi-finance-swarm`
- `site-manager`
- `account-worker`
- `network-manager`
- `lumi-hive-dreamer`
- `lumi-hive-engineer`
- `lumi-hive-critic`

## Definition of clean state

- no archived game lanes in active compose
- no placeholder services in active runtime
- no service directories on disk that are absent from the intended topology without being archived
- no duplicated deployment trees treated as equal source of truth
