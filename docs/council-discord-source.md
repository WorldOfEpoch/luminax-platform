# Council and Discord source boundary

This is a service-source preview of the existing Mount Olympus platform. It preserves its current owners so contributors can improve the actual implementation. It is not a complete, independently deployable council system.

## What is included

| Owner | Existing responsibility | Current evidence |
| --- | --- | --- |
| `payload/assistant-core/main.py` | Platform API, Discord identities and project access, pairing, review routing, control leases, and many optional platform workflows | Python syntax compiled; selected authorization and inactive-startup owners exercised offline |
| `payload/discord-bot/bot.py` | Discord messages, commands, optional voice, pairing, community administration, leader leases, message deduplication, and policy-gated calls to assistant-core | Python syntax compiled; disabled entry point exercised offline |
| `payload/discord-ops-bot/main.py` | Community blueprint planning and review requests through assistant-core | Python syntax compiled; caller authorization and missing-endpoint behavior exercised offline |
| `payload/discord-bot/provision_papr.py` | Existing optional guild provisioning entry point | Python syntax compiled; default path stops before connecting |

The source came from the owner's preexisting Mount Olympus platform. The reviewed Python deployment and generated copies matched the selected source. Those duplicate trees are not included here. The existing service ownership record identifies the Discord adapter as the Discord liaison; the ops service is its review/planning companion.

Luminax's `council_allowed` field controls access to review and presence features. It is not the separate Zeus core's six-member council implementation. Zeus core owns that council and its jobs. A scoped Discord adapter to that core remains an integration goal; this extraction does not claim that the two implementations are connected or interchangeable.

No live credentials, account data, Discord histories, captured messages, identity registries, private profiles, logs, or deployed configuration are included. Python dependencies are references in the existing requirement files, not bundled vendor source. The root license covers the published first-party source; installed dependencies keep their own licenses.

## Safe defaults in this public copy

- `LUMINAX_RUNTIME_ENABLED` defaults to `false`. Directory initialization, vector-store construction and initialization, the background scheduler, and employee-profile bootstrap require an explicit opt-in.
- `DISCORD_RUNTIME_ROLE` defaults to `disabled`. The bot entry point exits instead of connecting or remaining asleep as a background process.
- Continuous Discord stewardship defaults to off. Guild provisioning requires both `DISCORD_PROVISIONING_ENABLED=true` and an explicit `PAPR_GUILD_ID`.
- There is no shared default control token. Connector credentials must contain at least 32 characters; empty, short, incorrect, and missing tokens fail closed. Generate an independent secret outside this repository.
- A control token alone does not grant ordinary operator actions: the existing bounded control lease is still required. Its grant remains 600 seconds.
- The ops relay requires the caller's valid token. It no longer supplies its own configured token for an unauthenticated review request.
- Trusted proxy identity is disabled unless `TRUST_PROXY_AUTH_HEADERS=true` and `TRUSTED_WEB_CONTROL_USER` are explicitly configured. Only enable this behind a trusted proxy that removes client-supplied identity headers and sets its own authenticated identity.
- Personal operator names, account/email defaults, deployed addresses, and local path translations have been removed from source defaults. No fixed Discord user or guild IDs were found in the selected source; those identities already use configuration.

The platform is still a substantial monolithic application. These changes are bounded publication hardening, not a complete security audit. Do not expose its development API directly to the Internet. Its container recipes describe existing build dependencies; they are not a verified production deployment bundle.

## Offline validation

Python 3.11 or later is sufficient for the publication guards; they require no third-party dependencies:

```console
python -m unittest discover -s payload/assistant-core/tests -p "test_*.py"
```

The current suite contains 12 tests. It reads the service files, selects the actual authorization and startup statements, and executes them with synthetic requests and inert substitutes. It does not import the service modules, load `.env` files, use personal settings, install packages, open sockets, connect to Discord, or start a scheduler.

The checks cover:

- Unconfigured, short, incorrect, and non-ASCII supplied credentials.
- The distinction between a connector credential and an active operator control lease.
- Explicit proxy-identity opt-in and matching configured identity.
- Rejected empty control grants, with no persistence write.
- A valid control grant retaining its bounded lease.
- Rejected unauthenticated ops reviews, with no upstream request.
- Authenticated reviews preserving the caller and requested action.
- A missing ops endpoint stopping before HTTP client creation.
- Disabled core initialization, bot startup, stewardship, and provisioning defaults.

