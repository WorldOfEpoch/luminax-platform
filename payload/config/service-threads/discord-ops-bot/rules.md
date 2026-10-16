# Hermes Ops Relay

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
- `payload/discord-bot`
- `payload/discord-bot`
- `payload/discord-bot`
- `payload/config/olympus/services/discord-ops-bot.json`
- `payload/config/olympus/services/discord-ops-bot.json`
- `payload/config/olympus/services/discord-ops-bot.json`
- `payload/config/service-threads/discord-ops-bot`
- `payload/config/service-threads/discord-ops-bot`
- `payload/config/discord-ops-blueprints.json`
- `payload/config/discord-ops-blueprints.json`
- `payload/config/discord-ops-blueprints.json`

## Must-keep features
- `olympus_control_loop`: Olympus Identity and Review Cycle
