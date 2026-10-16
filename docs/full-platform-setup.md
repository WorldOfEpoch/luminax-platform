# Run a local Mount Olympus source checkout

This repository contains the authored service sources, command center, configuration, and local launchers. Initial setup provides a local console with runtime work disabled. Selecting a model and an optional service is a separate, deliberate step. No published package contains the maintainer's accounts, private deployment, model weights, game binaries, or saved conversations.

The release was checked offline: configuration generation, JSON parsing, Compose rendering, and the recorded source tests. Container image builds, the running stack, authenticated model responses, Discord delivery, microphone/audio routing, and game sessions remain unverified in this public checkout. Image tags and Python service requirements are not a fully locked, reproducible supply chain.

## Prerequisites

- Python 3.11 or newer.
- Docker Desktop with Linux containers on Windows, or Docker Engine on Linux, and a current Compose v2+ CLI. This package uses Compose `include` and `config --no-env-resolution`; use a current release that supports those options. The optional GPU override needs Compose 2.30+ and a compatible NVIDIA setup.
- OpenSSL for the local web password hash. Git for Windows includes it; the initializer discovers it from the actual Git installation. An explicit `--openssl` path is also supported.
- Disk, RAM, and any GPU required by the models you choose. No model is installed or pulled by these setup helpers.

The canonical service definition is `payload/docker-compose.yml`. `server-compose.yml`, `compose.server.yml`, `compose.pc.yml`, and `edge-compose.yml` include that same definition for compatibility; they are not separate deployments. Docker's [profiles documentation](https://docs.docker.com/reference/compose-file/profiles/) explains opt-in selection. The public launchers require an explicit profile list.

## Inspect and prepare

Run these commands from the repository root:

```text
python -B -m unittest discover -s payload/tools/tests -v
python payload/tools/check_public_config.py
python payload/tools/check_public_config.py --compose
```

