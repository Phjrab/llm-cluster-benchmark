# Security Policy

## Supported scope

Security fixes target the current `main` branch. This project is a private-lab
Controller/Worker benchmark, not an Internet-facing multi-tenant service.
Supported operation assumes trusted users and an isolated RFC1918 LAN. Do not
forward Dashboard, Worker API, or native llama.cpp RPC ports from a public
router or use unauthenticated RPC on public Wi-Fi.

The complete operating threat model, SSH pinning procedure, optional token
authentication, model-download boundary, RPC cleanup check, and retention
policy are documented in
[`docs/security/remote-operation-and-retention.md`](docs/security/remote-operation-and-retention.md).
Sensitive result storage modes are documented in
[`docs/security/sensitive-data-storage.md`](docs/security/sensitive-data-storage.md).

## Reporting a vulnerability

Do not open a public issue containing credentials, SSH material, private IP
inventory, medical prompts/responses, unpublished result artifacts, or a
working exploit. Use GitHub's private vulnerability reporting feature for this
repository when it is available. If that feature is unavailable, contact the
repository owner through a private channel already established for the lab and
share only the minimum reproduction needed.

Include the affected commit, platform, impact, safe reproduction steps, and
whether a token, model, result, or active Worker may have been exposed. Remove
real patient or research content and replace secrets with test-only values.

Until the report is assessed, isolate affected Workers, stop new experiments,
leave SSH host-key pinning enabled, and do not publish logs or artifacts. Token
authentication does not encrypt traffic; use a VPN or TLS boundary if the
Controller is accessed remotely.

## Non-security limitations

Performance differences, model quality, expected RPC inefficiency, and
hardware-specific package failures are normally engineering issues unless they
cross a trust boundary or expose protected data. The absence of a project
license is an owner decision and is not resolved by this policy.
