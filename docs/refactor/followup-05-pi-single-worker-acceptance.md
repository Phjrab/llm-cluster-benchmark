# FOLLOWUP 05 — Raspberry Pi Single-Worker Hardware Acceptance

Status: **BLOCKED_BY_HARDWARE**

This phase executed only the Raspberry Pi single-Worker acceptance scope. It did not begin multi-Worker or RPC acceptance, did not reboot or reconfigure the Raspberry Pi, and did not modify or delete the legacy Jetson workspace.

## Device identity

- Inventory node: `pi-worker-01`
- Address: private RFC1918 IPv4 `192.168.0.16`
- Role: enabled Worker; the Controller was not a benchmark participant
- Hostname: `pi1`
- Board: Raspberry Pi 5 Model B Rev 1.0
- OS: Ubuntu 24.04.4 LTS
- Kernel/architecture: Linux 6.8.0-1060-raspi, `aarch64`
- CPU: 4 × Cortex-A76, up to 2.4 GHz
- RAM: 8,322,748,416 bytes total; 7,880,642,560 bytes available at baseline
- Storage: approximately 214 GiB available at baseline
- Python: 3.12.3
- Deployment: inventory-managed isolated Worker project path; source sync excluded `.git`, `.venv`, `.run`, models, outputs, results, tokens, keys, and caches

The pre-change listener baseline showed the Worker API on TCP 8000 and no RPC listeners on 50052 or 18080. Unrelated process details and credentials were not collected.

## Git/deployment commit

- Branch: `codex/mac-control-plane`
- Follow-up 04 baseline: `edffe7ea8f14d8e68af2badfac7a3ae3fdeccc88`
- Deployed correction: `727fae03dcb2fdca074044a06ba090daae999926`
- Source flow: clean Mac feature branch → regression tests → commit/push → repository `sync-code` → isolated Worker path
- The Worker source was never edited directly.

## Environment/backend

`environment-check` completed without installing or recreating anything:

- Result: `READY`
- Linux/aarch64 and Raspberry Pi 5 detection: pass
- Project-local virtual environment: pass
- Exact pinned Python runtime requirements: pass
- `llama-cpp-python` 0.3.20: pass
- OpenBLAS backend: verified
- CUDA requirement: none
- Jetson/jtop requirement: none
- Model inventory after sync: one GGUF

Because the existing environment was already ready, `environment-install`, package installation, and virtual-environment recreation were intentionally not run.

## Power baseline

- Probe source: fixed `vcgencmd get_throttled`
- Command availability: yes
- Raw value: `327680` / `0x50000`
- Current undervoltage: false
- Current frequency capped: false
- Current throttled: false
- Current soft-temperature limit: false
- Historical undervoltage: true
- Historical throttling: true
- Status: `history_warning`
- Blocking: false

The Worker health contract correctly kept inference and telemetry ready while reporting this non-blocking historical warning. No adapter type or cause was inferred.

## Worker API

Before the model smoke, the repository lifecycle completed:

- `start`: running
- second `start`: idempotent; same PID
- `restart`: new PID observed
- `status`/health: online
- `stop`: Worker PID and TCP 8000 listener removed
- benchmark preparation `start`: online with an exact guarded PID/listener

Health reported:

- `inference_ready=true`
- `telemetry_ready=true`
- `telemetry_degraded=false`
- OpenBLAS backend verified
- inference threads: 4
- `power_integrity.status=history_warning`
- `power_integrity.blocking=false`

## Model identity/checksum

- ID/file: `llama3.2-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf`
- Type: text instruct GGUF
- Quantization: Q4_K_M
- Size: 807,694,464 bytes
- SHA-256: `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`
- Worker inventory: present, `checksum_valid=true`
- Worker verification endpoint: exact SHA-256 pass
- Partial download artifacts: none observed before the hardware loss

The model was copied from an already accepted, checksum-verified source without modifying that source workspace. The Controller-to-Worker transfer completed before benchmark timing.

## Single-node config

