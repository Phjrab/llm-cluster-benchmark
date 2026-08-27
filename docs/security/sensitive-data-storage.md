# Sensitive Prompt and Response Storage

## Purpose

The benchmark can process medical or otherwise private text on a trusted,
isolated research LAN. Private network placement does not make result files
non-sensitive. Select the storage policy before each run and treat every
Controller backup as having the same sensitivity as the retained artifacts.

## Dashboard controls

`프롬프트 원문 저장` maps to `persist_prompt` and `응답 저장 범위` maps to
`response_storage_mode`.

| Configuration | Durable result content |
|---|---|
| `persist_prompt=true` | prompt text is retained in the private run config and response journal |
| `persist_prompt=false` | prompt SHA-256 and character count are retained; prompt-set version is retained when present |
| `response_storage_mode=full` | response text, exact character count, SHA-256, metrics, and failure metadata |
| `response_storage_mode=hash_only` | exact character count, SHA-256, metrics, and failure metadata; no response text |
| `response_storage_mode=none` | request identity, metrics, and failure metadata; no response text, length, or hash |

The default remains `persist_prompt=true` and `response_storage_mode=full` for
compatibility with existing runs and clients. For sensitive experiments, turn
prompt persistence off and choose `hash_only` only when output identity and
length are needed. Choose `none` when even output length or identity should not
be retained.

## Artifact boundaries

- `config.json` omits private prompt text and records its SHA-256 and character
  count when prompt persistence is off.
- `responses.jsonl` applies the selected response policy to every completed
  request. It is the only result artifact that can contain response text.
- `requests.csv` keeps its existing 19-column metric schema. In `none` mode its
  output length and hash cells are blank.
- `events.jsonl`, progress events, summaries, and ordinary application errors
  never contain raw prompt or response text. Exact occurrences in nested
  failure metadata are replaced with `[REDACTED]`.
- Saved Dashboard experiment definitions omit private prompt text.
- A queued or running durable job temporarily needs the prompt in its private
  `0700` runtime directory so it can survive a Controller restart. The prompt
  is replaced with its SHA-256 and character count when that job completes,
  fails, is cancelled, or is recovered as orphaned. Do not copy a live runtime
  directory to a less protected location.

All result, job, and journal files remain Controller-local and private by
default. The storage policy is not encryption and does not protect data from a
locally privileged account.

## Dashboard response states

The response inspector distinguishes these states instead of presenting a
non-persisted response as an empty model answer:

- `stored`: a response text field is available under a full policy;
- `hash_only`: only length and SHA-256 are available;
- `not_persisted`: response text, length, and hash were deliberately omitted;
- `legacy_missing`: an older artifact has no explicit storage status.

The response API enforces the recorded status. A malformed or manually edited
hash-only/none record cannot expose a raw response field through the API.
Legacy records with a response field remain readable as `stored`; a legacy run
without `responses.jsonl` remains readable as `legacy_missing`.

## Operational checklist

Before a sensitive run:

1. Verify the Dashboard is reachable only from the intended private LAN or
   loopback/VPN boundary.
2. Turn off prompt persistence.
3. Select `hash_only` or `none` according to the approved study protocol.
4. Confirm the experiment definition shown after submission contains a hash
   and count, not the prompt text.
5. Keep the Controller result and runtime directories on an encrypted volume
   when device-loss risk is in scope.

After a run:

1. Open the response inspector and verify the expected storage-state badge.
2. Confirm no experiment is still queued or running before copying artifacts.
3. Export only the artifacts required by the study protocol.
4. Use the recoverable result trash workflow for removal. A hash-only policy
   does not retroactively redact an older full result; create a new run or
   explicitly remove the old result through the reviewed retention workflow.

Never place real secrets, access tokens, SSH private keys, or patient
identifiers in model IDs, node names, experiment names, or other metadata
fields. Those fields are operational identifiers and remain persistable.
