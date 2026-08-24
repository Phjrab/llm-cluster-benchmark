# Publication Analysis Pipeline

## Scope

`scripts/research/phase11_publication.py` creates one immutable analysis bundle
from one result class. The input must be either `formal` or `pilot`; smoke data
and mixed result roots are rejected.

Pilot export requires `--acknowledge-non-formal`. Its README, manifest, summary,
and figure titles retain the pilot label and set `formal_claim_allowed=false`.
A formal export requires a `formal_campaign` manifest whose terminal status is
`completed`.

## Statistical contract

- Independent unit: run.
- Requests: nested descriptive observations only.
- Summaries: count, mean, median, sample SD, IQR, min/max, p50/p95, coefficient
  of variation, request failure rate, and 95% percentile-bootstrap interval.
- Bootstrap seed and resample count come from the locked analysis plan.
- Missing values remain absent and are never converted to zero.
- Failed and incomplete attempts remain in the outcome and exclusion tables.
- Prompt and response text are not copied into publication tables or figures.

Formal bundles contain clean-only, clean-plus-warning, degraded-inclusive, and
all-completed sensitivity subsets. Pilot bundles expose only explicitly
non-formal pilot subsets.

## Output

```text
<bundle>/
├── README.md
├── bundle-manifest.json
├── analysis/
│   ├── input-manifest.json
│   └── summary.json
├── figures/
│   ├── *.svg
│   ├── png-300dpi/*.png
│   └── png-600dpi/*.png
├── inputs/
│   ├── analysis source and render source
│   ├── exact matrix/protocol/analysis/lock documents
│   └── exact pilot or campaign manifest and analysis evidence
└── tables/
    ├── runs.csv
    ├── requests-nested.csv
    ├── cell-summary.csv
    ├── telemetry-samples.csv
    ├── node-contributions.csv
    ├── rpc-stages.csv
    └── exclusions.csv
```

The matching ZIP uses fixed entry timestamps and sorted paths. Every file is
listed with size and SHA-256 in `bundle-manifest.json`. Directories are private
(`0700`) and files are private (`0600`). Existing output paths are never
overwritten.

SVG figures are vector-first, white-background, Okabe–Ito coloured research
figures. The optional Node.js Playwright renderer produces 180 mm PNG copies at
300 and 600 DPI and inserts a matching PNG `pHYs` resolution chunk. Exact
Playwright and Chromium versions are recorded in the bundle.

The figure set is data-driven. Throughput, latency ECDF/boxplot, energy,
temperature, and preserved outcomes are generated when their source values
exist. Scaling is emitted only when the same model, prompt, and platform have
at least two node counts. Multi-node token contribution, per-platform
power/temperature time series, and RPC-stage duration figures are omitted when
the corresponding measurements are unavailable; absence is never rendered as
zero.

## Commands

Pilot example:

```bash
.venv/bin/python scripts/research/phase11_publication.py build \
  --results-root .run/controller/pilots/<pilot-id>/results \
  --output-dir .run/controller/publications/<pilot-id> \
  --experiment-type pilot \
  --acknowledge-non-formal
```

Formal example after a completed authorized campaign:

```bash
.venv/bin/python scripts/research/phase11_publication.py build \
  --results-root .run/controller/campaigns/<campaign-id>/results \
  --output-dir .run/controller/publications/<campaign-id> \
  --experiment-type formal
```

Use `--svg-only` only when vector output is intentionally sufficient and the
pinned browser renderer is unavailable. Formal result collection remains
blocked independently by the Phase 09/10 campaign admission gates.
