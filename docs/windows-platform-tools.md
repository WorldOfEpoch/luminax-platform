# Windows and development tools

This release includes the authored Windows operator scripts, WinForms/WebView2 shell, Darius and Epoch Portal development frontends, Python asset/assignment clients, and migration helpers. They are source components of Mount Olympus. Publishing them does not install a monitor, enable a connector, or start a model.

## Local setup

Use the tracked `payload/docker-compose.yml` and its configuration owner. Prerequisites are Python 3.11 or newer, OpenSSL, and Docker with Compose. Install and start Docker deliberately. The initializer can discover OpenSSL supplied with Git for Windows; otherwise pass `-OpenSsl` below. See [full platform setup](full-platform-setup.md) for service credentials, available optional profiles and model configuration.

From the repository root in PowerShell:

```powershell
# Preview: no configuration is read and no process is launched.
.\windows-oneclick-install.ps1 -Local -Profiles core

# Explicit setup/start: creates new local configuration if absent, then builds/starts core.
.\windows-oneclick-install.ps1 -Local -Profiles core -Apply

# Choose additional features explicitly; this example requires Discord configuration.
.\windows-oneclick-install.ps1 -Local -Profiles core,discord -Apply

# Status and stop use the same selected project. Stop preserves named volumes.
.\windows-oneclick-install.ps1 -Local -Profiles core -LocalAction status -Apply
.\windows-oneclick-install.ps1 -Local -Profiles core -LocalAction down -Apply
```

`-OpenSsl` accepts the path to an installed executable. Local mode does not install prerequisites, register scheduled tasks, establish SSH tunnels, import an existing private configuration, or pull a model. Compose may download container images/build dependencies when explicitly started. Partial existing configuration causes the initializer to fail instead of overwriting it.

Linux or Git Bash uses the same Compose source:

```sh
python payload/tools/initialize_local_config.py
bash luminax.sh --profile core
bash luminax.sh --profile core --apply
bash luminax.sh --profile core --action status --apply
bash luminax.sh --profile core --action down --apply
```

`luminax.sh` is a small launcher. The old embedded full-stack archive is not part of this release. `ubuntu-one-shot-install.sh` delegates to the same initializer and launcher; it no longer installs OS packages or enables boot services. Profile selection is always explicit. The launcher prints its plan before applying, and its default invocation does not read secrets or call Docker.

## Desktop shell

Build on Windows with a .NET SDK that supports `net8.0-windows`:

```powershell
dotnet restore .\desktop-shell\Luminax.DesktopShell.csproj
dotnet build .\desktop-shell\Luminax.DesktopShell.csproj --no-restore -c Release
```

Restore obtains Microsoft WebView2 and framework dependencies when they are not cached. The WebView2 runtime is an external requirement. Run the built executable deliberately when ready. Enter the configured local UI URL and select **Connect**. The shell starts on an embedded welcome page, does not automatically connect, and leaves microphone permission to the browser prompt. It accepts HTTPS or loopback HTTP, rejects URL-embedded credentials, and does not disable certificate checks. Build artifacts, browser state and diagnostics are not included in the source release.

## Optional Windows operators

Every original PowerShell operator entry point defaults to preview. `-Apply` enables its documented operation; `-DryRun` takes precedence. Read the specific parameters and source before choosing an advanced operation. `windows.env.example` is an empty configuration reference for optional `.env.windows`; it is separate from the Compose `.env` owner.

| Existing owner | Role and current boundary |
| --- | --- |
| `windows-program.ps1`, `windows-controller.ps1` | Original operator UI and command dispatcher. Opening the UI requires `-Apply`; it is distinct from the desktop web shell. Remote installation modes retain historical workflow assumptions and have not been accepted against this Compose release. |
| `windows-bootstrap.ps1`, `windows-oneclick-install.ps1` without `-Local`, `auto-deploy-project-light.ps1`, `push-ubuntu-deploy.ps1`, `restart-ubuntu-luminax.ps1`, `sync-deploy-folders.ps1`, `map-ubuntu-luminax-drive.ps1` | Advanced remote workflow source. Requires explicit host/user/key/path configuration and existing SSH host trust. Use local setup above for the supported source preview; old installer-mode automation needs adaptation to the compact launcher before production use. |
| `windows-model-bootstrap.ps1`, `move-existing-models-to-d.ps1` | Explicit model/dependency download and storage-migration operators. Model downloads require an explicit `-Models` list. Optional third-party source needs `-OptionalToolRepository`; provider diagnostics additionally need `-RunProviderDiagnostics`. Downloads need network/storage and may be large. Migration preserves a renamed source backup; it refuses to kill running Ollama instances. The historical filename does not require a particular drive. The distinct `payload/windows-model-bootstrap.ps1` source is preserved with the same opt-in boundaries. |
| `windows-start-local-brain.ps1`, `windows-stop-local-brain.ps1`, `windows-local-brain-status.ps1` | Optional native-process workflow using tracked payload services. It depends on separately configured services, dependencies and tunnels and has not been runtime validated. Local binds are loopback; automatic action execution is disabled. Shell launch needs `-StartShell`. |
| `windows-private-control-tunnel.ps1`, `windows-service-tunnels.ps1` | Explicit configured SSH tunnels. No automatic host trust. Stop operations verify executable and start time from a launch receipt, never kill a process based solely on a port or old numeric PID. Unverified old processes are preserved for manual inspection. |
| `install-windows-node-monitor.ps1`, `windows-node-monitor.ps1` | Optional persistent monitor. Installation requires `-Apply`, refuses an existing task name and registers a task for the next sign-in. `-StartMonitor` additionally starts it immediately. The tool broker is disabled unless separately selected. Tokens are not put into scheduled-task command arguments; this legacy installer stores configured values in the current user's environment, so use a dedicated limited token and local account. No monitor was installed by this release work. |
| `payload/windows-companion-launcher.ps1`, `.vbs` | Optional wrapper for the same monitor. PowerShell needs `-Apply`, and the hidden VBS launcher needs exactly `--apply`. The wrapper uses an explicitly selected settings file or process environment and a non-personal node key. It repairs the original incompatible call into a model installer by invoking the shared monitor with supported parameters. `PollSeconds` is retained as a deprecated compatibility argument; the monitor owns polling. |

