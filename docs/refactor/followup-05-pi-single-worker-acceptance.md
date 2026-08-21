# FOLLOWUP 05 — Raspberry Pi Single-Worker Hardware Acceptance

Status: **COMPLETE**

This phase executed only the Raspberry Pi single-Worker acceptance scope. It did not begin multi-Worker or RPC acceptance, did not reboot or reconfigure either Raspberry Pi, and did not modify or delete the legacy Jetson workspace.

The first candidate, `pi-worker-01`, became unreachable during its bounded model smoke. At the user's request, acceptance restarted from the read-only baseline on the independently registered `pi-worker-02`; all final acceptance claims below apply to that Worker.

## Device identity

- Inventory node: `pi-worker-02`
- Address: private RFC1918 IPv4 `192.168.0.14`
- Role: enabled Worker; the Mac Controller was not a participant
- Hostname: `pi2`
- Board: Raspberry Pi 5 Model B Rev 1.0
- OS: Ubuntu 24.04.4 LTS
- Kernel/architecture: Linux 6.8.0-1060-raspi, `aarch64`
- CPU: 4 × Cortex-A76, up to 2.4 GHz
- RAM: 7,937.19 MiB reported by Worker health
- Storage: 23.29 GiB available at baseline
- Python: 3.12.3
- Deployment: inventory-managed isolated Worker project path; source sync excluded `.git`, `.venv`, `.run`, models, outputs, results, tokens, keys, and caches

The relevant listener baseline showed the Worker API on TCP 8000 and no RPC listeners on 50052 or 18080. Unrelated process details and credentials were not collected.

## Git/deployment commit

- Branch: `codex/mac-control-plane`
- Follow-up 04 baseline: `edffe7ea8f14d8e68af2badfac7a3ae3fdeccc88`
- Deployment/correction source: `727fae03dcb2fdca074044a06ba090daae999926`
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
- Inference threads: 4
- CUDA requirement: none
- Jetson/jtop requirement: none
- Model inventory after sync: one GGUF

Because the existing environment was already ready, `environment-install`, package installation, and virtual-environment recreation were intentionally not run.

## Power baseline

- Probe source: fixed `vcgencmd get_throttled`
- Command availability: yes
- Raw value: `0` / `0x0`
- Current undervoltage/frequency cap/throttling/soft-temperature limit: all false
- Historical undervoltage/frequency cap/throttling/soft-temperature limit: all false
- Status: `ok`
- Blocking: false

Power integrity remained a separate observation axis and did not alter inference or telemetry readiness.

## Worker API

The repository lifecycle completed on `pi-worker-02`:

- existing Worker stopped cleanly before loading the deployed source
- `start`: PID 50480, TCP 8000 online
- second `start`: idempotent; PID 50480 retained
- `restart`: new PID 50728 observed
- `status`/health: online
- `stop`: PID 50728 and TCP 8000 listener removed
- benchmark preparation `start`: PID 51042, guarded listener online

Health reported:

- `inference_ready=true`
- `telemetry_ready=true`
- `telemetry_degraded=false`
- OpenBLAS backend verified
- `power_integrity.status=ok`
- `power_integrity.blocking=false`

The final cleanup check reconfirmed that PID 51042 owned TCP 8000. No 50052 or 18080 listener was present.

## Model identity/checksum

- ID/file: `llama3.2-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf`
- Type: text instruct GGUF
- Quantization: Q4_K_M
- Size: 807,694,464 bytes
- SHA-256: `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`
- Worker inventory: present, `checksum_valid=true`
- Worker verification endpoint: exact SHA-256 pass
- Partial download artifacts after acceptance: none

The model transfer occurred before benchmark timing. A separate load/inference/unload smoke used context 512, zero GPU layers, seed 42, and 16 generated tokens. It completed in 2.359451 seconds with 16 streamed tokens and unloaded successfully.

## Single-node config

- strategy: `single_node`
- participant: `pi-worker-02` only
- logical/physical requests: 2 / 2
- concurrency: 1
- warmup requests: 1
- max generated tokens: 24
- context: 512
- GPU layers: 0
- temperature/top-p: 0.0 / 0.9
- seed: 42
- prompt persistence: enabled
- run ID: `20260821_140452_69cf30`

The warmup was excluded from measurement, and the Mac Controller received no physical inference call.

## Functional result

**Functional Acceptance: PASS**

