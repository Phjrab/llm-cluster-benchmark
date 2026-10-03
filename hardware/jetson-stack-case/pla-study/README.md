# Jetsonstack v2 PLA preliminary study

This is a reproducible **prototype screening study**, dated 2026-10-03, for the
rounded-v2 stackable Jetson Orin Nano enclosure. The original Korean explanation
is preserved unchanged in [README_ko.txt](README_ko.txt), with the three-page
[Korean review PDF](Jetsonstack_v2_PLA_preliminary_review_ko.pdf).

**Only a linear-static structural solver was executed. No thermal FEM or air CFD
was executed.** The airflow chart and thermal CSV are an assumed mixed-air energy
balance, not simulated PLA temperatures. These files do not certify printed-part
strength, three-tier stability, thermal safety or long-term PLA service life.

## What is included

- `Jetsonstack_v2_PLA_reproducible_study.zip.part001` through `.part005`: five
  consecutive byte-for-byte parts of the single canonical archive, each at most
  8 MiB. Reconstruct `Jetsonstack_v2_PLA_reproducible_study.zip` as described below.
  The reconstructed archive is **41,234,249 bytes**, SHA-256
  `f74cc70e9dc146fdb681bd9be83929148906e886c989d6ad1c552ee267b2c899`
- `ARCHIVE_RECONSTRUCTION.json`: original archive identity and the ordered part
  names, byte counts and SHA-256 checksums
- `reassemble_archive.py`: cross-platform standard-library-only reassembly,
  verifying every part and the whole ZIP without replacing a different output
- `frame.step`: the custom rounded-v2 enclosure frame used for this study,
  byte-identical to the original modeling export
  `jetson-stack-case/rounded-v2/01_repeatable_frame.step`
- Six source scripts, the original dependency notes, final JSON validation
  summaries, three CSV files, the Korean report and two plots as individual files
  for convenient browsing
- `PUBLICATION_MANIFEST.json`: relative paths, byte counts, SHA-256 and Git blob
  SHA-1 for every publication file other than the manifest itself

The canonical ZIP extracts into `Jetsonstack_v2_PLA_study/`. Its 42 entries include
the original STEP, both final Gmsh meshes, CalculiX input decks, raw `FRD`/`DAT`
solver results and solver logs, a compression-patch benchmark, scripts, summaries,
report and plots. Its embedded checksum list covers the other 41 entries.

The ZIP contains no solver binaries, third-party material-data/manual PDF copies
or NVIDIA developer-kit reference CAD. Its only STEP file is the custom enclosure
frame. External references are linked below. The report PDF was generated for
this study by `build_report.py`.

Alternative delivery archives, package installers, Python-library copies,
generated NPZ caches, temporary run/plot logs, PDF QA page renders, private native
inspection helpers and upload metadata are not published separately. The
canonical archive bytes are preserved exactly in the five numbered parts,
including its original validation and solver evidence logs. No duplicate full ZIP
is included in this publication folder. Source CAD and study originals were not
modified.

## Reconstruct the canonical ZIP

Download all five parts into the same directory, preserving their exact names.
They are raw byte segments, not independently extractable ZIP files. Join them
in numerical order before opening or extracting the archive.

Recommended on Linux/macOS:

```sh
python3 reassemble_archive.py
```

Recommended on Windows:

```powershell
py -3 reassemble_archive.py
```

The script reads `ARCHIVE_RECONSTRUCTION.json` beside it, verifies each part's size
and SHA-256, checks the combined archive size/SHA-256 and all ZIP CRCs, and then
creates the ZIP. An existing identical valid ZIP is left unchanged; an existing
different file causes an error and is never overwritten. All verification and
reassembly works offline. Keep the script, manifest and five parts together.

Alternatively, Linux/macOS can concatenate the parts directly. This command
refuses to overwrite any existing output ZIP:

```sh
set -C
cat Jetsonstack_v2_PLA_reproducible_study.zip.part001 \
    Jetsonstack_v2_PLA_reproducible_study.zip.part002 \
    Jetsonstack_v2_PLA_reproducible_study.zip.part003 \
    Jetsonstack_v2_PLA_reproducible_study.zip.part004 \
    Jetsonstack_v2_PLA_reproducible_study.zip.part005 \
    > Jetsonstack_v2_PLA_reproducible_study.zip
set +C
```

