# Platform service source

This release contains the tracked Mount Olympus service implementations and their dependency manifests. It is a source preview: the table describes implemented code paths, not a verified deployment or a claim that every component works together unchanged. The service directories keep their original owners. Generated/deployment mirrors, private runtime records, accounts, model weights, keys, logs, and environment files are excluded.

The expanded service extraction comprises 78 original files in 26 directories, based on source commit `ed975894ba272a84b6d5a8ca41c70c9820634dbd`. Sanitization and the safety changes below modify that baseline. The additional shared boundary and offline tests live in [`payload/service-runtime`](../payload/service-runtime/). First-party code uses the repository's [license](../LICENSE); installed packages, containers, models, and separately supplied game software retain their own terms. Dependency files are source manifests, not a redistribution of those dependencies. Many inherited Python requirements remain unpinned; a reproducible, verified full-platform lock set is still needed.

## Included services

| Directory under `payload/` | Implementation and current limitation | External requirements |
| --- | --- | --- |
| `account-worker` | Account, tester-access, and brand-account records; existing operator-token and optional trusted-proxy contracts retained. | MySQL, cryptography, HTTPX; independently configured account/provider integrations. |
| `admin-api` | Administration, diagnostics, node inventory, maintenance, and project records. Shell-backed operations need a separate host-action opt-in. | Redis, MySQL, optional Qdrant and other platform APIs; external host tooling for selected actions. |
| `affect-controller` | Deterministic bounded voice-style hints from mood/context inputs. | FastAPI/Pydantic; no model required for this calculation. |
| `broadcast-conductor` | Broadcast/session plans, media preparation, and FFmpeg process control. | FFmpeg, storage, explicitly configured input/output destinations. |
| `browser-agent` | Playwright navigation, screenshots, and action chains. | Matching Playwright browser/runtime; explicit browser-action opt-in. |
| `image-agent` | Diffusers image pipeline and optional explicit warmup. | PyTorch/CUDA image, model weights, Diffusers/Transformers and related packages; no weights included. |
| `langgraph-orchestrator` | Retained compatibility stub with deterministic placeholder receipts. It does not run LangGraph. | FastAPI/Pydantic; a real graph implementation remains contribution work. |
| `lumi-3d-engineer` | Retained 3D task/asset-plan scaffold that writes job records. It does not generate or integrate finished game assets. | Writable job storage; external authoring adapters/assets remain separate. |
| `lumi-daemon` | Presence/maintenance, optional autopilot, hive director, and upgrade coordination. No loop starts with default feature settings. | Redis/MySQL, configured platform/model endpoints; host tools only after explicit opt-in. |
| `lumi-finance-swarm` | Finance planning and simulated execution records with synthetic zero-balance account examples. No bank, brokerage, or payment execution adapter is implemented. | Writable simulation storage; explicit simulation opt-in. |
| `lumi-hive-critic` | Critic role service with model requests and Redis records. | Redis and a configured Ollama-compatible model endpoint. |
| `lumi-hive-dreamer` | Dreamer role service with model requests and Redis records. | Redis and a configured Ollama-compatible model endpoint. |
| `lumi-hive-engineer` | Engineer role service with model requests and Redis records. | Redis and a configured Ollama-compatible model endpoint. |
| `lumi-upgrade-agent` | Patch proposals, shadow validation, and explicitly enabled application workflow. | Redis, writable isolated work area, configured provider or separately enabled Codex CLI, and selected build tools. |
| `luminax-brain` | File-backed continuity, goals, thoughts, tasks, and receipts. | Writable dedicated memory volume; no model required for storage operations. |
| `mcp-router` | Existing tool catalog, policy metadata, local file/brain operations, and optional process/SSH delegation. Client-supplied policy metadata is not an authorization boundary. | Configured workspace/brain endpoints; separately configured SSH host/user/key and host-action opt-in for process operations. |
| `network-manager` | Device registry and explicit local network discovery. | MySQL and optional network tools; the private subnet default has been cleared. |
| `realtime-gateway` | WebSocket/session relay, audio paths, and core coordination. | Redis, core/voice APIs and HTTPX/WebSockets; operator clients need an authenticated connection. |
| `research-worker` | Explicit research jobs and provider/search requests. | HTTPX/BeautifulSoup and configured research/model providers. |
| `site-manager` | Site/project records and generated site files. | MySQL and a writable dedicated site directory. |
| `underfoot-match-allocator` | Retained game-instance lifecycle implementation. Docker reconciliation and instance actions are disabled by default. | Docker SDK/daemon, permitted game-server image/build inputs, ports and network; no Docker socket is supplied by the default public setup. |
| `underfoot-voice-gateway` | Go WebRTC/SFU implementation selected by its Dockerfile; the original Python signaling alternative is also preserved. Player voice-token authentication remains separate from operator auth. | Go 1.24, Gorilla WebSocket, Pion WebRTC; independent voice secret and optional TURN service. Go source was not compiled during this extraction. |
| `underfoot-voice-turn` | TURN configuration template with the embedded secret removed. | Separately installed coturn, generated private runtime configuration, explicit ports/certificates/realm. The source template is not a credential-bearing deployment file. |
| `underfoot-world` | Retained game world/session, matchmaking, diagnostics, player-auth, voice, and persistence service. It is not the source or a playable build of the companion game. | Separate game contracts, clients, dedicated-server assets, optional MySQL, voice/allocator services. The missing `neweden_api` companion is disabled by default. |
| `vision-agent` | Image-to-model request adapter. | HTTPX/Pillow and a configured vision-capable model endpoint. |
| `voice-agent` | Speech transcription, synthesis, voice-reference, and affect integration. | FFmpeg/libsndfile, faster-whisper, TTS/XTTS or Edge TTS, model weights/voice references and applicable usage rights. |

