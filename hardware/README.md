# Cluster hardware prototypes

CAD and preliminary analysis for benchtop Raspberry Pi 5 and Jetson Orin Nano stacks. These are geometric prototypes, not printed or hardware-qualified products. Application code is unchanged.

- [Raspberry Pi 5 stack v1](raspberrypi5-stack-case/v1/README.md): editable native model, eight part/coupon outputs, assembly, previews, validation and official MIT reference notice
- [Jetson rounded stack v2](jetson-stack-case/v2/README.md): editable reference-free native model, seven part/coupon outputs, assembly, previews and validation; official NVIDIA CAD is linked externally
- [Jetson PLA preliminary study](jetson-stack-case/pla-study/README.md): source scripts, original generated solver inputs/results in one canonical archive, review PDF, plots, summary and checksums

Print the relevant fit coupons first. Confirm actual hardware revision, fasteners, cable/antenna/fan clearance and temperatures. External restraint is required for three-tier stacks; transport, vibration, robot movement and more than three tiers are outside these designs' verified scope.

Each folder has an exact publication manifest. Redundant ZIP variants, caches, temporary files, solver binaries and superseded models are excluded. The PLA study's full archive retains its raw evidence and results; the public Jetson native file intentionally excludes unlicensed external NVIDIA geometry while retaining all owned parametric design features.