Verify the reconstructed archive is exactly **41,234,249 bytes** and its SHA-256
is **f74cc70e9dc146fdb681bd9be83929148906e886c989d6ad1c552ee267b2c899**.
For Linux/macOS, the following Python check also tests every ZIP entry's CRC:

```sh
python3 -c "from pathlib import Path; import hashlib,zipfile; p=Path('Jetsonstack_v2_PLA_reproducible_study.zip'); data=p.read_bytes(); assert len(data)==41234249; assert hashlib.sha256(data).hexdigest()=='f74cc70e9dc146fdb681bd9be83929148906e886c989d6ad1c552ee267b2c899'; z=zipfile.ZipFile(p); assert z.testzip() is None; print('Archive size, SHA-256 and ZIP CRCs verified')"
```

On Windows, run the same verification with `py -3` instead of `python3`.
Publication preparation verified that ordered concatenation of all five parts
is byte-for-byte identical to the untouched original full ZIP and passes its CRC
test.

## Structural model and limitations

- Gmsh 4.13.1 and CalculiX 2.20; quadratic `C3D10` tetrahedra with straight
  midside nodes; units mm, N, MPa and tonne
- One lowest-tier frame, with equivalent loads from the two upper tiers; this is
  not a solved three-tier contact assembly
- Assumed kit mass 0.30 kg per tier and upper printed-part mass 0.15 kg per tier;
  the lowest frame also has gravity using density 1.24 g/cm3
- One isotropic elastic continuum with E = 1000 MPa and Poisson ratio 0.35;
  E = 500 and 2000 MPa displacement sensitivities use linear inverse-E scaling,
  not additional nonlinear solves or calibrated printed-PLA properties
- Four underside corner regions constrained in Z, with minimal XY anchoring;
  these are kinematic supports, not unilateral contact or lift-off
- Assumed print settings: 0.4 mm nozzle, 0.42 mm line width, 0.2 mm layer height,
  four walls, 30% gyroid infill and five top/bottom layers. The continuum mesh does
  not resolve this infill or layered microstructure

No contact/friction, joint separation, layer failure, print defects, creep,
temperature-dependent stiffness, warping, hook failure, vibration, drop, cable
tension or tipping analysis was executed. Manufacturer 100%-infill test-coupon
strength is not used to claim a safety factor for the assumed printed frame.

The two final mesh levels are a preliminary convergence check, not proof of
converged local corner stresses. Earlier curved quadratic elements with negative
Jacobians were discarded; only the positive-Jacobian final results are included.

## Recorded final results

At E = 1000 MPa under the assumed stationary gravity loads:

| Mesh size | Nodes | C3D10 elements | Rail vertical deflection | Peak nodal von Mises |
| --- | ---: | ---: | ---: | ---: |
| 4.0 mm | 72,232 | 38,998 | 0.011221 mm | 0.277601 MPa |
| 2.8 mm | 154,234 | 88,507 | 0.0113924 mm | 0.288675 MPa |

Refinement changes rail deflection by approximately 1.53% and peak stress by
3.99%. Both final meshes have zero nonpositive-Jacobian elements. Mesh volume
errors relative to CAD are approximately 0.417% and 0.200%. The analytical
compression benchmark passes: 0.001 mm compression, approximately 0.100 MPa
compressive stress and 10 N reaction. Gravity-corrected load/reaction balance
relative errors are below 1.4e-7. See the JSON and CSV files for unrounded values.

## Airflow sensitivity is not thermal simulation

The calculation is `delta_T = P / (rho * Cp * Q)`, using assumed air density
1.18 kg/m3, heat capacity 1005 J/(kg K) and 1 CFM = 0.00047194745 m3/s. Total
three-tier heat loads of 45/75/90 W and effective shared airflow are exploratory
assumptions. NVIDIA's 25 W module mode does not establish whole-device heat output.

At 90 W, ambient 25 C and assumed effective 5/10 CFM, mixed-air outlet values are
57.2/41.1 C. Neither is a PLA temperature prediction or upper bound. Actual fan
pressure/flow, recirculation, thermal contacts, radiation and local hotspots are
unknown. The 45 C review/stop level used in the plots is a provisional engineering
test criterion, not a manufacturer's continuous-use temperature rating.

