> Historical design/source record. Statements below describe plans and an earlier development state; they are not current deployment or acceptance evidence. Paths and identities were sanitized for this public copy.

# Luminax Modular Server Plan

## Objective
Move from a monolithic generated installer to an editable modular stack where:
- Windows becomes the primary interactive Luminax brain (desktop shell + local model + local voice + continuity).
- Ubuntu server hosts the always-on runtime and house-services spine.
- Service files are edited directly (no giant single-file regeneration loop required for every change).

## Target Structure
- `services/<service-name>/` source-owned Dockerfiles + code
- `infra/compose.server.yml` server and house-services overlay
- `infra/compose.pc.yml` PC brain overlay
- `infra/nginx/` TLS + routing config
- `config/` runtime config seeds (skills, models, employees, personalities)
- `deployment/server/` server deploy/validate scripts
- `deployment/windows/` Windows controller installer + monitor/link manager

## Permanent Windows <-> Server Connection
1. Windows controller installs and runs monitor as a scheduled task.
2. Monitor sends heartbeat + capability inventory to server.
3. Server can queue trust-zone-approved jobs for Windows node execution.
4. Windows returns execution receipts and status.
5. UI surfaces node status/jobs directly from server APIs.

## Trust-Zone Direction

- Default behavior is autonomous inside a scoped trust zone.
- Only sentinel-risk actions block for approval.
- Home control, browser side effects, deploy actions, and camera exports must remain independently scoped.

## Execution Model
- Keep `luminax.sh` as compatibility wrapper during transition.
- New work goes to modular files first.
- Wrapper calls modular deploy scripts instead of generating large blocks.
- Final step retires generation-heavy sections after parity checks.

## Short-Term Milestones
1. Introduce single Windows entrypoint (`windows-controller.ps1`) for install/control/status.
2. Freeze generated service outputs and extract to source-owned directories.
3. Switch server deployment path to compose from source directories.
4. Keep validation/smoke/integrity gates intact.
5. Remove duplicate/legacy tail blocks once parity is proven.

## Cleanup Pass Applied

The first live cleanup pass has already:
- removed archived `underfoot-*` lanes from the active compose path
- removed the placeholder `langgraph-orchestrator` from the required runtime
- removed Unreal-specific `lumi-3d-engineer` from the active compose path
- archived those service directories under `<your-workspace>`

Next cleanup targets:
- collapse the hive trio into one role-configured worker
- merge or collapse narrow CRUD workers (`site-manager`, `account-worker`, `network-manager`)
- retire the three-tree `deploy/payload/generated` duplication model

## Non-Negotiables
- No feature deletion.
- Keep auditability and control-token enforcement.
- Keep Windows link secure (token/cert rotation path).
- Keep smoke tests and install reports authoritative.