All four service Python files also compiled in memory during preparation. These checks do not validate full imports, package compatibility, database migrations, voice, ordinary Discord interactions, worker execution, model quality, or deployment.

## External dependencies and configuration

The existing platform depends on Redis, MySQL, and Qdrant, plus optional model and worker APIs. The expanded public edition includes the platform service source, deployment recipes and synthetic configuration examples. Use [full platform setup](full-platform-setup.md) to create a fresh isolated configuration before a runtime test; personal installation records and accounts are not prerequisites for obtaining the source.

| Configuration | Meaning |
| --- | --- |
| `CONTROL_TOKEN` | A new private connector secret shared only with explicitly authorized services |
| `CORE_URL` | Fully qualified assistant-core API base for both Discord services, including `/api` |
| `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD` | A dedicated test database and account; no personal database is implied |
| `REDIS_HOST`, `REDIS_PORT`, `QDRANT_HOST` | Explicitly provisioned test service locations; service-specific support follows the source |
| `DISCORD_TOKEN`, `DISCORD_APPLICATION_ID` | A separately created test Discord application; no token is supplied here |
| `YOUR_USER_IDS`, `OLYMPUS_OWNER_DISCORD_IDS`, `YOUR_USER_ID` | Explicit operator identities for a test server |
| `OLYMPUS_OPERATOR_PORTAL_USER_ID`, `OLYMPUS_OPERATOR_NAME`, `OPERATOR_PROFILE_PATH` | Optional operator identity and a private profile owned by the deployment |
| `PUBLIC_SITE_URL`, `OLYMPUS_SITE_URL`, `DISCORD_UI_ACTIVITY_URL`, `PAPR_SITE_URL`, `UNDERFOOT_SITE_URL` | Optional deployment-owned public UI locations; they default to empty |
| `WINDOWS_WORKSPACE_PREFIX` | Optional explicit translation of a Windows workspace prefix; no machine path is embedded |
| `OLYMPUS_IDENTITY_PATH`, `REVIEW_CYCLE_POLICY_PATH`, `DISCORD_OPS_BLUEPRINTS_PATH` | Deployment-specific configuration files; existing paths describe a container layout, not supplied personal files |

Optional service endpoints such as `OLLAMA_HOST`, `EDGE_BRIDGE_URL`, `VOICE_AGENT_URL`, `IMAGE_AGENT_URL`, `VISION_AGENT_URL`, `ACCOUNT_WORKER_URL`, `ADMIN_API_INTERNAL_URL`, `ADMIN_API_URL`, `RESEARCH_WORKER_URL`, `BROWSER_AGENT_URL`, `LUMINAX_BRAIN_URL`, `AFFECT_CONTROLLER_URL`, `MCP_ROUTER_URL`, and the hive/worker URLs now require explicit configuration. Existing private endpoint aliases are not automatically reused. Optional services are not promised to work simply because their client paths are present.

Assistant-core also has integrations with workspace registries, role policies, model catalogs, worker registries, project storage, approval records, and the Codex CLI. The public configuration and [service inventory](platform-services.md) identify the supplied source and examples; optional companion projects and operator accounts remain separate. Several requirement files retain their existing unpinned dependency references, so a reproducible full-runtime dependency lock is still needed.

The existing service notes described compilation plus manual private-pairing and sanitized-public-reply checks. No automated full Discord/council integration suite was found in the selected source. The publication tests above add bounded regression coverage; they do not substitute for those future runtime checks.

## Contribution targets

1. Build reproducible isolated service fixtures and dependency locks without using personal data or live accounts.
2. Add mocked Discord gateway and assistant-core contract tests for pairing, project isolation, permissions, leader leases, deduplication, rate limits, and denied requests.
3. Define and test an authenticated adapter to the existing Zeus council/job owner. Keep the Discord adapter as a client; do not add another council controller.
4. Test cancellation, reconnects, duplicate delivery, unavailable services, and token rotation with synthetic messages.
5. Perform an explicitly configured test-server run, then record exact versions and evidence separately for messages, voice, review outcomes, and execution authorization.

Changes to a role, prompt, or review result must not silently grant tool or service permissions. Any future write path needs the existing authorization boundary and evidence of the real receiver's behavior.
