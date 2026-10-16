# Luminax platform: public source preview

Luminax is a collection of assistant, local bridge, and Discord services. This repository opens the existing component source so people can inspect it, improve it, and help make setup reproducible. It is maintained by WorldOfEpoch, with Windows development as a priority.

**This is an early source release.** The selected components are real source code, but the larger private deployment is not included. A complete platform install and a live Discord-to-council workflow have not been demonstrated from this checkout.

## What is here

| Component | Purpose | Start here |
| --- | --- | --- |
| Olympus edge bridge | Health aggregation and explicitly configured local service routing | [Bridge source and setup](docs/bridge-source.md) |
| Assistant core | Assistant requests, authorization, scheduling and review routing | [Assistant and Discord boundaries](docs/council-discord-source.md) |
| Discord bot | Discord-facing assistant commands and optional steward behavior | [Assistant and Discord boundaries](docs/council-discord-source.md) |
| Discord operations bot | Operations and review request routing | [Assistant and Discord boundaries](docs/council-discord-source.md) |
| Six-member council engine | Owned by the separate Zeus project | [Zeus system](https://github.com/WorldOfEpoch/zeus-system) |

The Discord services and council engine have separate owners. Their presence in public source does not establish an end-to-end integration. The next milestone is a small, tested adapter with explicit permissions, cancellation, and a synthetic offline example.

## Start without connecting accounts

Install Python 3.11 and Node.js 22. From a fresh checkout, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
node scripts/audit-publication.mjs
python -m pip install -r payload/olympus-edge-bridge/requirements.txt
python -m unittest discover -s payload/olympus-edge-bridge -p "test_*.py" -v
python -m unittest discover -s payload/assistant-core/tests -p "test_*.py" -v
```

On Linux, activate the environment with `source .venv/bin/activate`. These checks use synthetic configuration and mocked or isolated code paths. They do not require Discord, a model, or the original platform deployment. See the component guides before starting any service.

Public defaults leave background work disabled and destinations unconfigured. Optional services need their own configuration and legitimate dependencies. Private runtime databases, accounts, models, identity files, keys, and captured data are not distributed.

## Where contributors can help

1. Reproduce the offline checks on a clean Windows machine and improve the setup guide.
2. Extract a small configuration contract for each existing service, with clear missing-dependency errors.
3. Build a mocked Discord-to-Zeus review adapter before any live account integration.
4. Add authorization and cancellation tests around the existing command owners.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution process and [SECURITY.md](SECURITY.md) for the deployment boundary. This release does not install a startup service or enable Space Bunny. Publishing related source is independent of any local plugin installation.

## License and provenance

The selected original source is released under the [MIT license](LICENSE). Third-party libraries are installed separately through the component requirements; their own licenses continue to apply. This repository starts with a cleaned source snapshot and does not expose the original private Git history.