The last command invokes only [Compose configuration rendering](https://docs.docker.com/reference/cli/docker/compose/config/) with synthetic values. It does not start containers, read your service `.env`, pull images, or contact a provider. Without Docker, omit `--compose`; the result explicitly says Compose was not checked.

Create the private local configuration once:

```text
python payload/tools/initialize_local_config.py
```

If you already have a local model installed, you can select its exact tag while creating the file:

```text
python payload/tools/initialize_local_config.py --model your-installed-model-tag
```

These commands generate `payload/.env`, `payload/runtime/luminax.htpasswd`, and `payload/runtime/turnserver.conf`. They generate separate control, database, web, voice-join, TURN, and account-vault secrets. The application database password is shared with the same application's optional Underfoot database user. Secrets are never printed. Existing files are preserved: a second initialization refuses to overwrite them. Keep these files private and backed up; they are excluded from Git and the Docker build context. On Windows, restrict folder access using your normal Windows account permissions.

If OpenSSL is unavailable, use `--openssl` with your installed executable. Do not replace the generated hash with a plaintext password. To change credentials later, update the matching consumers together; database credentials on an existing volume are not changed merely by editing `.env`.

## Preview and start the local console

PowerShell:

```powershell
.\windows-oneclick-install.ps1 -Local -Profiles core
.\windows-oneclick-install.ps1 -Local -Profiles core -Apply
```

Linux or Git Bash:

```sh
bash luminax.sh --profile core
bash luminax.sh --profile core --apply
```

The first command previews the selection. The explicit apply command builds and starts only the selected local stack. It can download public base images and package dependencies as part of building; this is not an offline build. The core profile contains the configuration seed, Redis, Qdrant, MySQL, assistant-core, admin-api, and the web console. Every service has automatic restart disabled.

Open `http://localhost:6969`. The web login is `operator`; read `LUMINAX_WEB_PASSWORD` privately from `payload/.env`. The console stays offline until you choose its connection option or open `http://localhost:6969/?connect=1`. For operations that request a control token, use `CONTROL_TOKEN` from the same private file. A disabled-runtime response is expected at this stage.

All published host ports bind to `127.0.0.1`; the database and vector store have no published host ports. Service-to-service discovery uses Compose names. Source is mounted read-only. Runtime configuration and writable outputs use Docker named volumes, including dedicated sites, reports, memory, model, and project storage. See Docker's [port binding reference](https://docs.docker.com/reference/compose-file/services/#ports).

The `config-init` service seeds the configuration volume once. It preserves an initialized volume and refuses a nonempty, unmarked destination. Updating source examples does not overwrite an operator's existing runtime configuration. Back up that volume before deliberately migrating or resetting configuration. Do not use `down --volumes` as a routine update step.

Example site sources in the repository's `sites/` directory are not automatically published or imported into the writable runtime sites volume. Select, build, and import the examples you intend to use separately.

## Enable a model deliberately

Before changing `LUMINAX_RUNTIME_ENABLED`, review `payload/.env`:

1. Set `OLLAMA_HOST` to your provider's compatible local endpoint. The supplied container setting is `http://host.docker.internal:11434`; the Compose host mapping supports that name. Verify the endpoint from your own machine and confirm it is not an unintended public listener.
2. Set the `OLLAMA_CHAT_MODEL_*` tags you intend to use. Set `OLLAMA_EMBED_MODEL` and `OLLAMA_EMBED_DIM` to a compatible, installed embedding model and its actual output dimension. A model catalog entry is a candidate, not a compatibility certification.
3. Set `LUMINAX_RUNTIME_ENABLED=true` only when ready for the selected services to handle requests, then apply the same core profile to recreate affected containers. Keep `OPENAI_ENABLE=false` unless intentionally configuring that provider with your own account and key.
4. Workers remain paused in the seeded runtime policy. Keep them paused while testing one manual request and inspecting the result. Configure and unpause only the capabilities you intend to use through the authenticated control surface. The default execution profile is the implemented `pro_only` value; it does not itself supply a Codex account or authorize cloud work.

Model downloads, paid calls, external connectors, and unattended work are operator choices. Runtime enablement does not turn on host execution, browser actions, network scanning, automatic upgrade application, or finance simulation. Their separate `LUMINAX_*_ENABLED` flags default to false. The source release does not install or enable Space Bunny.

## Optional profiles

Select `core` together with each optional profile. For example, the preview below selects the source voice services without starting them:

```sh
bash luminax.sh --profile core --profile voice
```

| Profile | Included source | Additional setup and limits |
| --- | --- | --- |
| `browser` | Browser agent | Browser actions require their separate flag and operator token. |
| `discord` | Main, Zeus, community, operations, and broadcast adapters | Supply your own application registrations, bot tokens, allowed operator IDs, and guild settings. No bot sends or registers commands during setup. Start only the intended bot service if you are not configuring every adapter. |
| `voice` | Voice agent, realtime gateway, affect controller, edge bridge | Supply the selected audio/model/provider settings and test real capture, playback, and receiving separately. |
| `models` | Image, vision, and Luminax brain services | Install/select licensed compatible models and review hardware requirements. No weights are bundled. |
| `orchestration` | MCP router and LangGraph orchestrator | Configure only the tools and downstream services you authorize. |
| `specialist` | Research, sites, accounts, network, 3D, finance services | Tokens and separate action flags remain required. Finance simulation is not a brokerage connection. |
| `hive` | Dreamer, engineer, critic | Model selection and explicit worker configuration are required. |
| `maintenance` | Daemon and upgrade agent | Host execution and automatic apply remain off. Read-only source mounts and the absence of a Docker socket intentionally prevent host/container administration from this checkout. |
| `underfoot` | World API, allocator, Go voice gateway, TURN configuration | Supply your own game project/contracts/builds and review player authentication. The public stack does not mount a Docker socket or supply an allocator execution backend; this is not a complete playable game deployment. TURN is local-only, with TLS disabled in its generated development configuration. |
| `tls` | HTTPS console at `https://localhost:6970` | First run `bash payload/generate-local-certs.sh`; it creates a 30-day localhost certificate without installing trust or replacing existing certificates. Browser trust and runtime validation remain your responsibility. |

For an intentional GPU build, combine the canonical file with `payload/compose.gpu.yml` and select `core` plus `models`. The override requests GPU access only for the image agent. It does not install drivers or validate hardware compatibility.

Private settings are shared among selected application containers through `.env`. Treat the local operator and those containers as one trust boundary. This setup is for local evaluation; a public Internet or multi-user deployment needs a separate authentication, secret distribution, network, and operational review.

## Updates, reports, and stopping

Review source changes and rerun offline checks before an explicit apply. `payload/update.sh` delegates to the same local launcher and defaults to a plan. It does not pull Git changes, schedule updates, or restart a remote system. `payload/restart-underfoot-runtime.sh` selects the same core and Underfoot profile plan; it does not launch a private game installation.

The workspace atlas uses the existing builder at `payload/tools/workspace_atlas_builder.py`. Its default is read-only inspection of this payload. `--apply` writes only `payload/reports/atlas`; source configurations, private runtime directories, caches, and sibling projects are not rewritten. Reports can contain your configured local paths and should remain private.

```sh
bash luminax.sh --profile core --action status --apply
bash luminax.sh --profile core --action down --apply
```

Include the same optional profiles when stopping their services. Preserve data volumes when updating. No scheduled startup, bot registration, provider invocation, or remote deployment is performed by the offline checks documented here.
