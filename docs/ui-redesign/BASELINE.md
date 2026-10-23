# Baseline — 2026-10-07

Initial source HEAD: `9245e24` (`docs: show fixture dashboard capture examples`). Initial work tree clean; remote verified as `https://github.com/Phjrab/llm-cluster-benchmark.git`. No existing process or production data was used.

The former Overview devoted the first viewport to a large promotional heading and Controller orbit. Compare silently selected a replacement baseline and calculated ratios without condition evidence. Summary power used numeric coercion, permitting null to count as 0. Controller readiness used optimistic defaults. These are the concrete redesign targets.

| Command, repository cwd | Exit / result | Log |
|---|---|---|
| `npm run test:syntax` and `test:fixtures` via initial `npm test` | 0 / PASS | `logs/baseline-npm.log` |
| Initial `npm test` publication browser launch | 1 / sandbox Chromium Mach-port restriction | `logs/baseline-npm.log` |
| `npm run test:publication-png` with browser execution permitted | 0 / PASS | `logs/baseline-publication.log` |
| `npm run test:e2e` with loopback/browser execution permitted | 0 / 13 PASS | `logs/baseline-e2e.log` |
| Initial Python discover in sandbox | 1 / 718 tests, 3 loopback binding errors | `logs/baseline-python.log` |
| Python discover with loopback execution permitted | 0 / 718 PASS | `logs/baseline-python-unrestricted.log` |

Node 22.17.1, npm 11.18.0, pinned Playwright 1.62.1 and installed Chromium were available. Existing `.venv` was reused; no dependency download, global install or framework migration occurred. Sandbox failures were resolved by scoped execution permission, not by changing tests.

`captures/before-*` are actual Chromium outputs of source `9245e24` with the same `research-lab-v1` synthetic fixture, action scenario and viewport as after captures. The reproducible capture script reads baseline template/static assets with `git show`, allowing recapture without checking out or overwriting working files. The manifest contains only files actually generated. Synthetic data are UI evidence only.
