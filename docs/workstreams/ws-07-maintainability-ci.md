# WS-07 Maintainability and Automated Verification

## Scope and gap analysis

| Requirement | Existing state | Gap found | Resolution |
|---|---|---|---|
| Python, compile, JSON/lock, shell, wheel gates | Hosted Linux CI already ran all gates and used ShellCheck | Wheel/shell output was not consistently copied to evidence files | Add pipefail plus durable wheel-build, wheel-install, repository, and shell logs |
| macOS Controller lifecycle | Dedicated macOS 15 job exercised setup, PID identity, restart, loopback health, and compatibility tests | Compatibility-test output existed only in the Actions console | Preserve it in the macOS evidence artifact |
| Dashboard JavaScript and Chromium | Dedicated Node 22/Playwright job ran syntax, fixtures, PNG, and E2E; config retained trace/screenshot/video on failure | Upload paths named `playwright-report`/`test-results`, while Playwright writes `.artifacts/playwright`; evidence could be lost | Upload the actual artifact root |
| Workstream checkpoints | Pull requests triggered CI | Direct `workstream/**` pushes did not | Add the additive branch trigger |
| Hardware policy | Manual/nightly unavailable is explicit; release-candidate is blocking; raw results and cleanup evidence retained | None | Reused unchanged |
| Immutable Actions | Every external action uses a full 40-character commit SHA | None | Validator and regression test retained |
| Large Dashboard facade | Result, Settings, and Research services are already extracted behind injected, transport-neutral boundaries; routes call only the facade | Further extraction would increase change risk without a current behavior gap | Do not re-split in this workstream; document preferred and compatibility APIs |
| Deprecated/compatibility API | Compatibility exports and legacy inventory adapters are explicit and tested | No contributor-facing guide named them | Add `CONTRIBUTING.md` |
| Security/contribution guidance | Detailed docs existed below `docs/security` and prior reports | Standard repository entrypoints were absent | Add root `SECURITY.md` and `CONTRIBUTING.md` |
| Project license | No license file exists | Owner decision required | Record as unresolved; do not choose a license |

## Design note

No product API, result schema, Dashboard behavior, CLI behavior, or runtime dependency changes. Workflow changes only improve when checks run and where their evidence is retained. Existing service extraction is treated as sufficient incremental progress: `services.py` remains a compatibility assembly/facade while new transport-neutral responsibilities belong in `service_layers`.

The workflow continues to use hosted Linux for Python/package/shell/scientific validation, hosted macOS for the actual Controller lifecycle, hosted Chromium for Dashboard behavior, and an optional labelled self-hosted runner for hardware. Release-candidate hardware policy is unchanged and fail-closed.

## Compatibility and failure behavior

- A failing command in a piped logging step still fails the job because `pipefail` is set.
- Artifact uploads remain `if: always()` so partial failure evidence is retained.
- Required job names remain `Linux quality gate`, `macOS controller gate`, and `Browser E2E`; branch protection compatibility is preserved.
- External actions remain immutable SHA pins.
- Compatibility exports are not removed or renamed.

## Verification

Regression tests assert stable job names, Workstream triggers, correct Playwright artifact path, CI log filenames, immutable action pins, hardware unavailable semantics, release-candidate blocking, service-layer transport neutrality, and explicit compatibility exports. Full Python, compile, repository, wheel, JavaScript, and Chromium gates run before checkpointing.

Live hardware is reported as unavailable when no labelled runner/Workers are connected. Local ShellCheck is reported as not run when the binary is absent; hosted Linux remains the authoritative ShellCheck gate.

## Remaining risk and decisions

- The repository owner must choose a license before redistribution terms can be stated.
- A GitHub-hosted run of the changed workflow is the authoritative validation of YAML runner behavior and artifact upload paths after push.
- Further `services.py` extraction should be driven by a concrete change and characterization test, not file length alone.
