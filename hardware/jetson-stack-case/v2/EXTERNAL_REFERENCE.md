# External NVIDIA reference

`JetsonOrinNano_StackablePrototype_Public.FCStd` contains the enclosure's editable native geometry and Parameters spreadsheet, including frame, gate, captured centering guides, fit coupons and three-tier enclosure assembly. It excludes external NVIDIA developer-kit reference geometry.

Only these reference objects were removed from a separate copy:

- OfficialStockBase: stock lower guard reference
- OfficialKitReference: complete NVIDIA developer kit STEP compound
- OfficialKitReference_Level2: reference instance at tier 2
- OfficialKitReference_Level3: reference instance at tier 3

The original private/native deliverable and original reference STEP were not modified. Fabrication geometry and enclosure parameters were not changed. There are no dependencies from the enclosure features to the excluded reference objects.

For personal verification, obtain the official P3766 / P3768 / P3767 STEP package directly from NVIDIA:

https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/orin_nano/docs/jetson_orin_nano_devkit_3d_step_model.zip/

Official download center:

https://developer.nvidia.com/embedded/downloads

The source package used during enclosure development contains P3766-P3768SKU4-P3767ENVELOPE.stp (20230320). Follow the provider's current terms for downloading and using its CAD. No NVIDIA CAD redistribution license is asserted by this repository.

For reproducing the original visual placement, import the source STEP separately and place its complete compound at X=25.00, Y=21.25, Z=12.90 mm for tier 1; use Z=72.90 and 132.90 mm for tiers 2 and 3. Keep original reference shape coordinates and orientation unchanged. The fitted kit's lowest envelope is nominally Z=8.00 mm at tier 1. Reimporting is optional; all enclosure construction features are self-contained.

The public-export validation report verifies recompute/save/reopen, preservation of every owned object/expression/label, shape bounds/areas/volumes, exact Boolean symmetric-difference volumes for printable outputs and the enclosure assembly, and absence of embedded NVIDIA reference shape files.
