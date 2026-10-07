# WS-UI Research Lab — feature preservation inventory

Repository: `Phjrab/llm-cluster-benchmark`. Audited checkout: `9245e24`, clean `main` before work. Feature branch: `codex/ui-llm-cluster-research-lab`. No applicable `AGENTS.md` was found in this repository or its parents. Existing CONTRIBUTING and SECURITY boundaries apply.

The requested pack was located at the supplied attachment directory `../prompt/dashboard-ui-redesign/llm-cluster-ui-prompt-pack/`, rather than the repository root. START_HERE, MASTER_PROMPT, 01–06, references and acceptance checklist were read there. They are design input for the user's authorized UI task; their deployment suggestions do not authorize hardware operations. The existing framework remains FastAPI/Jinja + vanilla JS/CSS.

| Existing capability | Route / DOM / events | API / state | Research Lab placement | Regression evidence |
|---|---|---|---|---|
| Overview and Controller role | `#overview`, summary IDs | `/api/bootstrap`, `/api/controller/status`, shared `state` | Saved campaign Matrix; independent Controller management panel; Worker inventory | `research-lab.spec.js` Matrix + no-write checks |
| Nodes, platform selection, registration, disconnect | `#nodes`, `nodeGrid`, `data-node-*`, dialogs | `/api/nodes/*`, `selectedNodes`, `detailNode`, platform tab | Compact inventory and existing native telemetry dialog | Existing core/disconnect/detail tests; offline/long-name test |
| Environment/SSH/power management | Nodes workbench and settings dialogs, `data-cluster-action` | Existing management APIs and token header | Same selectors and controls, neutral surfaces | Existing mocked tests; no live operations |
| Model search, filters, packs, download/install controls | `#models`, `modelLibrary`, `libraryModelSearch` | `selectedModels`, catalog and recommendations | Existing searchable library and installation evidence | Existing packs/vendor/pagination tests; escaped long identity |
| Experiment design and durable execution | `#experiment`, `experimentForm`, strategy radios | `/api/experiments`, job events, `activeExperiment` | Existing strategy/configuration/run controls, single pending submission | Navigation/history/input test; mock duplicate-submit test |
| Sweep axes / RPC / preview approval | `#sweeps`, `sweepBuilderForm`, preview/save/start IDs | `/api/sweeps/*`, stable Worker IDs, preview hash | Existing builder, light preview blockers and durable control | Existing 108-trial test, hash invalidation, RPC, concurrency gates |
| Results, raw responses, failure attempts | `#results`, `runsTable`, inspector | `/api/experiments`, `/api/runs/*`, response policies | Existing run table/inspector and graphs | Existing responses/trash/export fixtures and Python result tests |
| Durable campaign status/control/retry | `#campaign`, `campaignList`, `campaignDetail` | `/api/campaigns/*`, campaign manifest | Same control table plus full saved-plan Matrix | Matrix inspection; gate disabled; existing Python campaign tests |
| Cross-run compare/export | `#compare`, all `data-compare-filter`, baseline/metric IDs | `/api/research/compare`, research state, chartModels | Condition rail + explicit baseline/controlled axes + one plot + linked table | New zero/mismatch unit suite, browser baseline/filter/CSV tests |
| Readiness/formal gate | `#research`, eligibility/worker/lock/blocker IDs | `/api/research/readiness` | Independent source/runtime/power/instrumentation and formal blockers | Browser blocked gate; existing lock/readiness tests |
| Export, trash/restore/protected purge | Results trash dialog, existing chart/publication controls | Existing payloads, checksums and retention protection | Same controls, native dialog and formats | Existing restore/delete test; protected-purge browser test; publication PNG fixtures |
| Navigation and auth/events | Nine hashes, mobile select, back/forward, focus title | Single shared store, mounted DOM, token header, authenticated events | Same route contract and mounted feature DOM | Existing all-page / history / deep-link / keyboard tests |

## Metric and status contracts

| Display | Source / rule |
|---|---|
| Physical throughput | Existing `cluster_tokens_per_s` / explicit `physical_cluster_tokens_per_s`; broadcast counts all replica output |
| Effective user throughput | `effective_user_tokens_per_s`; never inferred from physical output |
| Logical request throughput | `logical_requests_per_s`; not confused with physical calls |
| Energy efficiency | Existing instrumentation `overall.generated_tokens_per_j` only with complete energy coverage; missing remains null |
| Success | `success_rate × 100`; valid zero preserved; baseline difference in pp |
| Execution / quality | Separate `status` and `measurement_quality`; failed/cancelled remain in tables |
| Provenance / phase / gate | Separate saved-artifact provenance, `experiment_type`, and server `formal_execution_allowed` |

Server enum confirmed in `cluster/domain/strategy.py`, SweepSpec validation and template: `single_node`, `replicated_round_robin`, `broadcast_compare`, `node_sweep`, `model_parallel_rpc`. The repository's formal gate remains false; 1,080 is planned runs, not completed research.

## Dangerous action boundary

SSH discovery/install/sync/start/stop, model download, inference, sweep/campaign start/resume/retry and power writes remain real product controls, but all browser validation uses synthetic handlers. The capture script serves only the existing template/static files: it never imports the Controller or JobService. It intercepts every API; only synthetic plan preview POST is allowed, with all execution and management writes denied. Remote browser origins are aborted. Existing regression tests use mock writes against separate empty runtime paths.
