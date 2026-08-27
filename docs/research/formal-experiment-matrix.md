# Formal Experiment Matrix v1

Status: Phase 09 v5 decision frozen; not executable until current-source pilot
revalidation and runtime/source re-lock are complete

## Locked inputs

The matrix is bound to `formal-study-v1` fingerprint:

```text
3d5c7f2429c42d02db60ab635a263db6edd86fb4527078eb8d5ff80fc30744a5
```

Only the two `approved` model entries are active:

- Qwen2.5 1.5B Instruct Q4_K_M;
- Granite 3.3 2B Instruct Q4_K_M.

Every cell uses prompt-set version 1 and one of its four exact prompt IDs. The
Controller is orchestration-only and is never a participant.

## Active cell groups

One base cell is one platform cohort, strategy, exact node set, model, and
prompt combination before the independent repeat index is applied.

| Cell group | Strategy | Exact node sets | Models | Prompts | Base cells |
|---|---|---|---:|---:|---:|
| Jetson 01 baseline | single_node | Jetson 01 | 2 | 4 | 8 |
| Jetson 02 baseline | single_node | Jetson 02 | 2 | 4 | 8 |
| Jetson 03 baseline | single_node | Jetson 03 | 2 | 4 | 8 |
| Pi single-node variance | single_node | Pi 02, Pi 03, Pi 04 separately | 2 | 4 | 24 |
| Pi request-distribution scaling | replicated_round_robin | Pi 02+03; Pi 02+03+04 | 2 | 4 | 16 |
| Pi response agreement | broadcast_compare | Pi 02+03+04 | 2 | 4 | 8 |
| **Total** |  |  |  |  | **72** |

The single Pi 02 cell is the declared one-node baseline for the 2- and 3-node
round-robin scaling contrast. Explicit node-set order is preserved.

## Why Jetson multi-node cells are absent

Runtime lock v3 defines each Jetson as a separate formal cohort:

- Jetson 01: R36.4.7 and MAXN_SUPER;
- Jetson 02: R36.5.0 and MAXN_SUPER;
- Jetson 03: R36.5.0 and 15W.

No two Jetsons currently form one homogeneous formal cohort. Combining them in
a formal multi-node cell would violate the runtime lock. Existing interoperability
smoke evidence remains valid, but it is not publication-comparable performance
evidence.

## Questions and claim boundaries

| Research question | Matrix v1 status | Permitted interpretation |
|---|---|---|
| Jetson vs Pi single-node | active | Descriptive comparison between locked hardware/runtime cohorts |
| Same-platform node variance | active | Pi variance; Jetson results remain cohort-specific |
| Round-robin node scaling | active | Homogeneous Pi 1/2/3-node scaling |
| Broadcast agreement | active | Pi replica success and exact output-hash agreement |
| Homogeneous vs heterogeneous | exploratory only | No formal heterogeneous estimate |
| Model size × platform | blocked | The approved models also differ in family, so size is not isolated |
| RPC performance/network cost | blocked | The locked policy needs an explicit Jetson coordinator and at least two members in one formal cohort |
| Power/thermal effect | descriptive only | Jetson 02 and 03 are different physical devices; this is not a randomized power-mode intervention |

Inefficient or exploratory strategies are not discarded. They remain explicit
deferred questions with unlock conditions in the machine-readable matrix.

## Workload size

The repeat count is not guessed. Phase 09 v5 selected 15 from the preregistered
10–30 range using pilot run-level variance and the predeclared CI half-width
target. The selected volume is 1,080 runs, 21,600 logical requests, and 26,400
physical requests.

| Quantity | Per full matrix repeat | 10 repeats | 30 repeats |
|---|---:|---:|---:|
| Formal runs | 72 | 720 | 2,160 |
| Logical measured requests | 1,440 | 14,400 | 43,200 |
| Physical measured requests | 1,760 | 17,600 | 52,800 |
| Warmup requests | 112 | 1,120 | 3,360 |
| Model loads/unloads | 72 / 72 | 720 / 720 | 2,160 / 2,160 |

Broadcast accounts for 20 logical requests but 60 physical replica calls per
run. It must never be reported as 60 independent user requests.

## Runtime and storage planning

The five-minute successful-run value remains a conservative matrix planning
assumption; Phase 09 v5 observed a 511.77-second median successful request run,
and the frozen 180-second campaign cooldown is accounted separately. At the
selected 15 repeats, base run time alone is approximately 90 hours. The
request-timeout envelope is deliberately much larger and is a failure envelope,
not an expected duration.

The raw-response allowance is 16 KiB per physical request plus 64 KiB per run.
This yields 320 MiB at 10 repeats and 960 MiB at 30 repeats before telemetry,
figures, and archives. The Controller must reserve at least 5 GiB.

## Machine-readable authority

`config/research/formal_experiment_matrix.json` is the authority for cell
templates and expected volume. `cluster.research.matrix` expands and validates
it without reading files or executing a benchmark. A future concrete campaign
adds `repeat_index` and order through `campaign_manifest.schema.json`.
