# Roadmap Phase 03 — Hardware and Runtime Condition Re-lock

Date: 2026-08-23 (Asia/Seoul)

Status: COMPLETE

Scope: Roadmap Phase 03 only; Phase 04 experiment matrix and analysis plan were not started

## 1. Outcome

All six Workers were re-observed after restart and locked as four explicit
runtime cohorts. Formal eligibility is now cohort-scoped: selected Workers must
be a subset of exactly one cohort. Cross-cohort pooling fails closed with
RUNTIME_COHORT_MISMATCH.

| Cohort | Workers | Locked condition | Formal capacity |
|---|---|---|---:|
| jetson-orin-r3647-maxn-super-cuda | Jetson 01 | R36.4.7, kernel 5.15.148, MAXN_SUPER | 1 |
| jetson-orin-r3650-maxn-super-cuda | Jetson 02 | R36.5.0, kernel 5.15.185, MAXN_SUPER | 1 |
| jetson-orin-r3650-15w-cuda | Jetson 03 | R36.5.0, kernel 5.15.185, 15W | 1 |
| pi5-ubuntu2404-kernel1061-openblas | Pi 02/03/04 | Ubuntu 24.04.4, kernel 6.8.0-1061, OpenBLAS | 3 |

This resolves the Phase completion condition by explicit cohort separation,
not by silently treating unlike hardware/runtime conditions as homogeneous.

## 2. Git and deployment checkpoint

| Item | Value |
|---|---|
| Feature branch | codex/roadmap-phase-03 |
| Cohort/power-policy implementation | 6dc88f8b2feeb8d7531d966dc11e4e25bc66c093 |
| Runtime lock v3 approval | 1160a51c320bc0060eefd175e80d4926b5b988d6 |
| Deployed source tree | 9136db62128920190c13fc11e9a10c72b33a1fbabc91823c528fdc494c691a59 |
| Deployed source files | 158 |
| Native RPC commit | f49e9178767d557a522618b16ce8694f9ddac628 |

All six Workers report the same source commit/tree, clean verified deployment,
verified backend, pinned RPC commit, and unloaded model state. Per-Worker
deployment manifest hashes remain distinct because they include node-local
identity.

## 3. Jetson power and runtime evidence

Fresh nvpmodel reads found the common modes 15W, 25W, and MAXN_SUPER. Jetson 01
additionally advertises 7W. All three report jetson_clocks=OFF.

| Worker | Before/after mode | L4T | kernel | CUDA compiler build |
|---|---|---|---|---|
| Jetson 01 | MAXN_SUPER / MAXN_SUPER | R36.4.7 | 5.15.148-tegra | 35059454_0 |
| Jetson 02 | MAXN_SUPER / MAXN_SUPER | R36.5.0 | 5.15.185-tegra | 34714021_0 |
| Jetson 03 | 15W / 15W | R36.5.0 | 5.15.185-tegra | 34714021_0 |

The Controller has no passwordless permission to change nvpmodel on these
Workers. The phase therefore did not bypass sudo, store a password, modify
sudoers, answer a reboot prompt, or perform an OS/L4T update. Jetson 03 records
the exact optional follow-up command:

    sudo /usr/sbin/nvpmodel -m 2

After that manual change, the machine must be re-observed before its cohort is
changed. Automatic reboot remains forbidden.

NVIDIA documents that MAXN_SUPER exposes the maximum core and clock
configuration while carrying no fixed watt budget, and that some nvpmodel
changes can require reboot. Therefore the dashboard recommendation now prefers
one unambiguous MAXN/MAXN_SUPER profile over a bounded watt profile, but never
guesses when multiple MAXN profiles exist.

Official references:

- https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonOrinNanoSeriesJetsonOrinNxSeriesAndJetsonAgxOrinSeries.html
- https://docs.nvidia.com/jetson/archives/r36.5/ReleaseNotes/Jetson_Linux_Release_Notes_r36.5.pdf

## 4. Raspberry Pi power baseline

All three Pi Workers were observed after the same reboot boundary and report:

- Ubuntu 24.04.4 LTS;
- kernel 6.8.0-1061-raspi;
- Python 3.12.3;
- verified OpenBLAS backend and runtime fingerprint b4387053e655722a;
- synchronized NTP;
- no current undervoltage, frequency cap, throttling, or thermal-limit bit.

| Worker | get_throttled | Classification |
|---|---|---|
| Pi 02 | 0x50000 | history warning, non-blocking |
| Pi 03 | 0x0 | clean |
| Pi 04 | 0x0 | clean |

The Pi 02 history warning remains evidence, not a readiness failure. Any active
bit during measurement still degrades that run.

## 5. Common runtime lock

Lock version 3 records, for every Worker:

- hardware model, architecture, OS, kernel, Python, RAM, and free storage;
- NTP synchronization;
- backend and runtime fingerprint;
- exact deployment commit/tree/manifest and RPC commit;
- power state, temperature, and available power telemetry;
- an exact formal cohort assignment.

The four research files share fingerprint:

    3d5c7f2429c42d02db60ab635a263db6edd86fb4527078eb8d5ff80fc30744a5

Prompt text, approved model binaries, sampling values, result schema, and the
19-column request CSV were not changed.

## 6. Formal eligibility decisions

- Pi 02/03/04 may run single-node and up-to-3-node homogeneous formal cells.
- Each current Jetson cohort may run a single-node formal cell.
- Jetson 01+02 is excluded because L4T/kernel/CUDA compiler builds differ.
- Jetson 02+03 is excluded because the locked power mode differs.
- Any mixed Jetson cohort and any Jetson/Pi formal pooling is excluded.
- Existing mixed-platform smoke evidence remains valid as interoperability
  evidence, not publication-comparable performance evidence.

## 7. Test gates

- focused research/power/deployment/measurement tests: 65/65 PASS;
- full Python regression: 356/356 PASS in 49.604 seconds;
- dashboard JavaScript syntax/export fixtures: PASS;
- Python compile, shell syntax, lock fingerprint, and git diff check: PASS.

## 8. Safety and stop boundary

- No benchmark was run.
- No model was downloaded or removed.
- No automatic reboot, L4T upgrade, sudoers change, or password handling
  occurred.
- Worker source was changed only through the Controller sync path from a pushed
  commit.
- Phase 04 matrix/protocol/analysis work was not started.
