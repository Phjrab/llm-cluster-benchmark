# Roadmap Phase 13 — Security, Deployment, and Retention

## 1. Outcome

Phase 13 is complete for the product and documentation scope defined by the
roadmap. It adds an opt-in strict SSH pinning mode, a recoverable and protected
result lifecycle, an operating-mode threat model, secure remote-access guidance,
and an explicit source-checkout versus Controller-wheel deployment boundary.

Trusted-LAN compatibility remains available. No firewall, VPN, reverse proxy,
system service, certificate, or boot-time process was installed or enabled.

## 2. SSH continuity and explicit pinning

The default `trusted_lan` policy retains the existing first-contact TOFU
behavior for a single-user isolated LAN. The new `pinned` policy uses a private
project-local known-hosts file and strict checking for every SSH and rsync path.

Pinning is deliberately two step:

1. scan the registered private endpoint and display candidate key types and
   SHA-256 fingerprints;
2. store a key only when the user submits an exact candidate fingerprint with
   explicit confirmation.

Pinned mode fails closed for missing, corrupt, changed, or unregistered keys.
Security-setting corruption also resolves to the stricter pinned policy.

Public interfaces are additive:

- CLI: `host-key-scan`, `host-key-pin`, and `host-key-list`;
- Dashboard: `GET/POST /api/nodes/{node_name}/ssh-host-key`;
- setting: `ssh_host_key_policy=trusted_lan|pinned`.

## 3. Result retention lifecycle

Terminal-run deletion remains recoverable and now has a complete lifecycle:

- list private trash and its deterministic content SHA-256;
- restore a run without overwriting an active run ID;
- preserve suite tombstone metadata so a restored run can rejoin its suite;
- permanently purge only after explicit confirmation and an exact fresh
  checksum match;
- prohibit permanent purge for formal or campaign-linked evidence.

The Dashboard exposes these operations through a dedicated **결과 휴지통**.
Permanent deletion requires typing the run ID and then passing the displayed
checksum to the server for revalidation. Protected entries show **보존 보호**
instead of an enabled delete control.

Additive APIs:

- `GET /api/results/trash`;
- `POST /api/results/trash/{trash_id}/restore`;
- `DELETE /api/results/trash/{trash_id}` with `confirmed=true` and the current
  64-character checksum.

No automatic purge task was introduced. The documented archive policy requires
an externally verified archive and checksum index before a user approves an
ordinary result's permanent deletion.

## 4. Network and RPC operating modes

The new operations guide distinguishes trusted LAN, pinned LAN, remote/VPN, and
offline publication/archive modes. Remote operation requires VPN-first network
isolation, token authentication, and TLS at a reverse proxy. Worker APIs and RPC
ports must not be publicly proxied.

llama.cpp RPC remains experimental and unauthenticated. It is allowed only on a
private VLAN or tunnel, starts for an experiment, and must be stopped on every
terminal path. The guide explicitly prohibits a persistent RPC boot service and
does not claim that Worker API tokens secure RPC traffic.

## 5. Packaging and deployment decision

Source checkout remains the supported operational mode for Controller launchers
and Worker native deployment. The existing wheel remains a tested importable
Controller/library artifact, not a replacement for Worker source manifests,
project-local virtual environments, CUDA/OpenBLAS builds, or pinned llama.cpp
RPC binaries.

A console entry point was reviewed but not added. The current operational
launcher depends on a verified source-root layout and runtime assets, so a
console entry point would imply a wheel-installed operating mode that is not yet
accepted. The supported global short command continues to be the symlink to the
source-checkout `scripts/llm-cluster` launcher.

## 6. Compatibility and safety

- Existing result directories and old trash entries require no destructive
  migration.
- Existing TOFU installations continue in `trusted_lan` mode until the user
  explicitly pins every Worker and changes policy.
- Global `~/.ssh/known_hosts` is not modified.
- Fingerprints must match a fresh scan of the registered private endpoint.
- Host-key policy applies uniformly to SSH and rsync command construction.
- Active results cannot be restored over, and active suites retain their
  existing deletion protection.
- Campaign/formal protection is enforced by the repository, not only hidden in
  the Dashboard.
- The Dashboard and Worker token defaults remain off for trusted-LAN
  compatibility; the guide requires them for remote use.
- No product code enables a public listener, automatic VPN, TLS, firewall,
  systemd unit, RPC persistence, or unattended result purging.

## 7. Verification

Focused gates cover:

- scan output parsing and SHA-256 fingerprint selection;
- exact-fingerprint confirmation and private atomic known-hosts replacement;
- strict SSH/rsync argv construction in pinned mode;
- corrupted-policy fail-closed behavior;
- host-key CLI/API compatibility;
- recoverable run deletion, trash listing, restore, collision refusal, suite
  tombstone repair, checksum mismatch refusal, and campaign purge protection;
- Dashboard result-trash structure and JavaScript syntax/export fixtures;
- existing Dashboard settings and security regression contracts.

The release checkpoint also runs the repository-wide Python, JavaScript,
packaging, CLI, shell-syntax, compilation, and whitespace gates. Hosted Required
CI remains the authoritative cross-platform gate.

Local release evidence:

- 466 Python regression tests passed;
- 46 focused Dashboard, SSH pinning, result-retention, and publication tests
  passed;
- 3 isolated wheel/package tests passed;
- 2 Chromium Dashboard E2E flows passed, including delete-to-trash and restore;
- the live local Dashboard result screen and empty-trash dialog were inspected;
- JavaScript syntax/export fixtures, publication PNG fixtures, repository
  validation, Python compilation, shell syntax, and whitespace checks passed.

The local Dashboard was started only for the bounded visual check and was
stopped immediately afterward. No Worker, model, RPC process, power mode, or
experiment was changed during Phase 13 verification.

## 8. Research-phase boundary

Phase 13 does not reinterpret incomplete pilot data as complete. Phase 09 v5
has finished the Jetson baseline/60/180/300-second cooldown calibration only:
4 of 28 preregistered runs. Raspberry Pi calibration and both variance cells
remain pending. Phase 10 formal campaign collection remains blocked until the
pilot reports `freeze_ready=true`.

## 9. References

- [Secure remote operation and result retention](../security/remote-operation-and-retention.md)
- [Phase 09 pilot status](phase-09-pilot-experiment.md)
- [Phase 12 compatibility split](phase-12-compatibility-facade-split.md)
