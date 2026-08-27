# WS-08 Operations and Research Documentation

## Scope and gap analysis

| Requirement | Existing state | Gap found | Resolution |
|---|---|---|---|
| Architecture and trusted-LAN threat model | `cluster/README.md` and security documents described the boundaries | No single reading path joined the roles, network boundary, and commands | Add one canonical operations/research guide and link it from both READMEs |
| Strategy placement and request flow | Strategy registry and cluster README listed all five modes | Metric interpretation was separated from the strategy table | Join placement, logical-to-physical mapping, research question, and limitation in one table |
| Logical/physical and broadcast semantics | WS-01 measurement contract defined additive fields | Operators had to infer formulas and the duplicate-token warning across documents | Add explicit formulas, aliases, and a broadcast interpretation rule |
| Single Worker inference slot | Worker API and WS-01 recorded `inference_slots=1` and lock wait | It was not in the primary operating guide | Explain Controller queue versus Worker lock queue and serialization |
| RPC load, LAN cost, security, cleanup | Cluster and security guides covered lifecycle | Research interpretation and cleanup acceptance were not adjacent | Add one RPC boundary section with load-time treatment, LAN caveat, fixed cleanup command, and stop condition |
| Jetson/Pi power interpretation | Power and identity documents were complete | Operating comparison rules were scattered | Add controlled Jetson comparison conditions and the four Pi integrity states without inventing wattage |
| Experiment quality and formal approval | Protocol, analysis, campaign, and identity documents were complete | No concise smoke/pilot/formal decision path existed | Add quality table and a fail-closed approval checklist |
| Sensitive storage and artifact schemas | Dedicated contracts existed | File-specific version numbers could look like one global schema | Add a storage summary and artifact-family compatibility table |
| Recovery and retention | Durable job/campaign and security docs existed | Suite recovery, RPC cleanup, trash, and formal protection were not one operator sequence | Add recovery and retention sequence |
| Every result metric has calculation and caution | Measurement docs covered most instrumentation formulas | Core throughput, percentile, broadcast, scaling, and aliases lacked one complete operator table | Add counts/latency/throughput and instrumentation tables with formula and caution columns |

Existing phase/refactor documents were not renamed or rewritten. Their detailed historical evidence remains the source for the phase that produced it.

## Design note

This Workstream changes no public API, result schema, runtime default, Dashboard behavior, CLI behavior, or measurement boundary. The new guide is a navigation and interpretation layer over checked-in behavior. It names exact commands already accepted by the Controller setup and CLI parsers.

Legacy results remain readable under their artifact-family schema. Missing measurement files and nullable metrics are described as unavailable rather than zero. Failure behavior is documentation-only: incomplete formal evidence blocks approval, RPC cleanup failure blocks reuse of affected nodes, and heterogeneous results remain exploratory.

Security guidance keeps the trusted private-lab LAN scope and does not claim that optional tokens encrypt traffic. Timing guidance preserves the implemented request wall boundary and explicitly keeps model load, warmup, cooldown, persistence, and RPC cleanup outside inference throughput.

## Verification strategy

A documentation contract regression test checks the root quick start against real repository scripts and launcher actions, requires every Workstream acceptance topic and formula field in the canonical guide, verifies the mixed-platform warning and phase-document preservation statement, and validates internal Markdown links.

The full Python, compile, repository validation, wheel, JavaScript, and browser gates are run unchanged. No hardware experiment is required or claimed by this documentation-only Workstream.

## Non-goals and remaining risk

- No product or schema change and no real hardware run.
- This guide does not replace the exact JSON locks, schema validators, formal protocol, analysis plan, or security policy.
- Operators can still export a graph with a misleading handwritten title; publication review must confirm the metric and cohort labels.
- Project licensing remains an owner decision recorded by WS-07.