The previously released `assistant-core`, `discord-bot`, `discord-ops-bot`, and `olympus-edge-bridge` retain their existing owners and contracts. See [bridge source](bridge-source.md) and the root documentation for those components. UI, packaging, tools, and Windows sources are inventoried separately.

## Startup and operator boundary

`LUMINAX_RUNTIME_ENABLED` defaults to `false`. Only the value `true` enables the shared boundary. Disabled HTTP operations return `503` with a fixed `runtime_disabled` status; shallow `GET /health` and `GET /api/health` return a disabled status without invoking the service handler. Operator services require a configured `CONTROL_TOKEN` of at least 32 characters. Use a freshly generated random token, not a descriptive placeholder.

Authenticated operator requests use `Authorization: Bearer <token>` or `X-Control-Token`. Existing JSON-body `token` callers remain supported, with a 64 KiB / 1,024-event authentication-body bound. Uploads should use a header. Operator WebSockets require a header; browser WebSocket clients cannot normally set one and need a deliberately authenticated gateway integration. Tokens are not accepted from URL query strings by this shared guard.

The shared guard is applied to the newly extracted operator services. `account-worker` and `admin-api` retain their existing per-operation validators; these now reject empty/short tokens and require explicit `TRUST_PROXY_AUTH_HEADERS=true` plus a configured trusted user before honoring proxy identity headers. The Underfoot world and voice services retain their player/session authentication; the runtime gate does not replace it. Those inherited player/admin route policies still need a dedicated review before exposing a game server publicly.

Enabled shallow operator health reports `enabled`; that means the boundary is configured, not that models, databases, providers, or dependent services are ready. It intentionally does not run a Codex authentication probe, warm a model, or create a memory store. Existing health dashboards may need to distinguish this status from full readiness.

Additional opt-ins default to false:

| Setting | Enables |
| --- | --- |
| `LUMINAX_HOST_EXECUTION_ENABLED` | Existing host-process helpers, FFmpeg operations, and Docker allocator operations. It does not grant host mounts or Docker access. |
| `LUMINAX_BROWSER_ACTIONS_ENABLED` | Browser-agent requests. |
| `LUMINAX_NETWORK_SCAN_ENABLED` | Network-manager requests and scan subprocesses. |
| `LUMINAX_FINANCE_SIMULATION_ENABLED` | Finance simulation service. `FINANCE_MONEY_MODE` is a legacy additional flag for the simulated execute path; it does not enable real money movement. |
| `AUTOPILOT_ENABLED`, `AUTOPILOT_ALLOW_SERVICE_ACTIONS` | Daemon autopilot and its separate service-action permission. |
| `HIVE_DIRECTOR_ENABLED`, `HIVE_DIRECTOR_AUTO_CREATE_PROJECT` | Daemon hive coordination and its separate project-creation option. |
| `SELF_UPGRADE_ENABLED` | Upgrade agent and optional daemon upgrade watcher. |
| `LUMINAX_AUTO_APPLY_ENABLED` | Automatic application from an analysis request, still subject to the existing eligibility/control checks and host-action gate. |
| `UPGRADE_CODEX_USE_CLI` | Optional CLI provider path; the CLI is not authenticated or run by the release checks. |
| `EDGE_IMAGE_WARMUP_ON_START` | Model warmup after runtime opt-in. |
| `UNDERFOOT_DB_ENABLED` | Optional game database use. The driver and database configuration must also be supplied. |
| `UNDERFOOT_DEDICATED_IMAGE_AUTO_BUILD` | Allocator image builds, still requiring allocator/host opt-in. |
| `UNDERFOOT_NEWEDEN_ENABLED` | Import the separately supplied `neweden_api` companion. Enabling this without that module raises `neweden_companion_source_required`; it does not silently hide an explicitly requested feature. |
| `UNDERFOOT_LOAD_DOTENV` | Explicit loading of a separately supplied game project environment file, after runtime opt-in. |

Daemon startup also requires at least one of its background features and a strong operator token. Existing diagnostics are appended to, rather than cleared on startup. Storage creation, world-state loading, and Docker reconciliation are gated when runtime is disabled. Python and Go voice signing reject an unconfigured short secret; TURN uses a separate secret and no private public-host fallback. The default source does not invent usable voice/TURN credentials.

## Integration and setup limits

Use [full platform setup](full-platform-setup.md) for generated local configuration and Compose profiles. The added Python Dockerfiles use the `payload/` build context so that each can copy `service-runtime/runtime_guard.py`. The Go voice gateway keeps its own directory as its build context. Native Python runs need `payload/service-runtime` on `PYTHONPATH` as well as the service's own dependencies.

The guard deliberately does not inject an operator credential into every outgoing request. Some inherited core, bridge, admin, daemon, realtime, model/voice, and game-allocator calls do not forward an authenticated operator credential to newly guarded helpers. They will receive `401`/`503` until the relevant caller is integrated with a trusted, scoped transport. Automatically attaching a secret in a publicly reachable proxy could turn that proxy into an unauthorized operator. Contributions must authenticate the inbound caller, limit credentials to an explicitly configured service origin/path, refuse credential-bearing redirects, and keep provider/search requests free of operator credentials. This is a concrete remaining integration task, not a claim that the combined stack has been tested.

`underfoot-world` references companion `shared/`, game-client, and dedicated-server resources that are not in the Mount Olympus service source. The public package preserves the adapters; it does not supply those sibling game projects or claim gameplay acceptance. Its optional MySQL import and inherited JSON fallback need explicit validation when selecting a persistence mode. Model-heavy voice/image containers, browser actions, host tools, and actual provider compatibility likewise require separate opt-in validation.

## Offline evidence and contribution targets

The extraction check compiled all 27 added Python source/test files without importing services. The shared boundary/owner regression suite passed 32 tests:

```text
python -m unittest discover -s payload/service-runtime -p "test_*.py" -v
```

These tests use in-memory ASGI events, selected actual owner functions, and fake subprocess/storage/model collaborators. They cover disabled operations and startup, missing/short/wrong credentials, proxy opt-in, header and chunked-body authentication, malformed/oversized bodies, WebSocket denial, existing control leases, voice signing, optional companion imports, host-action gating, safe atlas argv, and separate upgrade auto-apply consent. They make no provider, service, socket, browser, database, or model calls. They do not establish container builds, full dependency compatibility, Go compilation, startup of the complete stack, voice reception, gameplay, or a complete security audit.

Useful next contributions are scoped authenticated service transports, reproducible dependency locks and container builds, a Go voice test pipeline, explicit optional-provider adapters, meaningful end-to-end tests against disposable local dependencies, and replacing retained scaffolds with tested implementations. Keep automatic work disabled until it is expressly selected, and report source, build, runtime, and user acceptance as separate results.