Print fit coupons and one tier first, fit both centering guides, and measure
frame temperatures near rails, post shoulders and sockets under the actual
maximum sustained workload and worst expected ambient condition. Reassess if
temperature approaches/exceeds the provisional 45 C level or if deflection,
layer separation or permanent fit changes occur. A three-tier prototype requires
external stabilization and physical load/temperature checks; brief testing does
not establish long-term creep life. Do not lift the assembly by its upper tier.

## Reproduction

Work in a disposable copy: scripts overwrite result files. Either reconstruct
and extract the canonical ZIP and enter `Jetsonstack_v2_PLA_study/`, or copy this folder to a new
working directory. The individually published scripts are unchanged from the
archive. No Python packages, Gmsh/CalculiX binaries or solver runtime libraries
are bundled.

The original run used Python 3.12, NumPy, Matplotlib, ReportLab, Gmsh Python API
4.13.1 linked to a compatible system `libgmsh.so.4.13.1`, and CalculiX 2.20 with
SPOOLES 2.2 from official Debian bookworm amd64 packages. `dependencies.txt`
preserves the original environment notes. NumPy, Matplotlib and ReportLab versions
were not pinned in those notes; this is not a hermetic environment lockfile.

For a compatible Linux x86_64 environment with Python 3.12, curl and dpkg-deb:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install numpy matplotlib reportlab
python -m pip install --target tools/python gmsh==4.13.1

mkdir -p tools/ccx results
curl -fL https://deb.debian.org/debian/pool/main/c/calculix-ccx/calculix-ccx_2.20-1_amd64.deb -o tools/calculix.deb
curl -fL https://deb.debian.org/debian/pool/main/s/spooles/libspooles2.2_2.2-14_amd64.deb -o tools/spooles.deb
dpkg-deb -x tools/calculix.deb tools/ccx
dpkg-deb -x tools/spooles.deb tools/ccx

PYTHONPATH=tools/python python -c "import gmsh; gmsh.initialize(); print(gmsh.__version__); gmsh.finalize()"
python run_study.py
python verify_solver.py
python check_mesh.py
python postprocess.py
python plot_results.py
python build_report.py
```

Provide the compatible Gmsh shared library and any system runtime dependencies if
the Gmsh import check fails. These scripts expect the CalculiX executable at
`tools/ccx/usr/bin/ccx` and SPOOLES runtime libraries under
`tools/ccx/usr/lib/x86_64-linux-gnu`; macOS/Windows or another architecture requires
adapting those local tool paths. `frame.step` must remain alongside the scripts.

`run_study.py` regenerates `mesh.npz`; `postprocess.py` regenerates `fields.npz`.
Those derived caches were deliberately omitted from the canonical archive and
publication folder. Run the workflow in the order above; invoking postprocessing
or plotting directly on the extracted archive before regenerating its caches
will fail. Raw solver results remain available in the ZIP for independent review.

The publication preparation verified archive CRCs and embedded checksums, source
identity, Python syntax, PDF rendering, image integrity and consistency of the
recorded summary/CSV results. It did not rerun the structural solver.

## Sources

- [Bambu Lab PLA Basic Technical Data Sheet V3.0, pp. 2-5](https://store.bblcdn.com/s7/default/b189de92249a4b9ebed28b8ea1f080f0/Bambu_PLA_Basic_Technical_Data_Sheet.pdf)
- [NVIDIA Jetson Orin Nano hardware FAQ, Q10](https://forums.developer.nvidia.com/t/jetson-orin-nano-hw-faq/249118)
- [NVIDIA Jetson Linux r36.4.4 power management documentation](https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonOrinNanoSeriesJetsonOrinNxSeriesAndJetsonAgxOrinSeries.html)
- [Gmsh official reference manual](https://gmsh.info/doc/texinfo/)
- [CalculiX 2.20 official manual](https://www.dhondt.de/ccx_2.20.pdf)

Source links describe the references used in the original study. No redistribution
license is asserted for third-party manuals, data sheets or NVIDIA CAD, and no
such third-party files are embedded in this publication package.
