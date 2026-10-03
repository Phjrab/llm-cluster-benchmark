# Compact Jetson v3 PLA preliminary study

This study uses the final 142 × 100 mm frame STEP including rounded rear guide-service channels, SHA-256 `4b03f9d8fd696466c35360a9d7859cf208a581c82649890b3845c1cf844603bb`. It is not analysis of the discarded 139 mm draft or the historical 142 × 132 mm v2. See [Korean notes](README_ko.md), the four-page Korean PDF, assumptions, load-footprint diagram, convergence CSV and validated result summaries.

**Preliminary linear-static gravity screening only. No printed-part strength safety factor, hardware assembly certification, retention, creep, lifetime, vibration or thermal safety approval.** Kit allowance remains an assumed 300 g per tier. Printed weight is recalculated from this CAD's full-solid volume; 500/1000/2000 MPa are uncalibrated stiffness sensitivities, not measured 30% infill material data or a validated lower bound.

Stock-base seating contact footprints were extracted and approximated for load application; the full NVIDIA kit CAD is not redistributed. Upper tiers load the column shoulders outside socket-entry radii. Contact separation/friction, tilt, slip, pin pull-out, gate retention, cable force and shock are not solved. Fine-mesh maximum displacement is 0.00386807 mm under the stated E1000 MPa assumptions; displacement changes 0.395% between h5/h4, while peak stress remains 26.77% mesh-sensitive. This is preliminary convergence, not complete local stress convergence.

Thermal work is P/(rho Cp Q) energy-balance sensitivity only. No thermal FEM/CFD or PLA/CPU temperature prediction was run. Changed footprint is not evidence of improved cooling. v2 used different print-weight and contact/load assumptions, so its displacement/stress cannot establish a simple performance-improvement percentage for v3.

## Complete final solved archive
Five `.zip.part001` through `.zip.part005` files are raw ordered byte segments of one canonical archive, not independently valid ZIPs. Run `python3 reassemble_archive.py` (Windows: `py reassemble_archive.py`) to verify part hashes, reconstruct `Jetson_compact_v3_PLA_reproducible_study.zip` (35,277,070 bytes, SHA-256 `93e750d11c3e2d890e0c2c9545662530abc3262d19b17f2c3b41731cc2c0a4fc`) and validate ZIP CRCs. The standard-library script refuses to overwrite a different output.

The archive retains original generated inputs/results and evidence for the final validated direct-solver cases. `DISCARDED_TRIALS.json` records incomplete/resource-limited and analytically rejected trials; their large unfinished raw trial files are preserved locally rather than duplicated into this publication. Generated NPZ caches are omitted and rebuilt by rerunning the meshing/solver before postprocessing. The separate user-delivery ZIP variants mentioned in the Korean notes are not duplicated in the repository.

Set `STUDY_DEPS` to official dependencies described in `dependencies.txt`; use the reproduction commands in the Korean notes. No solver binaries, vendor PDF copies or private native/vendor STEP geometry are included. `PUBLICATION_MANIFEST.json` lists files actually published here with repository-relative sizes and hashes; the original delivery manifest records package provenance.

CAD construction/assembly-path checks are reported separately in the [v3 model folder](../README.md). FEM does not validate those assembly paths. Physical fit and sustained-load/temperature tests remain required before operating a printed stack.
