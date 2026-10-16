# Contributing

Open an issue describing the problem, component, operating system, expected behavior, and a small sanitized reproduction. Other people can add their own reproduction or fix to the same issue. Never attach credentials, personal conversations, private server identifiers, deployment files, or raw runtime logs.

Fork this repository, make a focused branch, and submit a pull request. Run the offline checks in the README. Preserve the original command owner rather than creating a second scheduler, proxy, or council controller. Keep background work opt-in and make shutdown observable.

New source files must be reviewed and added to `PUBLICATION_FILES.json`. Run `node scripts/audit-publication.mjs` before installing dependencies in a clean checkout. The guard detects several common accidental disclosures and unexpected files; it is not a full security audit.

State exactly what you verified: source inspection, offline unit checks, dependency installation, service startup, live Discord behavior, and model behavior are different evidence. Tests here do not establish a working full deployment. For integration changes, use synthetic requests first and explain every external service required.

The community goal is a portable platform with simple configuration, explicit permissions, interchangeable model adapters, and useful diagnostics. Changes that require a particular private server or maintainer account do not meet that goal.