- Run status: `completed`
- Requests: 2
- Successful/failed: 2 / 0
- Responses: 2
- Success rate: 100%
- Generated tokens: 48
- Structured failures: none
- Deterministic response hashes: identical across both requests

Metrics:

- TTFT p50/p95: 0.179275 / 0.217554 seconds
- E2E p50/p95: 2.769557 / 2.826853 seconds
- Cluster throughput: 8.654476 tokens/second
- Average generation throughput: 9.265822 tokens/second
- Measured wall time: 5.546263 seconds

## Measurement quality

- Final quality: `clean`
- Preflight: `0x0`, status `ok`
- Pre-measurement: `0x0`, status `ok`
- Measurement: 1/1 valid sample, zero unavailable samples, zero active-warning samples
- Postflight: `0x0`, status `ok`
- Warnings: none
- Power blocking policy: false

The earlier `pi-worker-01` outage is retained as a separate hardware risk and is not attributed to power without evidence.

## Durable artifacts

The run directory contains private-mode artifacts:

- `config.json`
- `events.jsonl` — 14 records
- `requests.csv` — exact 19-column contract, two data rows
- `responses.jsonl` — two response records
- `summary.json` — schema version 2

Prompt text, raw generated responses, full SHA-256 response hashes, per-request TTFT/E2E/TPS, additive power snapshots, and final measurement quality were all persisted. No benchmark download log appeared in the experiment event stream.

## Dashboard validation

The Mac Dashboard was restarted once so the already-pushed power-observability backend was loaded. This was deployment refresh, not a source fix.

Browser acceptance confirmed:

- `pi-worker-02`: ONLINE and INFERENCE READY
- Worker card: `POWER NORMAL` rather than UNKNOWN
- experiment: `FOLLOWUP 05 Raspberry Pi 2 single-worker acceptance · COMPLETED`
- summary cards: 8.7 tok/s, TTFT p50 0.18s, E2E p95 2.83s, 100% success
- interactive throughput, latency, and per-node charts rendered
- Measurement Environment: NORMAL with before/during/after details
- both prompts and raw responses displayed
- response hash prefix displayed and matched persisted SHA-256
- per-response TTFT, E2E, generated-token count, and throughput displayed

## Cleanup

- Selected model unloaded; final health reported no loaded model.
- Worker API remains running under the guarded repository lifecycle policy.
- PID 51042 owns TCP 8000.
- No RPC process or listener was started.
- No partial model file remained.
- The accepted model was not deleted.
- The legacy Jetson workspace was not modified or deleted.
- No Raspberry Pi was rebooted, power-cycled, reconfigured, overclocked, or underclocked.

## Defects/fixes

The original `pi-worker-01` attempt exposed a macOS portability defect before its hardware outage: model synchronization used GNU-rsync-only `--append-verify` and `--info=progress2`, which macOS built-in rsync rejected before transfer.

The Mac source-of-truth fix:

- replaced those flags with portable `--partial --progress`
- retained mandatory remote SHA-256 verification
- retained model path containment and checksum-mismatch cleanup
- added a regression test that rejects reintroduction of the GNU-only flags

Correction commit: `727fae03dcb2fdca074044a06ba090daae999926` (`fix(models): support verified sync from macOS`). It was tested, pushed, redeployed, and used successfully for the full `pi-worker-02` transfer.

No additional product defect was found on `pi-worker-02`. The initial Dashboard `POWER UNKNOWN` observation was resolved by restarting a stale pre-feature Dashboard process; the current source correctly renders `POWER NORMAL`.

## Remaining hardware risks

- `pi-worker-01` remains unstable/unreachable after its short inference attempt; its root cause is unknown.
- This acceptance proves one Raspberry Pi Worker only. Multi-Worker and RPC acceptance are explicitly outside FOLLOWUP 05 and were not started.
- The smoke workload is intentionally small and does not establish sustained thermal, power, storage, or long-duration stability.
- The Raspberry Pi reports no swap and has 23.29 GiB free storage; larger models require a separate fit/preflight decision.

## Tests and gates

- Focused portable model-sync regression: pass
- Relevant model tests: 37/37 pass
- Full Mac regression before this acceptance: 279/279 pass
- Full Mac regression after this acceptance: 279/279 pass
- `git diff --check`: pass
- Hardware commands were limited to the inventory-selected Raspberry Pi Worker and approved repository lifecycle/model flows.

FOLLOWUP 05 is complete. FOLLOWUP 06 has not been started.