The planned acceptance configuration was:

- strategy: `single_node`
- participant: `pi-worker-01` only
- requests: 2
- concurrency: 1
- warmup requests: 1
- max generated tokens: 24
- context: 512
- GPU layers: 0
- temperature: 0.0
- seed: 42
- prompt persistence: enabled

The model-load smoke returned `OK` with the requested model, context 512, and zero GPU layers. A bounded 24-token inference was then submitted.

## Functional result

**Functional Acceptance: INCOMPLETE — BLOCKED_BY_HARDWARE**

The model load succeeded, but the Raspberry Pi disappeared from the network while the short inference was in progress. Afterward:

- Worker API: unreachable
- SSH: `Host is down`
- ICMP: no responses
- ARP: prior device entry remained visible

This was a device-level availability loss, not merely a Worker API failure. No response completion was observed, so the benchmark was not started and no successful inference claim is made. The recorded historical power warning is not treated as a blocker or asserted as the cause of the outage.

## Measurement quality

- Preflight quality: `warning`
- Preflight power raw: `0x50000`
- Benchmark quality: `unknown` because no measurement run was created
- Run status: not created
- Power blocking policy: false

No completed, degraded, failed, or partial benchmark result was synthesized.

## Durable artifacts

No benchmark run directory was created because the prerequisite load/inference smoke did not finish. Therefore this phase has no new:

- `config.json`
- `events.jsonl`
- `requests.csv`
- `responses.jsonl`
- `summary.json`

This is intentional: incomplete hardware activity was not represented as a completed durable run.

## Dashboard validation

Before the outage, the Worker API payload required by the Dashboard was verified: the Raspberry Pi remained inference-ready and telemetry-ready while `0x50000` was represented as a non-blocking historical warning.

The completed-result Dashboard acceptance could not be performed because no benchmark result exists. In particular, completed status, Measurement Environment, prompt/response, response hash, and TTFT/E2E/TPS remain unverified in this hardware phase.

## Cleanup

- No RPC process or listener was started.
- No model download remained partial before device loss.
- No temporary Controller benchmark server was started.
- The model was not deleted.
- The legacy Jetson workspace was not modified or deleted.
- Model unload and final Worker PID/listener ownership could not be confirmed after the device became unreachable.

The Raspberry Pi was not rebooted, power-cycled, reconfigured, overclocked, underclocked, or otherwise mutated in an attempt to recover it.

## Defects/fixes

Hardware discovery exposed a macOS portability defect in model synchronization: the command used GNU-rsync-only `--append-verify` and `--info=progress2`, which macOS built-in rsync rejected before transfer.

The Mac source-of-truth fix:

- replaced those flags with portable `--partial --progress`
- retained mandatory remote SHA-256 verification
- retained model path containment and mismatch cleanup
- added a regression test that rejects reintroduction of the GNU-only flags

Correction commit: `727fae03dcb2fdca074044a06ba090daae999926` (`fix(models): support verified sync from macOS`). The correction was tested, pushed, redeployed, and the full model transfer/checksum then succeeded.

## Remaining hardware risks

- The Raspberry Pi must return to stable SSH/API reachability before inference acceptance resumes.
- The short inference, unload, single-node benchmark, durable artifact contract, and Dashboard result view remain unverified.
- The observed outage coincided with inference but its cause is unknown; the report does not attribute it to power integrity without evidence.
- On resumption, verify the guarded Worker PID/listener and current model state before retrying. Do not assume the prior process or model lock survived.
- Multi-Worker and RPC acceptance are explicitly outside this phase and were not started.

## Tests and gates

- Focused portable model-sync regression: pass
- Relevant model tests: 37/37 pass
- Full Mac regression after the correction and hardware attempt: 279/279 pass
- `git diff --check`: pass
- Hardware-safe read-only and approved lifecycle/model commands: executed only against the inventory-selected Raspberry Pi Worker

FOLLOWUP 05 stopped at the hardware availability blocker. FOLLOWUP 06 has not been started.
