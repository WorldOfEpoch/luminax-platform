# Luminax Discord Liaison

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

## Must-keep features
- `olympus_control_loop`: Olympus Identity and Review Cycle
