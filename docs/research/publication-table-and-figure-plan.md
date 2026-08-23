# Publication Table and Figure Plan

## Reporting principles

- Every figure names the campaign, matrix version, lock fingerprint, model
  identity, prompt set, runtime cohort, strategy, node set, and quality subset.
- Points represent independent runs. Request-level points are shown only in
  explicitly nested distribution panels.
- Central estimates appear with uncertainty and the sample size.
- Missing and unavailable values remain missing; they are never converted to
  zero.
- Smoke, pilot, and formal outputs are visually and physically separated.

## Tables

| Table | Contents |
|---|---|
| 1. Reproducibility contract | Model SHA, prompt version, source/runtime fingerprint, platform profile, power condition |
| 2. Campaign coverage | Planned/completed/failed/excluded runs and reason codes by cell |
| 3. Single-node performance | Run-level throughput, TTFT, E2E, success rate, 95% CI by Worker/model/prompt |
| 4. Pi scaling | 1/2/3-node throughput, speedup, efficiency, failure rate |
| 5. Broadcast consistency | Logical groups, all-replicas success, exact agreement, per-node latency |
| 6. Quality sensitivity | Primary versus clean-only/warning-inclusive estimates |

RPC, heterogeneous clustering, causal power effects, and pure model-size effects
do not receive a formal table until their matrix blockers are resolved.

## Figures

1. **Single-node throughput:** run-level dot and 95% interval, faceted by model
   and prompt, with runtime cohorts explicit.
2. **Latency distribution:** TTFT and E2E ECDF plus compact box plots. Requests
   are grouped visually within runs.
3. **Pi scaling:** node count versus throughput, speedup, and efficiency. The
   Pi 02 single-node cell is the baseline.
4. **Broadcast agreement:** all-replicas success and exact agreement, paired
   with per-node latency rather than an inflated physical-throughput headline.
5. **Node variation:** independent run distributions for Pi 02/03/04 and
   cohort-labelled Jetson devices.
6. **Measurement quality sensitivity:** primary estimates beside clean-only
   estimates, with excluded counts printed below each panel.

Phase 05 may add energy/token, thermal, prefill/decode, and network/RPC panels.
Those figures are not promised until the metrics and availability rules are
implemented and pilot-validated.

## Export contract

Dashboard charts remain interactive and downloadable as theme-matched PNG.
Publication export uses vector SVG and 300/600 DPI PNG, colour-blind-safe
palettes, legible physical-size fonts, and no hidden series. A paper export must
reject incompatible run signatures instead of combining different prompt,
parameter, node, power, or runtime conditions under one caption.
