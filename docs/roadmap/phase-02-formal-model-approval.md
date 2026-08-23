# Roadmap Phase 02 — Formal Model Approval

Date: 2026-08-23 (Asia/Seoul)

Status: COMPLETE

Scope: Roadmap Phase 02 only; Phase 03 hardware/runtime condition re-lock was not started

## 1. Outcome

Two official, cross-platform model families were promoted through
`source_locked → worker_verified → approved`:

| Model | Official revision | Exact GGUF SHA-256 | Result |
|---|---|---|---|
| Qwen2.5 1.5B Instruct Q4_K_M | `91cad51170dc346986eccefdc2dd33a9da36ead9` | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` | APPROVED |
| Granite 3.3 2B Instruct Q4_K_M | `7cdf86ccd1f1bb3491c9b7017b033f2e51367397` | `ac71e9e32c0bea919b409c5918f69ca74339854b0319c5065e4e9fb6d95c4852` | APPROVED |

Qwen2.5 3B and Granite 3.3 8B remain `source_locked`. No name-matched
community binary was substituted, and no model was downloaded during a
benchmark run.

## 2. Git and deployment checkpoint

| Item | Value |
|---|---|
| Feature branch | `codex/roadmap-phase-02` |
| GGUF identity implementation commit | `2a46dc8edcb43e2995209b0b77382325105ce0e8` |
| Live Worker source tree | `483a33e0d5f3f1348667f8d2663387d63cd6bc27e7691e9af66cc1127845fa78` |
| Live source files | 157 |
| Native RPC pin | `f49e9178767d557a522618b16ce8694f9ddac628` |
| `llama-cpp-python` | `0.3.20` on all six Workers |

The implementation checkpoint was pushed before deployment. All six Workers
were synchronized and restarted, then reported the same source commit/tree and
`deployment.verified=true`. The later lock/report commit intentionally does not
replace the runtime baseline: the exact deployed implementation remains the
research source identity.

## 3. Formal GGUF identity contract

`cluster.infrastructure.gguf` now parses bounded GGUF v2/v3 metadata without
loading tensors or depending on `llama-cpp-python`. It rejects malformed,
truncated, duplicate-key, unsupported, and oversized metadata.

The `gguf-metadata-v1` contract records:

- `general.architecture`;
- a canonical SHA-256 over `tokenizer.chat_template` keys;
- a separate canonical SHA-256 over all other `tokenizer.*` keys;
- exact contributing key lists and metadata entry count.

Worker installation computes this identity from the downloaded `.part` file
before atomic promotion. Caller-supplied architecture/hash claims are treated
as expectations and fail closed on mismatch. A verified private sidecar is
written with mode `0600`; checksums remain mandatory.

## 4. Observed metadata

| Field | Qwen2.5 1.5B | Granite 3.3 2B |
|---|---|---|
| Architecture | `qwen2` | `granite` |
| Exact bytes | 1,117,320,736 | 1,545,303,328 |
| Chat-template SHA-256 | `24f79a69401549da90b4da8477caf9cab6fc0a58929b81767003df310a513e33` | `e1ef5217871cc6891e09ea2a9f01466ce6b1cd6cdcb1d22bfe91fe303cea4e01` |
| Tokenizer-metadata SHA-256 | `db18a59f5f900ded3a1973ad90dabce36a3ef6df8b833779b0fa3bc74f0a5547` | `27ef0bcce8cf124bb2a434acc68297a8f1f38748efc94f3ef64ba5c48b99d074` |
| Chat-template keys | 1 | 1 |
| Tokenizer keys | 9 | 11 |
| Total metadata entries | 26 | 40 |
| License | Apache-2.0; accepted | Apache-2.0; accepted |

Every value above was identical on all six Workers.

## 5. Controlled installation

Each Worker downloaded from an immutable Hugging Face `resolve/<commit>/...`
URL before any experiment was started. The Worker streamed SHA-256 while
receiving the file, inspected GGUF metadata before promotion, fsynced the
temporary file, and atomically replaced the final path only after all checks
passed.

The canonical Qwen catalog ID now preserves the exact official lowercase
basename. The older mismatching uppercase-path file on `jetson-worker-02`
remains isolated and rejected for formal use; no legacy or community model was
deleted.

## 6. Six-Worker checksum and inventory gate

| Worker | Platform | Qwen | Granite | Final state |
|---|---|---|---|---|
| `jetson-worker-01` | Jetson CUDA | PASS | PASS | unloaded |
| `jetson-worker-02` | Jetson CUDA | PASS | PASS | unloaded |
| `jetson-worker-03` | Jetson CUDA | PASS | PASS | unloaded |
| `pi-worker-02` | Raspberry Pi OpenBLAS | PASS | PASS | unloaded |
| `pi-worker-03` | Raspberry Pi OpenBLAS | PASS | PASS | unloaded |
| `pi-worker-04` | Raspberry Pi OpenBLAS | PASS | PASS | unloaded |

The final independent inventory read confirmed both expected models, exact
size/SHA, inspected metadata, unloaded state, and verified deployment identity
on 6/6 Workers.

## 7. Platform smoke gate

Twelve model/Worker combinations were executed. Each combination performed:

1. an independent expected-SHA verify;
2. model load with `n_ctx=4096`;
3. deterministic seed-42 streaming generation with at least one token;
4. explicit unload and unloaded-state verification.

Actual load contract:

| Cohort | Workers | Actual context | Actual GPU layers | Result |
|---|---:|---:|---:|---|
| Jetson CUDA | 3 | 4096 | 30 | 6/6 model-node smokes PASS |
| Raspberry Pi OpenBLAS | 3 | 4096 | 0 | 6/6 model-node smokes PASS |

Smoke timing was observed only to prove runtime execution and is not a formal
performance result. The first harness run parsed the existing SSE `type` field
as `event`; that harness-only error produced no model failure and unloaded all
models. The corrected contract run passed 12/12.

## 8. Research lock version 2

The four lock files share the new canonical fingerprint:

```text
a4ea4400841b849b87cfe7813ebd963e4d76532783a08167768fe08636469f20
```

Changes are limited to the new source baseline/deployment evidence and formal
model approval facts. Prompt text and experiment parameters were not changed.
The runtime lock now records verified deployment manifests instead of the old
`.git`-absence blocker.

## 9. Test gates

Implementation checkpoint gates:

- focused GGUF/model/domain/research tests: PASS;
- full suite: 351 non-socket tests PASS; two sandbox-denied launcher socket
  tests passed in the permitted local-port environment;
- dedicated launcher suite: 6/6 PASS;
- Python compile and `git diff --check`: PASS.

Final lock/report regression gate:

- full Python suite: 353/353 PASS in 50.520 seconds;
- dashboard JavaScript syntax/export fixtures: PASS;
- Python compile, shell syntax, research-lock validation, and
  `git diff --check`: PASS.

## 10. Compatibility and safety

- Existing API routes and CLI commands are preserved; install metadata flags
  are additive.
- Existing result schema and 19-column request CSV are unchanged.
- Model binaries, `.run`, tokens, keys, and private metadata are not committed.
- No `latest` revision was used.
- No checksum-free READY path was added.
- The Controller never becomes an inference participant.

## 11. Remaining blockers and stop boundary

Phase 02 satisfies the roadmap completion condition with two approved
cross-platform, cross-family models. The campaign as a whole is still not ready
for homogeneous Jetson publication runs because:

1. `jetson-worker-03` reports 15W while Workers 01/02 report MAXN_SUPER;
2. Jetson 01 reports L4T R36.4.7 while Workers 02/03 report R36.5.0.

These are explicitly Roadmap Phase 03 concerns. No power-mode change, reboot,
L4T update, cohort split, or Phase 03 implementation was performed here.
