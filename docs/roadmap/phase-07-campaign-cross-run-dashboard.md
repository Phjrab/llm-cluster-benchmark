# Roadmap Phase 07 — Campaign and Cross-run Dashboard UX

## 1. Outcome

Phase 07 is complete on branch `codex/roadmap-phase-07`.

The Dashboard now exposes three additive research workspaces:

- durable campaign progress and result coverage;
- filtered cross-run comparison with an explicit baseline;
- formal research-readiness evidence across model, source, runtime, power,
  license, and instrumentation dimensions.

Existing Dashboard routes, result listings, run detail, deletion, experiment
execution, and publication exports remain available. No pilot or formal
campaign was created or started. Phase 08 automation and Phase 09 pilot work
were not started.

## 2. Checkpoints

| Checkpoint | Commit |
|---|---|
| Campaign, comparison, and readiness API projections | `ede3a38` |
| Campaign, comparison, and readiness Dashboard screens | `3507ea2` |

Both commits were pushed to `origin/codex/roadmap-phase-07` immediately after
creation.

## 3. Additive API contract

Phase 07 adds read-only projections over existing campaign manifests, result
summaries, live Worker status, and research lock documents:

```text
GET /api/campaigns
GET /api/campaigns/{campaign_id}
GET /api/research/compare
GET /api/research/readiness
```

The projection layer does not migrate or rewrite existing artifacts. Campaign
events are bounded in the response, while complete durable manifests remain on
disk. Cross-run result reads are bounded to 10,000 summaries.

Unknown historical fields remain explicit:

```text
campaign_id          uncampaigned
model_lock           unlocked
platform             unknown
runtime_fingerprint  unknown
power_mode           not_applicable or unknown
legacy_fallback      true
```

Phase 07 never invents a formal identity for a legacy result.

## 4. Campaign workspace

The Campaign screen presents:

- total durable campaign and cell counts;
- completed, running, failed, pending, blocked, and cancelled coverage;
- repeat-level progress;
- linked-run result coverage;
- formal eligibility and current drift;
- estimated remaining cells, runtime, and storage;
- cell-level model lock, prompt, topology, quality, status, run, and failure
  identity.

Cell rendering is bounded to the first 100 rows with the complete count stated
when a manifest is larger. The live Controller currently has no campaign
artifact, so the screen correctly shows an empty pre-Phase-09 state instead of
manufacturing progress.

## 5. Cross-run comparison workspace

The Compare screen filters runs by all Phase 07 dimensions:

- campaign;
- model lock;
- platform;
- node count;
- strategy;
- runtime fingerprint;
- power mode;
- measurement quality.

It supports throughput, TTFT p50, E2E p95, and success-rate metrics. Only
completed runs with a finite value enter the graph. Failed and cancelled runs
remain visible in the table without being converted into metric values.

The selected baseline is evaluated with metric direction preserved: higher is
better for throughput and success rate, while lower is better for TTFT and E2E
latency. The table keeps run status and measurement quality as separate fields
and shows platform, node count, node names, strategy, runtime, and power
evidence next to the metric.

The live Controller projection contained 29 historical runs, including 24
completed measurable runs. All 29 were correctly labelled as legacy fallbacks.
The existing data covers single-node, replicated round-robin, broadcast,
node-sweep, and model-parallel RPC strategies with 1-, 2-, 3-, and 6-node
topologies. Where the old result lacks platform or lock identity, the filter
shows `unknown` or `unlocked` rather than guessing Jetson or Pi from a name.

## 6. Chart and export behavior

The cross-run chart uses the existing high-DPI interactive canvas system and
separates clean, warning, degraded, and unknown measurement quality into
independent series. Hover/keyboard detail includes model, platform, node count,
strategy, runtime, power, quality, and status.

Exports are additive:

- themed Dashboard PNG;
- UTF-8 CSV for every filtered run, with spreadsheet-formula injection
  protection;
- publication SVG or 300/600 DPI PNG using the existing white-background,
  Okabe-Ito, one-column/two-column publication renderer.

The publication dialog records that this is a filtered cross-run comparison,
the measured run count, and the selected baseline. Existing single-experiment
publication safety checks are unchanged.

## 7. Research Readiness workspace

Readiness does not collapse independent evidence into one optimistic badge. It
reports:

- approved model locks and Worker checksum coverage;
- license-acceptance blockers;
- Worker source identity against the locked Controller source;
- runtime fingerprint match or drift;
- Jetson power-mode match, mismatch, or unknown state;
- API, backend, and environment instrumentation readiness;
- the roadmap phase gate that still blocks formal execution.

The live read-only projection reported:

| Dimension | Result |
|---|---:|
| Approved models | 2 / 4 |
| License blockers | 1 |
| Worker source identity match | 0 / 6 |
| Runtime fingerprint match | 6 / 6 |
| Instrumentation ready | 3 / 6 |
| Formal eligibility | blocked |
| Blocking issues | 9 |

Worker source observations were unavailable through the current live status
projection, so the UI shows `UNKNOWN / unobserved`; it does not silently call
them matching. The formal gate also remains closed because Phase 09 has not
selected repeat count and cooldown/thermal policy or refreshed the final
source lock.

## 8. Browser and responsive verification

The running Dashboard was restarted with the Phase 07 code and tested in the
Codex in-app browser at `http://127.0.0.1:8080/`.

Verified interactions:

- all eight navigation destinations render and remain reachable;
- the empty Campaign state explains the Phase 09 dependency;
- all eight Compare filters populate from the backend projection;
- strategy filtering updates the run set, chart, baseline, and table;
- baseline ratios and failed-run metric exclusion render correctly;
- the publication dialog opens for the filtered cross-run chart;
- Research Readiness renders approved models, all six Workers, and blockers;
- desktop styling follows the existing Dashboard theme;
- a temporary 390 x 844 viewport has no document-level horizontal overflow;
- the mobile navigation scrolls to Campaign, Compare, and Research Readiness;
- the temporary viewport override was reset after verification.

The browser automation layer emitted a minified DOM-reader `nodeName` error
while performing semantic locator operations. No matching code exists in the
application sources, it was not emitted on an ordinary page reload, and all
page interactions and product tests remained successful. It is recorded as a
test-harness observation, not a product failure.

## 9. Connected hardware read-only verification

No Worker code, package, model, environment, power mode, RPC binary, or runtime
file was installed, rebuilt, synchronized, removed, or restarted in Phase 07.

The live inventory status check passed for all six registered Workers:

| Worker | Address | SSH | Project | API | Active model |
|---|---|---:|---:|---:|---|
| jetson-worker-01 | 192.168.0.26 | PASS | PASS | PASS | Qwen2.5 1.5B |
| jetson-worker-02 | 192.168.0.19 | PASS | PASS | PASS | none |
| jetson-worker-03 | 192.168.0.6 | PASS | PASS | PASS | none |
| pi-worker-02 | 192.168.0.14 | PASS | PASS | PASS | Qwen2.5 1.5B |
| pi-worker-03 | 192.168.0.9 | PASS | PASS | PASS | none |
| pi-worker-04 | 192.168.0.5 | PASS | PASS | PASS | none |

The read-only readiness view preserves the Phase 06 evidence: Jetsons share
runtime fingerprint `05e2c27b3bf0eff2`, Pis share `b4387053e655722a`, and all
six runtime identities match their locked platform profile. Source/power
evidence that is not currently observable remains unknown and blocks formal
eligibility rather than being inferred from connectivity.

## 10. Test gates

Final Phase 07 validation:

- full Python project regression with Controller socket lifecycle:
  **411/411 PASS** in **55.393 seconds**;
- Phase 07 Dashboard/research/matrix focused gate: **92/92 PASS**;
- new campaign, compare, readiness, API, legacy-fallback tests:
  **11/11 PASS**;
- Dashboard JavaScript syntax for `app.js` and `research.js`: PASS;
- Dashboard export/filter/chart fixtures: PASS;
- Python compileall and launcher Python compile: PASS;
- affected shell launcher syntax: PASS;
- live Phase 07 API reads: HTTP 200;
- desktop and 390 px browser verification: PASS;
- `git diff --check`: PASS.

The only dependency warning is the existing upstream Starlette/httpx
deprecation warning. No dependency was installed or upgraded.

## 11. Main files

Backend projection and routes:

- `cluster/dashboard/research_views.py`
- `cluster/dashboard/services.py`
- `cluster/dashboard/routes.py`
- `cluster/tests/test_dashboard_research.py`

Dashboard UX and fixtures:

- `cluster/dashboard/templates/index.html`
- `cluster/dashboard/static/js/research.js`
- `cluster/dashboard/static/app.js`
- `cluster/dashboard/static/styles.css`
- `cluster/tests/test_dashboard_exports.js`

## 12. Stop boundary and remaining work

- Phase 08 CI, browser E2E automation, and hardware workflow automation were
  not started.
- Phase 09 pilot, repeat-count decision, cooldown/thermal freeze, final source
  relock, and formal-gate opening were not started.
- No durable campaign exists in the live Controller runtime yet.
- No pilot or formal inference request was sent to a Worker.
- Phase 11 automatic statistical publication artifacts were not started; this
  phase only extends the existing chart/export system to filtered run data.

Phase 07 stops here. The next roadmap step is Phase 08 only when explicitly
requested.