## Darius and Epoch Portal

The tools retain their authored asset-job tracking, assignment, bridge and editor workflows. No project database, memory, conversation history, native account identifier or asset package is bundled. Legacy `UNDERFOOT_*` names remain compatibility aliases; they do not supply project permissions or licensed content.

Python clients require the standard library. Every subcommand previews by default:

```sh
python tools/CodenameDarius/python/darius_agent_cli.py sync
python tools/EpochPortal/python/epoch_portal_cli.py sync
```

Set `DARIUS_PROJECT_ROOT` and either `DARIUS_ENV_FILE` or `LUMINAX_TOOLS_ENV_FILE` explicitly. Only that selected settings file is read. To perform an operation, place `--apply` **before** its subcommand. Provider calls require your own credential and may cost money. Retained job IDs and refresh commands should be used to resume existing jobs; do not blindly resubmit an uncertain request. Credential-bearing Python requests reject redirects and use verified TLS. Asset downloads remain separate from engine acceptance.

Empty project examples are in `tools/CodenameDarius/templates/codename-darius.env.template` and `tools/EpochPortal/templates/epoch-portal.env.template`. Store configured copies outside the published tree; the link installers do not import them automatically.

The existing `Install-CodenameDariusLinks.ps1` and `Install-EpochPortalLinks.ps1` require explicit `-ProjectRoot` and `-UnityProjectRoot`. Preview first, then add `-Apply`. They create links to the shared source and helper after validating all targets. They never erase existing project files, copy credentials, or replace an environment file. Hard links require compatible local volumes; Windows junction/link permissions may apply.

Unity editor windows start with tool actions disabled. Select **Enable tool actions this session** before initialization or bridge/model work. They pass the same CLI `--apply` flag; bridge requests have a bounded timeout, reject redirects and require HTTPS or loopback HTTP. The Unity sources and GUIDs are preserved, but Unity import/compile, external bridge routes and game behavior remain unverified.

## Maintenance helpers

`tools/extract_payload_from_installer.ps1` and `tools/sync_payload_blocks_to_installer.py` are explicit legacy archive migration tools. They do not apply to the compact launcher. Extraction requires a new output directory and rejects paths outside it. `tools/sync_payload_to_base.sh` requires a new explicit destination and excludes credential/runtime/cache directories. It does not start services.

The Discord deploy/redeploy shell entry points now delegate to the selected public checkout's canonical Compose launcher. They do not upload source, rewrite credentials or change voice tuning. `tools/luminax_test_ladder.py`, `tools/underfoot_smoke.ps1` and `tools/self-upgrade-smoke.sh` are live operator probes: preview is the default; applying can send messages/tasks or model requests to a configured service. Their real endpoints were not called in this release work. The self-upgrade apply path needs a second explicit `APPLY=1` selection.

## Verification and remaining work

Offline checks:

```powershell
pwsh -NoProfile -File .\tools\tests\Test-PublicWindowsTools.ps1
python -m unittest discover -s tools/tests -p "test_*.py"
dotnet build .\desktop-shell\Luminax.DesktopShell.csproj --no-restore -c Release
```

The Windows suite parses the operator sources and checks their preview boundary, path containment and reused-process-ID rejection. Python tests check configuration-free previews, missing credential rejection and redirect handling with synthetic requests. Bash source is checked with `bash -n`; `run-todo-gates.sh` now invokes offline checks instead of live failure injection into the retired embedded installer.

These are source/build checks. They do not establish a Docker installation, signed-in Discord bot, microphone capture, Unity compilation, model quality, remote deployment, or installed desktop acceptance. Community work should report the exact component/version, selected profiles, expected behavior, sanitized result and rollback; never upload `.env`, process receipts, private logs or personal project databases.
