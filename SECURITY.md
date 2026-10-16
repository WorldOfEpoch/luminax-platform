# Security and deployment boundary

This is development source, not an audited production distribution. Keep services on loopback or behind separately configured authenticated ingress. Some bridge endpoints have no inbound authentication; do not expose them directly to the internet or an untrusted network.

Public defaults disable runtime initialization, scheduled work, bot startup, and bridge heartbeats. Enabling a component is an operator decision. Review that component's guide, credentials, network destinations, file access, and dependencies first. A successful model response must not confer permission to execute an operation.

The full source edition also includes administration, browser/network, finance, upgrade, host tooling and game-adapter code. Choose only the profiles you intend to evaluate. Their availability in source is not an authorization grant. Shared operator gates and separate feature opt-ins address demonstrated unsafe defaults; legacy route contracts and the larger monolithic applications still need dedicated integration and security review.

The command center starts offline until a deliberate connection. Saved microphone preferences cannot resume capture through a visibility change while offline. Windows/operator entry points describe the planned operation before their explicit application option is selected. Read the component's documented action boundary before enabling it.

Use separate least-privilege credentials for each integration. Keep environment files, model caches, identity stores, databases, runtime logs, and signing material outside version control. Bridge diagnostics redact known credential fields and configured tokens, but diagnostics from other systems still require review before sharing.

For a sensitive report, use GitHub's private vulnerability reporting option if the repository offers it. Otherwise open a minimal issue requesting a private reporting channel without exploit details, credentials, or personal data. Do not post an active token in an issue. If a credential has already been exposed, revoke it through its provider before sharing a sanitized report.
