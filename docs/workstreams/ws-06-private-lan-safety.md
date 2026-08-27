# WS-06 Private LAN Safety

## Scope and gap analysis

This workstream preserves the trusted, isolated RFC1918 laboratory LAN model. It does not introduce public-cloud identity, TLS termination, Kubernetes, or a new remote execution protocol.

| Requirement | Existing state | Gap found | Resolution |
|---|---|---|---|
| Explicit threat model | SSH/RPC/token/retention risks and trusted/pinned/VPN modes were documented | Dashboard exposure table implied a private-address bind although the launcher is loopback-only | Corrected the contract and documented the supported proxy/tunnel boundary |
| Controller loopback default | Fixed `127.0.0.1:8080` launcher and lifecycle identity validation | None | Preserved unchanged |
| Worker/RPC interface scope | Inventory rejects public, hostname, CGNAT, and link-local endpoints | Worker API and native RPC used `0.0.0.0` | Bind each service to its validated inventory LAN IPv4; retain Pi coordinator's intentional loopback RPC device |
| Optional authentication | Dashboard/Worker tokens default off and can be enabled; RPC is blocked while Worker auth is on | None | Preserved and documented as convenience, not complete transport security |
| Download safety | Catalog supplied immutable URL, size, SHA, license and Controller disk preflight; partial files were removed on exceptions | Worker did not receive expected size and did not enforce redirect host, streaming ceiling, or local disk reserve | Add exact size to the additive request field, validate final redirect, optional domain allowlist, 128 GiB ceiling, and disk reserve |
| Fixed remote commands | SSH adapter uses fixed argv, validated nodes/paths, no `shell=True` | None | Preserved; cleanup check uses two fixed actions and ports |
| RPC cleanup registration | Start attempt is registered before SSH; success/failure/cancel cleanup is idempotent and tested | No single operator acceptance command checked both fixed listeners and managed identity | Add `rpc-cleanup-check` and Worker-side fail-closed assertions |
| Error redaction | Dashboard unexpected errors are correlation-only; action records omit secret options | Inference SSE echoed arbitrary backend exception text, which could include prompt/native output | Return stable generic inference failure plus exception type only |

## Design note

`InstallModelRequest.expected_size_bytes` is additive and defaults to zero for older custom Worker clients. Catalog actions always provide a positive exact size. The existing SHA-256 contract remains mandatory. A Worker with a legacy request still receives the global streaming ceiling and minimum disk reserve, while an exact catalog request additionally enforces declared and observed byte equality.

The download allowlist is optional because official repositories redirect through provider-controlled CDN domains that vary over time. When configured, both the initial and final hostname must equal or be a subdomain of a listed domain. Caller-supplied URL user-info, query data, and fragments are rejected so credentials cannot be smuggled through action argv or error text; provider-generated redirect query data is never persisted or logged.

Worker API and RPC bind values come only from the validated inventory record. The user cannot provide a free-form bind argument through Dashboard actions. The cleanup acceptance command similarly fixes ports 50052 and 18080 in the Controller implementation.

## Failure and compatibility behavior

- Oversize, insufficient disk, disallowed redirect, size mismatch, GGUF metadata mismatch, or checksum mismatch removes the temporary file.
- An unsafe or active RPC process identity, or any remaining listener on a fixed port, makes `rpc-cleanup-check` fail.
- Existing normal benchmark strategy semantics, result schemas, model identifiers, and Controller loopback URL are unchanged.
- Existing direct install clients that omit size remain schema-compatible.
- No model, Worker package, power mode, or runtime file is changed during automated tests.

## Verification

Focused tests cover source/final URL policy, disk failure before network access, streaming size overrun with no partial file, expected-size propagation, exact LAN bind values, fixed cleanup actions/ports, RPC lifecycle cleanup, and prompt/native-output redaction. The full Python, shell, repository, wheel, JavaScript, and Chromium gates run before checkpointing.

Hardware validation is `hardware unavailable` when no Worker inventory is reachable. No live download, inference, RPC listener, or remote cleanup is claimed in that case.

## Remaining risk

Token authentication does not encrypt traffic. Native llama.cpp RPC remains unauthenticated. Operators must keep the LAN isolated, avoid public Wi-Fi and public port forwarding, use pinned SSH keys on shared networks, and use VPN/TLS boundaries for remote access. The project does not manage host firewalls or router policy.
