# Luminax Bridge source preview

The Bridge connects an operator's local services to the Olympus control plane. Its existing implementation is preserved in `payload/olympus-edge-bridge/`: a FastAPI service that checks configured workers, reports node health, sends an optional heartbeat, and forwards requests to the Luminax brain and affect controller.

This folder is a source preview, not a complete assistant installation. It does not install models, start Ollama, create a Windows scheduled task, connect Discord, or supply the other services listed below.

## Source and license provenance

The starting point was the owner's Mount-Olympus source snapshot `ed975894ba272a84b6d5a8ca41c70c9820634dbd`, specifically `payload/olympus-edge-bridge/`. At extraction, its `main.py` matched the generated and deployment mirrors byte for byte. Its original SHA-256 was `fba878e26b50cbfba0e55a9c1ba22e802e8e2e1cda617fe1fe608e424aff704f`. The public copy deliberately changes configuration defaults and credential handling as described below.

The private snapshot had no first-party license file for this service and no third-party source or copyright header in these three original files. The root `LICENSE` governs the first-party code in this publication; dependency licenses remain separate. No private Git history, environment files, runtime receipts, models, desktop binaries, or deployment credentials are included in this component.

Direct Python dependencies are installed separately:

| Dependency | Tested pin | Upstream license |
| --- | --- | --- |
| FastAPI | 0.133.1 | [MIT, Sebastián Ramírez](https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| Uvicorn | 0.41.0 | [BSD 3-Clause, Encode OSS Ltd](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) |
| HTTPX | 0.28.1 | [BSD 3-Clause, Encode OSS Ltd](https://github.com/encode/httpx/blob/master/LICENSE.md) |

The original dependency list was unpinned. These direct versions match the environment used for the offline tests. This is not a complete transitive dependency lock or a claim of compatibility with every newer release. The Dockerfile retains the Python 3.11 slim base; no container image or bundled Python environment is shipped here.

## What changed for this public copy

- All service URLs and the display-only public app URL start empty. An unconfigured health check reports disabled workers instead of contacting an existing installation.
- Heartbeats, GPU inventory, and diagnostic file writes require explicit configuration. The startup heartbeat requires both an admin URL and a control token. Its one background task is cancelled on application shutdown.
- Diagnostic probe URLs omit user information, query strings, and fragments. Known control-token values and credential fields are redacted from probe responses and diagnostic receipts. Exceptions report their type instead of their potentially sensitive text.
- Base service URLs must be HTTP or HTTPS without embedded credentials, a query, or a fragment. Health URL overrides accept only the existing `skip_edge_bridge` and `edge_safe` query parameters. Invalid configuration errors name the variable without printing its value.
- Boolean settings accept only `true` or `false`, ignoring case. Heartbeat intervals are bounded to 20–86,400 seconds. Upstream requests ignore ambient HTTP proxy settings and do not follow redirects.
- Unconfigured brain or affect proxies return HTTP 503. Default health checks do not invoke GPU tools or an operating-system shell.

These changes do not establish a general secret-filtering guarantee for arbitrary upstream payloads. This remains an operator diagnostics interface with access to the configured services' health data.

## External service contracts

| Configuration | Contract used by this Bridge | Role |
| --- | --- | --- |
| `ADMIN_API_URL` and `CONTROL_TOKEN` | `GET /windows-node/latest`; `POST /windows-node/heartbeat`; derived admin/voice health routes | Olympus node authority and heartbeat receiver |
| `OLLAMA_HOST` | `GET /api/tags` | Reports available local models; the Bridge does not load or infer with them |
| `EDGE_VOICE_URL` | `GET /health` | Local voice agent |
| `EDGE_LUMINAX_BRAIN_LOCAL_URL` | `GET /health`; forwarded GET/POST paths | Luminax brain |
| `EDGE_AFFECT_CONTROLLER_LOCAL_URL` | `GET /health`; forwarded GET/POST paths | Affect controller |
| `EDGE_VISION_URL`, `EDGE_IMAGE_URL` | `GET /health` | Optional vision and image workers |

There are no Python imports from sibling directories. That does not make the whole product independent: heartbeat delivery requires a compatible admin API, and the proxy routes require their named upstream services. The Bridge is not a universal OpenAI API adapter and is separate from the provider compatibility lab.

The historical admin authority route expects the control token in a query parameter. This preview preserves that wire contract while removing it from Bridge diagnostics. A deployed admin server or reverse proxy may still record request queries. Use a protected connection and appropriately configured server access logs; migrating both ends to header-based authentication remains integration work.

## Run and configure deliberately

Use a Python 3.11 virtual environment. From the repository root, install the Bridge's direct dependencies and run its offline checks:

```console
python -m pip install -r payload/olympus-edge-bridge/requirements.txt
python -B -m unittest discover -s payload/olympus-edge-bridge -p test_bridge.py -v
```

`payload/olympus-edge-bridge/.env.example` documents the settings. The application reads the process environment; it does **not** load that file automatically. Supply only the service URLs you want it to contact. Empty health overrides derive their routes from a configured admin URL.

To inspect the unconfigured service in a foreground terminal:

```console
python -m uvicorn main:app --app-dir payload/olympus-edge-bridge --host 127.0.0.1 --port 8150
```

This command starts only the Bridge. It does not activate the heartbeat or other services. Stop it with the terminal's interrupt command. `/health`, `/workers`, and `/local-runtime` report status; `/heartbeat/now` performs one explicitly requested heartbeat when configured. GET and POST routes under `/luminax-brain` and `/affect-controller` forward to their configured upstreams.

`EDGE_BRIDGE_HEARTBEAT_ENABLED=true` explicitly enables the periodic heartbeat; `EDGE_HEARTBEAT_INTERVAL_SEC` defaults to 60. `EDGE_GPU_PROBE_ENABLED=true` permits the existing `nvidia-smi` inventory probe, with an eight-second timeout. A nonempty `EDGE_DIAGNOSTICS_PATH` enables local diagnostic receipts. These files contain operator health data and should remain outside source control.

Bind the service to loopback. Its existing routes do not authenticate incoming callers, including the proxy and manual-heartbeat routes. Remote use needs a separate, authenticated operator ingress and a network policy for the configured upstreams. The Dockerfile binds inside its container; any host port publication must likewise bind to loopback unless a reviewed ingress supplies that boundary.

## Evidence and contribution targets

On 2026-10-16, 15 offline tests passed on Windows with Python 3.11.9 and the direct dependency pins above, including a repeat in a freshly installed virtual environment. The dependency consistency check passed. They cover inert defaults, configuration validation, credential redaction, missing configuration, mock upstream forwarding, and heartbeat task shutdown. HTTP traffic uses in-memory mocks; no model, account, live admin server, or device was contacted. Source syntax checks also passed. FastAPI reports deprecation warnings for the existing startup/shutdown event API; migrating to lifespan handlers remains maintenance work.

The source was not built into a container or tested against a complete Luminax deployment for this publication. The old Windows monitor, installer scripts, desktop shell, and duplicated deployment trees are not part of this Bridge component's export.

Useful next contributions are an admin/Bridge contract test with header-based control authentication; authenticated operator ingress; bounded request and response bodies; streaming proxy behavior; a transitive dependency lock; and a documented minimal stack that demonstrates brain, voice, authority, and shutdown together. The existing proxy buffers bodies and is not a production streaming gateway. Keep live tests explicitly opt-in and keep the default offline workflow usable without accounts or models.
