# Raspberry Pi 5 v1 PLA preliminary study

Finalized Pi 5 frame and removable board-pin STEP geometry were analysed with CalculiX 2.20 using linear-static gravity loading. See [Korean screening notes](README_ko.md), the four-page Korean PDF, `analysis_assumptions.json`, `results/summary.json` and the convergence CSV.

**Preliminary screening only. No printed hardware, load capacity, retention, fatigue, creep, vibration, PCB safety or lifetime certification.** The assumed board/cooler/cable load is 150 g per tier, not an official or measured mass. Full-solid CAD mass is used for the printed structure, while 500/1000/2000 MPa are uncalibrated stiffness sensitivities. No printed-part safety factor is established.

The frame and local pin have separate load-path models. The pin model assumes four evenly loaded seated shoulders and does not simulate insertion, pull-out, friction, slip or actual board contact. Its peak stress remains mesh-sensitive. Read all support/contact assumptions before interpreting results.

Thermal work is only P/(rho Cp Q) energy-balance sensitivity. No thermal FEM/CFD was run, and PLA/CPU temperatures are not predicted. The official 1.09 CFM fan value is a single-fan nominal maximum; it does not establish actual installed three-tier airflow or a temperature bound.

## Complete original archive
The four `.zip.part001` through `.zip.part004` files are ordered raw byte segments of one canonical archive, not independently usable ZIPs. They retain the original generated meshes, solver decks, FRD/DAT results and evidence logs. Run:

    python3 reassemble_archive.py

On Windows use `py reassemble_archive.py`. The standard-library script verifies each part, reconstructs `RaspberryPi5_v1_PLA_reproducible_study.zip` (26,377,738 bytes, SHA-256 `224964c2a5089e3c4be2ea3c07167d9199d576c7355fff60d020a293e1452e1e`) and validates ZIP CRCs. It refuses to replace a different existing output. See `ARCHIVE_RECONSTRUCTION.json` for exact ordered parts and hashes.

Generated NPZ caches are intentionally omitted and must be regenerated before postprocessing. Set `STUDY_DEPS` to a directory containing the official dependencies described in `dependencies.txt`, then run the meshing/solver, patch check, postprocessing and plotting commands from the Korean notes. The original development machine's sibling `tools` folder is not included in the repository. No solver binaries or vendor PDFs are redistributed. Separate user-delivery ZIP variants mentioned in the original Korean notes are not duplicated here.

`source-cad-manifest.json` ties this study to the Pi v1 CAD at `../v1`. `PUBLICATION_MANIFEST.json` gives repository-relative sizes and SHA-256/Git blob hashes. `DELIVERY_MANIFEST.json` is original package provenance; the publication manifest identifies the files actually present in this folder. No application or robot-control code is changed.
