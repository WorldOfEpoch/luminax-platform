# MyPetHuman: repositories, tasks, and home-server controls

MyPetHuman is a small companion view served by the existing Luminax operator UI at `/mypethuman.html`. Open **MyPetHuman** in the command-center navigation. Home shows one current item; Agents opens each repository's recent work and source links.

## Read a public repository immediately

The HTML also works as a standalone file. Choose **Add repository** and enter `owner/repository`. Its public mode makes real, anonymous GitHub REST reads from the browser. It stores connection choices locally, never GitHub credentials or task contents. Refresh is explicit, and a failed read remains an error.

An explicit startup link can select repositories: `mypethuman.html#repos=owner/repository,owner/another-repository`. Repository names in this URL are visible to anyone receiving it; use it only for public repositories.

Issue results are bounded recent open issues, with pull requests excluded. They are not a complete backlog or an AI-generated priority assessment. Open the source link to work in GitHub. Public reads are subject to GitHub's anonymous API limits.

## Connect the existing home base

Serve the file from the existing authenticated Luminax origin and choose **Connect home base**. Keep the current runtime and ingress configuration. The view reads the existing project/task APIs and agent registry. Registry entries describe configured agents; their presence does not establish that a worker is running.

Task capture and status changes use the existing task owner and verified operator session. A saved confirmation requires a successful response and read-back. Check the saved task before retrying an uncertain write.

## Control home-server services

Open **Home server** from the connection details. The sheet reads the existing admin owner through the same authenticated origin. It first checks runtime availability, operator authorization, the existing control grant, host execution, and maintenance policy. These checks do not enable runtime, grant control, or start a service.

The service list combines the configured control catalog with the existing operational status endpoint. Start, stop, and restart are available only for matching Docker service rows and permitted actions. Each action requires reviewing the selected service, checks the gates again, sends one request, and reads status afterward. Stopping a control service can interrupt the connection. An uncertain result remains uncertain; the app never retries a lifecycle command automatically. A running status after a restart request confirms current status, not a verified restart event.

| Same-origin route | Purpose |
| --- | --- |
| `GET /admin/services/control` | Read authorization/gate state, selectable services, and declared service profiles |
| `GET /admin/services/status` | Read observed service state through the existing Docker/HTTP checks |
| `POST /admin/services/start` | Request a service start through the existing owner |
| `POST /admin/services/stop` | Request a service stop through the existing owner |
| `POST /admin/services/restart` | Request a service restart through the existing owner |

Lifecycle requests use the existing `{ "token": "", "name": "service-name" }` body and authenticated operator session. The owner rechecks its own control and maintenance gates; host commands still require `LUMINAX_HOST_EXECUTION_ENABLED`. The new catalog limits targets offered by this view. It does not change the existing mutation endpoints into a per-service authorization system.

The new control snapshot is observational: no commands, probes, grants, or profile initialization. The existing status endpoint performs its usual operational checks and may initialize its supporting identity or database records. Neither read requests lifecycle repair.

## Extend service capabilities and routes

The Home server sheet also reads configured service profiles from the existing `config/olympus/services/*.json` directory. A profile can describe `key`, `display_name`, `role`, and `startup`, plus optional `capabilities` (a list of names) and `validation_routes` (an object mapping an owner name to a relative route path). These fields are bounded display metadata. The app labels them as declared and never calls a route taken from profile content.

New services still need an adapter and authorization in their existing runtime owner. Existing Darius agents can register or heartbeat through `POST /api/darius/agents/register` and `POST /api/darius/agents/heartbeat`, which accept project identity, capabilities, workspace, and routing metadata under the existing connector authentication. Operator route changes remain in `PUT /api/darius/projects/{project_key}/route`. MCP tools remain registered in the MCP owner's source registry and dispatch handlers. Adding profile metadata alone does not install a plugin, expose a callable tool, or establish live availability.

## Configure repository scopes on the server

In the existing operator environment, configure:

```dotenv
MYPETHUMAN_GITHUB_REPOSITORIES=owner/repository,owner/another-repository
MYPETHUMAN_GITHUB_TOKEN=
MYPETHUMAN_PROJECT_METADATA={}
```

The allowlist supports up to 20 repositories. An optional provider token stays in the server process; grant only the repository and issue read access needed for the selected repositories. It is separate from the Luminax operator credential. The browser never receives it.

Optional metadata is a JSON object keyed by an allowlisted repository. Each value can contain `name`, `purpose`, `kind` (`project`, `business`, or `research`), and `business_name`. Keep personal business records and provider credentials in local configuration, outside the public repository.

The new backend routes are:

| Route | Read |
| --- | --- |
| `GET /api/mypethuman/status` | Runtime/configuration state; no provider call |
| `GET /api/mypethuman/projects` | Configured repository metadata from GitHub |
| `GET /api/mypethuman/projects/{owner}/{repo}?limit=20` | One configured repository and bounded recent open issues |

These routes require the enabled runtime and the existing operator authorization. They refuse unconfigured repositories and upstream redirects. The assistant-core image includes the helper through its existing Dockerfile; no new service or scheduler is introduced.

This connection tracks work when opened or refreshed. It does not start autonomous agents, import issues as local tasks, or write to GitHub. Email, payments, social matching, cameras, and embodied VR remain separate integrations; this view makes no claims that those are connected.

## Checks

```sh
node scripts/audit-publication.mjs
python -B -m unittest discover -s payload/assistant-core/tests -p 'test_*.py' -v
python -B -m unittest discover -s payload/admin-api/tests -p 'test_*.py' -v
node --test payload/luminax-ui/mypethuman.test.mjs
```

The offline tests exercise the production parsers and owner functions with synthetic provider responses and mocked external boundaries. A deployment still needs its own authenticated browser check, actual GitHub reads, task write/read-back, and a chosen service action followed by status verification against the operator's running installation.
