# Source-of-Truth Reconciliation

> Retirement note: after the Controller/Worker migration and hardware acceptance
> completed, the user explicitly retired the legacy top-level benchmark client,
> Jetson notebook/scripts, standalone chat server, and committed historical
> output from the current product tree. Their exact contents and commit lineage
> remain reachable in Git history; no history rewrite or squash was performed.
> Local model binaries and `.run` experiment state were not part of this cleanup.

Date: 2026-08-20 (Asia/Seoul)

This record closes the Phase 00 source-of-truth blocker before Phase 01. The
Jetson workspace was treated as a read-only migration source; no file, Git ref,
process, service, or runtime state on the device was changed.

## Authoritative development repository

| Item | Value |
|---|---|
| Mac repository | `/Users/hajoonpark/Documents/자율설계/llm-cluster-benchmark` |
| GitHub remote | `https://github.com/Phjrab/llm-cluster-benchmark.git` |
| Work branch | `codex/mac-control-plane` |
| Initial target commit | `2bcbbe41906b048f397c17e6e1e16a99b6cebeaa` |
| Imported legacy main | `98d88c2583ebb301deb6887bd865eef3de66484e` |
| Imported cluster-only branch | `b17a346138f683294804623a27ad7468aabc1f36` |

The target and legacy repositories had unrelated histories. Commit `2014dc9`
joins them with an explicit merge, retaining the target benchmark client and
experiment plan as well as the complete committed legacy tree and benchmark
artifacts. Commit `c2d2476` then records the current non-output Jetson working
source, including its two previously untracked plotting tools.

The `legacy-jetson` remote is configured with a disabled push URL. It exists to
retain read-only provenance, not as a deployment destination.

## Recovery archive

The durable local archive is outside the Git working tree:

```text
/Users/hajoonpark/Documents/자율설계/migration-archives/
└── jetson-legacy-20260820-phase01/
    ├── git/
    │   ├── legacy-all-branches.bundle
    │   ├── legacy-committed.bundle
    │   ├── working-tree.patch
    │   ├── status.txt
    │   ├── history.txt
    │   └── repository-manifest.txt
    ├── source/working-tree/
    ├── artifacts/
    │   ├── outputs/
    │   └── runtime-cluster/
    └── manifests/
        ├── models.sha256
        └── archive.sha256
```

Archive facts at creation:

- 160 files, approximately 12 MiB (model binaries intentionally excluded)
- complete Git bundles for legacy `main`, `cluster-split`, and
  `llm-cluster-benchmark-main`
- binary patch for every tracked Jetson working-tree change
- exact non-runtime source snapshot, including untracked source files
- current `outputs/`, experiment definitions, suite/run results, environment
  reports, backups, and bounded runtime logs
- SHA-256 identities for all 9 GGUF model files (about 20 GiB total)
- SHA-256 manifest for every file in the recovery archive

The token files `dashboard.token` and `worker.token`, model binaries, virtual
environment, and large llama.cpp source/build directories were not copied.
Model identity is preserved by `models.sha256`; the binaries remain on the
Jetson. No credential or SSH private key is stored in the archive.

## Reconciliation policy

The Jetson status contained modified source and output files, deleted historical
plots/tables, and two untracked plot generators. The reconciliation applies the
following explicit policy:

1. Import all committed legacy history and committed result artifacts.
2. Import current non-output source and tests exactly.
3. Keep the committed historical result files instead of replaying Jetson output
   deletions, because the MASTER SPEC forbids deleting existing result evidence.
4. Preserve the Jetson's current output tree separately in the archive, including
   its modified CSV files.
5. Keep the original target `EXPERIMENT_PLAN.md` and `benchmark/` client beside
   the legacy `bench/` and `cluster/` implementations.
6. Do not deploy the reconciled source back to the Jetson during refactoring.

An rsync checksum comparison after import found no content difference between
the archived non-output Jetson source and the reconciled tree, except for the
intentionally combined `.gitignore`. Target-only files are additive.

## Recovery checks

```bash
git bundle verify \
  /Users/hajoonpark/Documents/자율설계/migration-archives/jetson-legacy-20260820-phase01/git/legacy-all-branches.bundle

sha256sum -c \
  /Users/hajoonpark/Documents/자율설계/migration-archives/jetson-legacy-20260820-phase01/manifests/models.sha256
```

The model checksum command must be run from the Jetson project root, because the
manifest intentionally stores model-relative paths.

## Baseline regression after import

- Python: 52 tests passed, 13 platform/dependency tests skipped
- JavaScript syntax and dashboard export fixtures: passed
- Bash syntax: 13 scripts passed
- Jetson live inference/RPC/telemetry: not run; this reconciliation performs no
  hardware or service mutation

The Mac repository plus its GitHub remote is now the development source of
truth. The legacy Jetson workspace remains intact until the MASTER SPEC hardware
acceptance gate permits retirement.
