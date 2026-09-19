# S07 — Sweep API lifecycle and input security

2026-09-19 (Asia/Seoul). Workstream: WS-S07.

S07 — Software COMPLETE within the authenticated Dashboard API and fake-runtime
acceptance scope. Hardware: **NOT RUN — user-operated**. CI: **NOT CHECKED**.
S08 has not been started.

## Authorization and baseline

The user approved starting the next phase after S06. S07 started from clean
commit `6566d28e82bfc508bbfe08961d275e4ccaf7e364` on
`codex/current-source-pilot-v6`, matching its origin branch. The integrated
Master Spec, Data Contracts, Integration Notes, S00–S06 reports,
CONTRIBUTING.md, Dashboard routers/dependencies/schemas/facade, token policy,
model catalog/inventory adapters, S05 JobService reservations and the S06
supervisor were reviewed. No applicable AGENTS.md exists.

No research lock, formal inventory, runtime inventory, model, result,
credential or approval artifact was changed. No real Worker, SSH connection,
model download/load, inference or native RPC process was used.

## Implemented API and lifecycle

The authenticated `/api/sweeps` surface now provides:

- capabilities, cached preview and explicit read-only readiness refresh;
- save-draft, bounded list and get;
- start, pause, resume, cancel and retry-trial;
- paged events, SSE event replay, results and plan export.

Routes only translate HTTP concerns. `SweepService` owns lifecycle semantics,
`SweepDraftRepository` owns private saved-plan/API idempotency state, and S06
`SweepSupervisor` remains the sole sweep scheduler. Child work still goes
through the existing JobService and S05 resource coordinator. The formal
Campaign schema and gates are unchanged.

Drafts persist a server-verified plan snapshot, revision/hash, bounded public
request metadata and API operation claims. Raw prompt text is stored in a
separate mode-0600 private file and never appears in preview, manifest, event,
result or export-plan responses. Model-specific prompt variants are supported.
For `persist_prompt=false`, terminal completion removes the private prompt
file; retained prompts remain available only when policy permits manual retry.

Start reads only the saved plan, re-resolves its original input with fresh
server evidence, checks exact revision/hash/full-plan equality and then creates
the durable S06 manifest. Every dispatch performs the same drift check. Resume
and retry also require fresh equality. Preview reads only Controller cache;
Worker status/inventory refresh is confined to the explicit readiness route and
Start/Resume/Retry preflight. No route downloads, installs, relocks or directly
loads a model.

The current server resolver intentionally marks prompt token capacity unknown
unless exact server-side preparation evidence exists. It does not accept a
browser-supplied token count, template/checksum, actual config or verified
flag. Such unknown plans remain previewable/savable but cannot Start through
the S06 valid-cell gate. This preserves the S02/S03 exact-token contract; an
operator must first establish authoritative preparation evidence through the
supported runtime path rather than approving a client estimate.

## Security and recovery behavior

- All new schemas use `extra="forbid"`; nested sweep/domain parsing rejects
  unknown keys, booleans-as-integers, invalid axes and products over the S01
  budget. Prompt/model/target counts and text bytes are bounded. A router-level
  Content-Length guard rejects sweep bodies over 1 MiB before parsing.
- Model references resolve against the server catalog and cached Worker
  inventory. Client URLs, filesystem paths, hashes and capability claims are
  absent from the accepted schema and cannot authorize execution.
- Start/Resume/Retry require an idempotency key plus exact saved revision/hash.
  Every operation claim is durable. Same key/same payload replays; same key with
  another payload returns conflict. A two-request Start race creates one S06
  manifest and one child job.
- The existing Dashboard policy is preserved: auth-off remains available and
  auth-on requires `X-Cluster-Token`. The app does not use cookie credentials,
  and no CORS middleware authorizes cross-origin credentialed requests, so a
  new CSRF cookie path was not introduced. Every mutation uses POST.
- IDs are strict single-segment identifiers. Storage rejects symbolic-link
  draft roots, traversal and unsafe filenames. Export uses a fixed quoted
  filename built only after ID validation; callers cannot supply an export
  path or header value.
- Events use the per-sweep append-only journal. JSON pages are bounded to 200;
  SSE replays from `Last-Event-ID`/cursor with an owner-specific stream. A
  cursor ahead of the journal returns a recovery cursor rather than silently
  losing events. The journal count is not used as a benchmark metric.
- Pause/cancel/retry address an exact sweep and trial. Cancel forwards only
  that sweep's durable job IDs. JobService remains final resource admission, so
  formal global exclusivity, disjoint overlap and quarantined leases cannot be
  bypassed by request flags. Pi history/idle state is not used as a global
  blocker.
- Result failures expose stage, trial/cell, node/model and condition IDs plus a
  safe resolution hint. Raw exceptions, argv, paths, tokens and prompt text are
  omitted. Unexpected errors continue through the existing request-ID-only
  Dashboard handler.

## Fake/offline API coverage

The new tests use temporary draft/run directories, the S03 cached identities,
an injected resolver and an injected durable backend. TestClient and service
tests cover valid preview/save/start/list/get/export, all mutation endpoints,
fresh drift, stale revision/hash, stored-plan tamper, duplicate and concurrent
Start, canceling another ID, overlap serialization, auth on/off, unknown fields,
invalid axes, huge grids, 1 MiB rejection, traversal/header safety, event
paging, cursor recovery, SSE reconnect, structured results and
`hash_only`/`none` privacy cleanup. Production-resolver tests prove cached
preview does not call refresh and model/Worker identities come from server
objects.

## Actual validation

| Check | Actual result |
|---|---|
| New S07 API/service suite | **19 tests, OK, 1.280s** |
| Focused S06/S07 + Dashboard/security regression | **101 tests, OK, 9.675s** |
| Context/API/Dashboard isolation regression | **70 tests, OK, 8.053s** |
| Final full Python discovery | **670 tests, OK, 136.293s** |
| `compileall -q cluster scripts` | exit 0 |
| repository validator | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| offline wheel build | PASS; SHA-256 `3b517845145d1823096c23e23fa70c5f34882c18bb0717c6548deab763c197db` |
| npm syntax, fixtures, publication PNG and Playwright | PASS; **3 browser tests passed** |
| Git whitespace and credential-pattern checks | run before commit; results recorded in Git section/final response |
| ShellCheck | NOT RUN — no shell source changed |

An intermediate full run exposed an order-dependent existing context test
because the new test module imported the compatibility Dashboard facade during
test discovery and started its registry watcher. The test was corrected to
lazy-import the facade only inside resolver/route cases and to stop that watcher
at module teardown. The final full run passed cleanly. Starlette emitted its
existing TestClient/httpx deprecation warning.

## Compatibility and next boundary

All existing Experiment, result, Campaign and global event routes remain.
Authentication and auth-off behavior are unchanged. Sweep storage is a
separate versioned private namespace; older job/result readers need no
migration. Formal eligibility still comes only from the research subsystem,
and no sweep payload contains a formal-gate override.

S08 may build the Dashboard authoring and control UI on these endpoints. No S08
HTML, JavaScript or user workflow was added. Actual Worker readiness, exact
prompt preparation, model load, inference, RPC and physical cleanup remain
unverified until operator-controlled hardware work.

Only S07 source, fake/offline tests and this report are included. Commit and
normal feature-branch push results are reported after Git completes.

STOPPED after S07. S08 has not been started. Hardware: **NOT RUN**. CI:
**NOT CHECKED**.
