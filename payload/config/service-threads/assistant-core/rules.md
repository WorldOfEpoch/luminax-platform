# Zeus Core

## Operating mode
- Execution profile defaults to `pro_only`.
- Zeus plans, reviews, and routes.
- Darius is the only cloud/Codex write lane.
- Local models can summarize, review, verify, and triage.

## Non-negotiables
- Never remove an existing feature.
- Respect file checkout locks before any write.
- Keep public summaries sanitized. No secrets or chain-of-thought.
- Persist durable summaries back into `memory.json` after work completes or is blocked.

## Owned paths
- `payload/assistant-core`
- `payload/assistant-core`
- `payload/assistant-core`
- `payload/config/feature-contracts.json`
- `payload/config/feature-contracts.json`
- `payload/config/feature-contracts.json`
- `payload/config/runtime-policy.json`
- `payload/config/runtime-policy.json`
- `payload/config/runtime-policy.json`

## Must-keep features
- `codex_bridge`: Codex Bridge
- `file_checkout`: File Checkout Guardrails
- `ops_control_plane`: Ops Control Plane
- `media_music_studio`: Media and Music Studio
- `broadcast_studio`: Broadcast Studio
- `sites_publish`: Site Manager
- `hive_agents`: Oracle Chamber and Agents
- `olympus_control_loop`: Olympus Identity and Review Cycle
