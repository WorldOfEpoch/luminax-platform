> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# Luminax Local-First Migration

## Target Runtime

- Primary PC:
  - desktop shell
  - local model serving
  - local voice in/out
  - `luminax-brain`
  - `affect-controller`
  - browser and dev tool routing inside trust zones
- Server:
  - `assistant-core`
  - `admin-api`
  - MySQL
  - Redis
  - Qdrant
  - backup and logging
  - house services after the PC brain is stable

## Trust Zones

- `pc-companion`
- `dev-control`
- `home-read`
- `home-write`
- `camera-security`
- `browser-use`
- `public-external`

## Action Classes

- `safe_auto`
- `checkpoint_required`
- `sentinel_blocked`

## Sentinel-Risk Actions

- purchases
- lock, alarm, and security-state changes
- destructive deletes
- production deploys
- credential changes
- public network exposure changes
- camera export or sharing

## Storage Ownership

- MySQL:
  - goals
  - tasks
  - attempts
  - approvals
  - device registry
  - audit events
  - learned playbooks
- Redis:
  - active session state
  - short-lived plans
  - queue glue
- Qdrant:
  - semantic retrieval
  - playbook lookup
  - memory recall

## Migration Order

1. Trust zones and topology split
2. PC brain core
3. Memory, orchestration, and learning
4. Home services
5. Desktop shell and companion behavior
6. Hardening and observability

## Rollback Rules

- Keep the current `assistant-core` ingress API active throughout migration.
- Add new services as sidecars first.
- Keep cloud and Edge TTS paths as explicit fallbacks until the local path is proven.
- Keep current web UI available while the desktop shell is introduced.
