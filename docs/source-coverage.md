# Public platform source coverage

This release expands the initial four-component preview into a sanitized public edition of the Mount Olympus / Luminax platform. The authoritative input is the owner's current platform source tree. Existing source owners and component names are retained so fixes have a clear home.

## Included source

| Area | Public location | Purpose |
| --- | --- | --- |
| Core and administration | `payload/assistant-core`, `payload/admin-api` | Assistant API, authorization, review, registry and operator administration |
| Orchestration and specialist services | Other service folders under `payload` | Daemon, research, account/site/network, hive, finance and upgrade workflows |
| Local brain and media | Brain, affect, voice, image, vision, bridge, router and gateway service folders | Configurable PC and server capabilities |
| Discord | `payload/discord-bot`, `payload/discord-ops-bot` | Existing liaison, operations and review clients |
| Operator interfaces | `payload/luminax-ui`, `web`, `desktop-shell` | Command center, swarm board and Windows desktop shell |
| Local deployment | `payload/docker-compose.yml`, related recipes and config tools | Explicit profiles, independent local credentials, registries and storage |
| Windows and development tools | Root operator scripts and `tools` | Existing node, model, tunnel, deployment and authoring commands, with deliberate application controls |
| Project site examples | `sites` | GroupUp, Olympus, Underfoot and PAPR source |
| Historical plans | `docs/design` | Earlier design records, labeled as historical rather than acceptance evidence |

The service, Windows and setup guides provide the detailed inventories and current limits. Optional/deprecated implementations remain source for contributors; their inclusion does not activate them or certify that their old deployment still works.

`PUBLICATION_FILES.json` is the exact reviewed file list for this edition. The publication check rejects unexpected files, common credential/path disclosures and unreviewed binary content. The six authored PNG assets have explicit immutable hashes. This limited guard complements human review; it is not a security certification.

## Sanitized or replaced

Personal operators, account identities, deployment records, project assignments, endpoints and local path defaults are replaced with portable examples or explicit configuration. New installations generate their own secrets. Private configuration is not a prerequisite for obtaining the public feature source.

The old self-extracting installer embedded duplicated copies of the services. Its public entry point instead uses tracked source and the canonical Compose recipe. The deployment/generated mirrors are not copied as competing source trees. Public setup and tests operate on the source owners included here.

Some earlier first-party service definitions already referred to missing companions, game builds, registries or external products. Those gaps are documented. A missing component is not replaced by a fake success response, and this repository does not claim to include complete sibling game projects.

## Excluded non-source material

- Git history, `.codex` chat/receipt state, personal operating runbooks, feedback and per-run records.
- Runtime databases, conversations, identity records, logs, PID files, capture/model caches and generated secrets/certificates.
- Compiled desktop output, package dependency directories and generated static-site output; their source and build manifests are included.
- Redundant deployment/generated trees and older archived snapshots superseded by the current source owner.
- Third-party SDKs, model weights and game assets that must be obtained through their own distributions.

These exclusions remove installation state and redundant output. The public edition is intended to expose the platform's actual implementation for community development, rather than require access to the original owner's installation.

## Validation boundary

The release notes identify the exact commit, source file count, archive checksum and automated workflow. Offline checks, syntax, static builds and configuration validation are reported separately from live PostgreSQL/MySQL, models, voice, Discord, browser interaction and full-stack behavior. Start with the [setup guide](full-platform-setup.md), choose a small capability, and contribute a reproducible result.
