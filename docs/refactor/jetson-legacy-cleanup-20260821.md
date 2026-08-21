# Jetson legacy workspace cleanup — 2026-08-21

## Scope

This operation reconciled the three registered Jetson Workers with the current
Controller inventory and removed the explicitly approved legacy workspace on
`jetson-worker-01`. No model was downloaded and no Jetson power mode was
changed.

## Inventory and workspace reconciliation

The pre-change inventory was saved at:

`.run/cluster/backups/jetson-path-migration-20260821/nodes.local.csv.before`

The resulting registered project paths are:

| Worker | Host | Registered project path |
| --- | --- | --- |
| `jetson-worker-01` | `192.168.0.26` | `/home/jetson_orin_nano/project/llm/llm-cluster-benchmark-worker` |
| `jetson-worker-02` | `192.168.0.19` | `/home/jetson2/project/llm/llm-cluster-benchmark-worker` |
| `jetson-worker-03` | `192.168.0.6` | `/home/ho/project/llm/llm-cluster-benchmark-worker` |

For Workers 02 and 03, the Worker API was stopped before the existing project
directory was atomically moved from `local_llm_bench` to the registered path.
The API was then restarted from the new path. This preserved each node's local
virtual environment, models, and runtime state instead of reinstalling them.

The moved CMake RPC build caches contained absolute references to the old
directory. Only `.run/cluster/llama.cpp-rpc` was deleted on Workers 02 and 03,
after confirming that no RPC listener or recorded RPC process was active. The
pinned native RPC runtime was then rebuilt from the preserved source at commit
`f49e9178767d557a522618b16ce8694f9ddac628`.

## Permanent legacy deletion

The following exact directory on `jetson-worker-01` was permanently deleted
after verifying that it was not a symbolic link and no process used it as its
working directory:

`/home/jetson_orin_nano/project/llm/local_llm_bench`

The directory was approximately 24 GiB and included historical outputs,
runtime results, and legacy GGUF files. The deletion was explicitly authorized
and is not recoverable from the node. The pre-migration archive at
`/Users/hajoonpark/Documents/자율설계/migration-archives/jetson-legacy-20260820-phase15-predeploy`
preserves prior reconciliation evidence, source, and manifests; it should not
be treated as a byte-for-byte backup of every deleted GGUF file.

Post-operation checks confirmed that the legacy path is absent and the active
registered path remains present.

## Environment verification

Structured environment checks after migration reported:

- `jetson-worker-02`: READY, CUDA backend verified, jtop active, two local
  models, pinned RPC runtime verified, `MAXN_SUPER` observed.
- `jetson-worker-03`: READY, CUDA backend verified, jtop active, one local
  model, pinned RPC runtime verified, `15W` observed.
- No power setting was applied during this operation.

Final Controller status:

```text
NODE                 ROLE    SSH   PROJECT  API   MODEL
jetson-worker-01     worker  True  True     True  -
jetson-worker-02     worker  True  True     True  -
jetson-worker-03     worker  True  True     True  -
```

## Dashboard follow-up

The Dashboard now supports two explicit disconnect policies:

1. inventory-only disconnect, which is the default and preserves Worker files;
2. confirmed permanent deletion of the exact registered Worker project path,
   followed by inventory removal.

Remote cleanup is restricted to a validated Worker inventory path. It rejects
local nodes, legacy heads, broad paths, symbolic links, real-path mismatches,
and owner mismatches. It stops only the registered Worker API, deletes only the
exact validated directory, and verifies that the directory is absent before
removing the inventory row. A cleanup failure leaves the inventory unchanged.

The topology display is also deterministic: Jetson Workers are shown first,
then Raspberry Pi Workers, then unknown platforms; names use natural numeric
ordering within each group. Explicit experiment node order is not rewritten.

## Verification gates

- Python regression suite: `311/311` passed.
- Dashboard JavaScript syntax: passed.
- Dashboard export/DOM contract fixtures: passed.
- Live browser QA: Jetson 01/02/03 rendered before Pi 01/02/03/04; the
  disconnect dialog defaulted to file preservation and changed to an explicit
  permanent-deletion warning only after the checkbox was selected. No delete
  action was submitted during QA.
- `git diff --check`: passed.
- Live Jetson status check: all three registered Jetsons reported SSH,
  project, and Worker API online.

## Recovery limits

- The inventory can be restored from the recorded CSV backup if necessary.
- Workers 02 and 03 can be moved back only by an explicit, coordinated stop,
  filesystem move, inventory update, and restart.
- The deleted Worker 01 legacy directory cannot be restored from the node.
