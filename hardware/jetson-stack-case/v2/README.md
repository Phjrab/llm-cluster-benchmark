# Jetson Orin Nano rounded stack case v2

## HOLD: DO NOT PRINT V2

The centering-guide installation path is not validated and may not be assemblable as documented. Earlier geometric checks confirmed board insertion only after the guides were already installed. Pure vertical guide insertion crosses the retaining lip; an endwise outer-hook path crosses corner posts. Hold this v2 design pending corrected compact v3 and complete assembly/service-path checks. The historical geometry and reports are preserved; zero overlaps in the final assembled pose do not prove that every part can reach that pose.

Editable native FreeCAD model, seven fabrication/fit-coupon parts in STEP/STL, three-tier enclosure STEP, assembly previews and geometric validation. Start with [Korean assembly notes](README_assembly_ko.txt). The two 1 mm captured centering guides are mandatory; print the rail and pin/socket fit coupons first.

**Provisional prototype. Not physically printed, fitted, thermally certified or mechanically load tested.** Gravity-seated three-tier stacking requires external restraint. The accompanying PLA study is preliminary linear-static analysis and airflow energy-balance sensitivity, not physical certification or a thermal FEM/CFD prediction.

## Editable source and vendor reference
`JetsonOrinNano_StackablePrototype_Public.FCStd` preserves all 86 owned objects, Parameters, native feature history, seven printable outputs and the 12-solid enclosure assembly. A separate copy removed only four external NVIDIA reference objects. All owned shapes and printable outputs are unchanged; see `public_export_validation.json`. The NVIDIA full-kit CAD is not redistributed because its redistribution terms were not established. Obtain it from the [official reference links and placement instructions](EXTERNAL_REFERENCE.md) if required for personal fit verification. Original reference-containing private deliverable is unchanged. Some assembly preview images and original geometric collision checks show the official reference solely as contextual CAD illustration; NVIDIA does not endorse this design.

Manufacturing STEP/STL and tier assembly contain only the designed enclosure, guides, gate and coupons. Keep the Korean notes' stock guard, antenna, cable, I/O and insertion-path assumptions. No software or robot-control code is changed. Temporary helper scripts, autosaves, superseded v1 files and redundant ZIP copies are excluded.

`PUBLICATION_MANIFEST.json` lists exact repository file sizes and hashes. See `../pla-study` for analysis sources, results and reproduction package.
