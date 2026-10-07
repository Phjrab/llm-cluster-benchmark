# Implementation decisions

- Retain FastAPI/Jinja and vanilla modules. Add `lab.js` for independently testable display calculations and grouping; include it in syntax and fixture gates.
- Keep all nine hashes, existing selector IDs, input/selection store and publication/trash controls. `orbitWorkers` remains an internal compatibility ID but renders a plain Worker status list. Controller has its own management-only panel.
- Build Matrix from `/api/campaigns/:id`'s actual stored cells. Do not expand the repository's 1,080-run formal plan into apparent measured data. Without a durable campaign, show preparation/design links.
- Add compare projection fields without writing summaries. Derive a workload fingerprint only when every required workload parameter is present. Expose observed model/source facts; preserve missing tokenizer, quantization or measurement evidence as UNKNOWN. Energy efficiency requires backend complete energy coverage.
- Require complete comparison evidence for speedup. Legacy data are kept; their gaps are disclosed. Zero throughput is a valid numerator (0×); zero/missing ratio denominators are N/A. Latency speedup and change% have separate formulas. Success changes use pp.
- Prevent pending experiment double submission, keep campaign action pending guard, and reject formal start/resume/retry in the UI when the server gate is unconfirmed/closed. Backend admission remains authoritative.
- Keep cached read-only data on individual endpoint failure and disclose unavailable/stale state; unavailable gate clears former readiness visuals. Guard campaign selection responses with a revision so late responses cannot replace a newer selection.
- Existing HTML/cache-version assertions were updated for the actual changed assets and brand. The former unit assertion that supplied no condition identity now expects N/A; full-evidence latency direction (2×) remains explicitly tested in `test_research_lab.js`. No existing navigation, sweep approval, trash or export assertion was removed.
- External MLflow/W&B pages were not re-fetched; their links in the supplied REFERENCES were used as design context only. No external backend or service was installed.

- Final visual review corrected leftover dark-theme close/model-filter/error colors and publication clipping. Partial per-participant tokenizer/source evidence remains UNKNOWN; one known Worker cannot stand in for missing peers. After capture refuses uncommitted Dashboard source to keep manifest SHA accurate.
