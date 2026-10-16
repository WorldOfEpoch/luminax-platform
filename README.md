# Mount Olympus / Luminax: public platform source

This repository opens the Mount Olympus / Luminax platform for people to use, inspect, adapt, and improve. It includes the assistant and administration services, local brain and media workers, orchestration, command center, Discord routing, Windows desktop and operator tools, deployment recipes, and example project sites. WorldOfEpoch maintains the project, with Windows development as a priority.

**This is a development source release.** Personal deployment records are replaced by examples and configuration you create locally. The code is available for community development; a successful source check is not evidence of a complete live deployment. [Source coverage](docs/source-coverage.md) identifies what is included, what was sanitized, and the remaining companion dependencies.

## What is here

| Component | Purpose | Start here |
| --- | --- | --- |
| Olympus edge bridge | Health aggregation and explicitly configured local service routing | [Bridge source and setup](docs/bridge-source.md) |
| Assistant core | Assistant requests, authorization, scheduling and review routing | [Assistant and Discord boundaries](docs/council-discord-source.md) |
| Discord bot | Discord-facing assistant commands and optional steward behavior | [Assistant and Discord boundaries](docs/council-discord-source.md) |
| Discord operations bot | Operations and review request routing | [Assistant and Discord boundaries](docs/council-discord-source.md) |
| Six-member council engine | Owned by the separate Zeus project | [Zeus system](https://github.com/WorldOfEpoch/zeus-system) |
| Platform services and workers | Administration, brain/voice/image/vision, orchestration, network and specialist lanes | [Service inventory](docs/platform-services.md) |
| Deployment and registries | Compose profiles, fresh local secrets, synthetic registry examples, storage and ingress | [Full platform setup](docs/full-platform-setup.md) |
| Windows desktop and operator tools | Desktop shell, node/model/tunnel controls, development integrations | [Windows tools](docs/windows-platform-tools.md) |
| Command center and project sites | Existing operator UI, swarm board, GroupUp and site examples | [Web sources](docs/web-and-example-sites.md) |

The Discord services and the separate Zeus council engine have different owners. A tested adapter between them remains a milestone. Some legacy game services also need companion modules and game builds that are not part of this platform's source.

## Start without connecting accounts

Install Python 3.11 and Node.js 22. From a fresh checkout, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
node scripts/audit-publication.mjs
python -m pip install -r payload/olympus-edge-bridge/requirements.txt
python -m unittest discover -s payload/olympus-edge-bridge -p "test_*.py" -v
python -m unittest discover -s payload/assistant-core/tests -p "test_*.py" -v
node --test tests/web-publication.test.mjs
```

On Linux, activate the environment with `source .venv/bin/activate`. These checks use synthetic configuration and mocked or isolated code paths. They do not require Discord, a model, or the original deployment. CI also checks the additional service/configuration owners and builds the example static site.

## Evaluate the platform locally

Read [the complete setup guide](docs/full-platform-setup.md) and choose a model you already have installed. Its configuration initializer creates independent local credentials and sample configuration. The original installer now works from tracked source instead of embedding another copy of the stack. Operator scripts default to showing their plan; applying a change is explicit.

The command center opens offline until you choose **Connect local platform**. Background workers, bots, provisioning, model warmup, upgrade actions, and other consequential capabilities require deliberate configuration. Installed model weights, accounts, databases, captures, and local secrets belong to each operator's own installation.

## Where contributors can help

1. Reproduce the offline checks on a clean Windows machine and improve the setup guide.
2. Reproduce the minimal local stack, improve service dependency locks, and record exact working versions.
3. Build a mocked Discord-to-Zeus review adapter before any live account integration.
4. Add authorization and cancellation tests around the existing command owners.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution process and [SECURITY.md](SECURITY.md) for the deployment boundary. This release does not install a startup service or enable Space Bunny. Publishing related source is independent of any local plugin installation.

## License and provenance

The published original source is released under the [MIT license](LICENSE). Third-party libraries are installed separately through component manifests; their own licenses continue to apply. See [dependency and source notices](THIRD_PARTY_NOTICES.md). Historical design documents are explicitly labeled as earlier plans, and the source inventory records the reviewed release files.
