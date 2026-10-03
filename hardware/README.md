# Cluster hardware prototypes

## Current versions
- [Compact Jetson v3 CAD](jetson-stack-case/v3/README.md): **142 × 100 mm**, unchanged width and reduced front/back depth; corrected guide service channels, editable reference-free native model, seven STEP/STL part/coupon outputs and tier assembly
- [Compact Jetson v3 PLA study](jetson-stack-case/v3/pla-study/README.md): exact final v3 frame, validated final linear-static cases, airflow energy-balance sensitivity, original input/result archive and reproducibility scripts
- [Raspberry Pi 5 CAD v1](raspberrypi5-stack-case/v1/README.md): editable native model, eight STEP/STL part/coupon outputs, assembly, previews, validation and official MIT reference notice
- [Raspberry Pi 5 PLA study](raspberrypi5-stack-case/pla-study/README.md): separate frame and removable-pin linear-static screening, airflow sensitivity, original input/result archive and reproduction scripts

These are unprinted geometric prototypes and assumption-bound preliminary studies. They do not certify actual fit, strength, temperature, airflow, retention, tipping, vibration or service life. Verify fit coupons, hardware/cable/antenna/fan clearance and sustained-load/temperature behaviour. Three-tier gravity stacks need external restraint; transport and robot movement are unverified. Thermal work is energy-balance sensitivity only, not thermal FEM/CFD or PLA/CPU temperature prediction.

v3 native core reopening/recompute and CAD assembly/service paths passed. Final GUI visual reopening remains unverified after the cloud FreeCAD GUI exited; upcoming preview images are final-geometry CAD renders, not GUI screenshots or physical-hardware photographs.

## Historical v2: DO NOT PRINT
The [Jetson v2 snapshot](jetson-stack-case/v2/README.md) has an unvalidated guide-installation path and may not assemble as documented. Earlier board-insertion checks assumed guides were already installed. Keep v2 printing on hold; use corrected v3 for current CAD review. The [historical v2 study](jetson-stack-case/pla-study/README.md) remains evidence for its stated old geometry/loading, not assembly proof or analysis of v3. Changed contact/load assumptions prevent a simple v2→v3 stiffness/cooling-improvement comparison.

## Complete reproducibility and provenance
Each folder has an exact publication manifest. Study archives use numbered raw byte parts with whole/per-part SHA-256 and a cross-platform Python reassembler; the parts are not independent ZIPs. All final validated solver inputs/results are retained. Discarded/incomplete compact-study trials are documented; their large raw failed-trial files remain local. Generated caches, duplicate user-delivery ZIP variants, temporary files and solver binaries are excluded.

NVIDIA full-kit CAD is linked externally; public Jetson native files contain the owned design only. Pi native reference geometry retains its MIT notice. Application and robot-control code are unchanged. This work remains on a draft PR; no merge or deployment is performed.
