# Secure Remote Operation and Result Retention

## 1. Supported operating modes

| Mode | Intended network | SSH host keys | Dashboard / Worker auth | Dashboard exposure | llama.cpp RPC |
|---|---|---|---|---|---|
| Trusted LAN | single-user, isolated private LAN | TOFU (`accept-new`) allowed | may be off | private address only | private, experiment-scoped only |
| Pinned LAN | shared or less controlled private LAN | explicit project-local pin required | enable both | private address only | private, experiment-scoped only |
| Remote / VPN | WireGuard, Tailscale ACL, or equivalent private VPN | explicit pin required | enable both | TLS reverse proxy or VPN address | tunnel/private segment only |
| Publication / archive | offline analysis and long-term preservation | not applicable | not applicable | no live exposure required | disabled |

The application does not turn a trusted-LAN installation into an Internet-safe
service automatically. Port 8080 (Dashboard), port 8000 (Worker API), and RPC
ports must not be forwarded directly from a public router.

## 2. Threat model

Protected assets are SSH identities, Dashboard and Worker tokens, model files,
benchmark prompts and responses, telemetry, formal campaign records, and the
source/runtime identity used to reproduce a result.

Relevant threats are:

- an untrusted LAN peer changing models, starting workloads, or reading results;
- DNS or route manipulation sending SSH to the wrong machine;
- a Worker being reinstalled or replaced while retaining an old IP address;
- token disclosure through URLs, logs, a reverse proxy, or plaintext transport;
- a persistent unauthenticated RPC listener remaining after an experiment;
- accidental result deletion or deletion of formal campaign evidence;
- confusing a Controller wheel/import artifact with a native Worker deployment.

Trust boundaries are the browser-to-Controller HTTP connection, Controller-to-
Worker SSH and HTTP connections, the temporary llama.cpp RPC transport, and the
filesystem boundary around `.run/cluster` and result archives.

Residual risks remain explicit:

- trusted-LAN mode uses TOFU on first SSH contact;
- tokens do not encrypt traffic; VPN or TLS is still required off an isolated LAN;
- llama.cpp RPC is unauthenticated and experimental;
- pinning proves continuity of an SSH host key, not the physical identity of a
  machine unless the fingerprint is verified out of band;
- locally privileged users can read local process state and result files.

## 3. SSH host-key pinning

Pinned keys are stored in the Controller runtime directory as a private
project-local `known_hosts` file. They are not written to a global user file.
The pin must be verified through a second channel such as a directly attached
terminal before it is accepted.

For every Worker:

```bash
python -m cluster.clusterctl --node edge-worker-01 host-key-scan
```

Compare the displayed SHA-256 fingerprint with this command on the Worker:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

After the values match exactly:

```bash
python -m cluster.clusterctl --node edge-worker-01 \
  host-key-pin --fingerprint 'SHA256:verified-value' --confirmed
python -m cluster.clusterctl host-key-list
```

Pin every enabled Worker before selecting **고정 지문 · 등록 키만 허용** in
Dashboard settings. Pinned mode uses strict host-key checking and fails closed
when a key is missing or changes. A legitimate reinstall therefore requires a
new out-of-band verification and explicit re-pin; do not bypass the mismatch.

The same scan and pin operations are available through:

- `GET /api/nodes/{node_name}/ssh-host-key`;
- `POST /api/nodes/{node_name}/ssh-host-key` with the exact fingerprint and
  `confirmed=true`.

## 4. Remote access and TLS

Prefer a VPN with an allowlist that lets the Controller reach Worker SSH/API
addresses but does not make Worker APIs public. Enable Dashboard and Worker
token authentication before remote use. Then place only the Dashboard behind a
TLS reverse proxy. The proxy should:

- terminate TLS with a valid certificate;
- forward to `127.0.0.1:8080` or a private VPN address;
- preserve the `X-Cluster-Token` request header;
- disable request-body logging and redact authorization-like headers;
- set conservative body/time limits suitable for Server-Sent Events;
- restrict source addresses when the VPN or firewall supports it.

Do not pass a Dashboard token in a query string. The Dashboard client uses a
header for JSON and event-stream requests. Do not expose the Worker API through
the reverse proxy.

## 5. llama.cpp RPC boundary

RPC servers are started only for one model-parallel experiment and are stopped
on success, failure, cancellation, and Controller recovery. A persistent boot
service for an RPC server is prohibited.

Use one of these boundaries:

1. an isolated private VLAN whose firewall admits only selected experiment
   nodes; or
2. an SSH tunnel that keeps the RPC listener on loopback.

RPC has no application token authentication. Worker API authentication and
direct RPC mode are therefore treated as incompatible rather than being shown
as fully secured. Never forward an RPC port from a public router, and inspect
the durable cleanup status before accepting a run as complete.

## 6. Recoverable result lifecycle

Deleting a terminal run moves its complete directory into private
`results/_trash/`; it does not erase it. The Dashboard **결과 휴지통** can list
entries, show their deterministic content SHA-256, and restore them. Suite
tombstones preserve enough relationship data to reconnect a restored model run
where possible.

Permanent deletion is a distinct operation. It requires:

1. an explicit confirmation;
2. re-entering the run ID in the Dashboard;
3. the current 64-character trash content checksum; and
4. a server-side checksum re-read immediately before deletion.

Formal results and every result carrying a campaign identity are retention-
protected and cannot be permanently deleted through the product API.

Recommended policy:

- keep ordinary trash for at least 30 days;
- keep failed pilot runs with the same study archive as successful pilot runs;
- never automatically purge campaign/formal evidence;
- export an archive before manual permanent deletion;
- record archive filename, byte size, SHA-256, Git commit, runtime-lock
  fingerprint, and the included run/campaign IDs in an external archive index;
- verify the archive by extracting it into a temporary directory and hashing it
  before approving deletion.

There is deliberately no automatic retention daemon. Retention decisions stay
visible and user-controlled until a separately reviewed archive index and
backup destination exist.

## 7. Deployment modes

The supported operational deployment is a source checkout:

- the Mac Controller runs the Dashboard, durable scheduler, CLI, and analysis;
- each Worker receives the verified source subset and owns its project `.venv`,
  native inference backend, models, and pinned llama.cpp RPC build;
- Worker deployment identity is captured in the deployment manifest.

The Python wheel is supported as an importable Controller/library artifact for
isolated tests and downstream tools. It is not a substitute for Worker source
deployment, native CUDA/OpenBLAS builds, shell lifecycle scripts, or model
storage. No console entry point is declared yet because the operational launcher
depends on a checked-out project layout and runtime assets. The supported short
command remains the source-checkout `scripts/llm-cluster` symlink installed by
the Controller setup flow.

## 8. Incident checks

If a Worker key changes unexpectedly, leave pinned mode enabled, stop new jobs,
and verify the machine locally. If a token may have leaked, rotate the relevant
token, restart the affected service, and confirm that no URL/query logs contain
the old value. If an RPC cleanup fails, isolate the Worker network first and use
the exact project lifecycle command; do not use broad `pkill` or `killall`.
