# NeuroFlow DICOM Sorter

The desktop sorter copies scanner DICOM files into one folder per sequence and
can optionally convert a sorted subject to BIDS. The scanner export stays
unchanged. NeuroFlow's processing tools and external neuroimaging containers
are installed separately.

## Test artifact status

Native platform builds and smoke tests passed on the `neuroflow-v0.4.14` tag
at commit `d258c08` in Actions run `39713726327`. It produced Linux and Windows
controller bundles, a Windows sorter installer, and macOS ARM64 and Intel
sorter apps. Linux install/upgrade/uninstall, glibc 2.17, sorter GUI, synthetic
DICOM-to-BIDS conversion, and unit/web tests passed in run `39713726335`. The
0.4.14 GitHub Release is an unpublished draft with versioned packages and
checksums for controlled internal testing; do not redistribute it as a stable
public release. The earlier `neuroflow-v0.4.13` draft remains unchanged. The
macOS apps are not notarized; follow the controlled internal-test steps in
`INSTALL_MACOS.md`.

## Install and launch

Use only a versioned, checksum-verified package for your operating system. No
Python installation is needed to run a published standalone desktop package.

- **macOS:** download the ARM64 or X64 artifact from the
  [`neuroflow-v0.4.14` draft release](https://github.com/YaoMeng94/NeuroFlow/releases/tag/neuroflow-v0.4.14),
  verify its checksum, and follow the internal-test steps in
  `INSTALL_MACOS.md`.
- **Windows:** download the Windows sorter installer from the
  [`neuroflow-v0.4.14` draft release](https://github.com/YaoMeng94/NeuroFlow/releases/tag/neuroflow-v0.4.14),
  verify its adjacent `.exe.sha256`, then run the installer.
  The controller bundle and its installation steps are in `INSTALL_WINDOWS.md`.
- **Linux desktop:** install the Linux/HPC NeuroFlow bundle. The
  `neuroflow-dicom-sorter` command is added when its Python has Tk support.
  Headless HPC sessions should use the browser sorter.

Verify the Windows sorter executable in PowerShell before running it:

```powershell
$installers = @(Get-ChildItem . -Filter 'NeuroFlowDICOMSorter-Windows-Setup-*.exe')
if ($installers.Count -ne 1) { throw 'Place exactly one versioned sorter installer in this folder.' }
$installer = $installers[0]
$expected = ((Get-Content "$($installer.FullName).sha256" -Raw).Trim() -split '\s+')[0]
$actual = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected.ToLowerInvariant()) { throw 'DICOM sorter installer checksum mismatch.' }
```

For source-based development, `code/scripts/Start_DICOM_Sorter.command` remains
available on macOS and requires Python 3, Tkinter, and pydicom. The Windows
source route requires Python 3, Tkinter, and pydicom as well.

## Sort DICOM files

1. Choose the scanner export folder. The app suggests a separate `_sorted`
   output folder.
2. Preview the subject labels. They come from each study's DICOM `PatientID`;
   the app does not assign sequential labels. IDs are cleaned to BIDS-safe
   letters and numbers for the output folder name.
3. Preview the sequences and inspect their categories. Unmatched and ambiguous
   series require review before copying.
4. Copy to the sorted folder. Original scanner files are left unchanged.

The folder layout is:

```text
scanner-export_sorted/
  sub-20261016001/
    anat/
      series-6_3dT1w_mpr_sag_1mm/
    dwi/
    func/
    _unmatched/
```

The included sequence rules were derived from one Prisma protocol. Use
**Advanced: change sequence rules** to open or select the JSON rule file.

## Convert sorted subjects to BIDS

1. Preview and sort the export first.
2. Under **Subject to convert**, select one `sub-…` entry or **All subjects**.
3. Choose a new or empty BIDS output folder. The app selects an included
   `dcm2bids` config for the tested Prisma sequences; use **Open config** to
   review or edit it. You can also browse to another config.
4. Optionally enter a session label, then click **Convert to BIDS…**.

When **All subjects** is selected, participants are converted one at a time
into the same BIDS output folder. Temporary staged DICOM files are removed
after each participant completes.

The macOS and Windows packages built from the updated source include
`dcm2bids` 3.2.0,
`dcm2bids_helper`, and `dcm2niix` 1.0.20260724. No separate converter install
is needed. The existing 0.4.14 packages predate this change and still require
both tools on `PATH`. Source-based launches can also use converter executables
installed on `PATH`. Review the mapping before conversion. The default config
covers the observed anatomical,
multi-echo REST, diffusion, and spin-echo fieldmap acquisitions. Scouts,
PhysioLog recordings, and presentation-state reconstructions are left out of
BIDS for manual review. The converter writes NIfTI images and JSON sidecars,
and the app adds `dataset_description.json` when needed. DICOM sorting works
without these conversion tools.

Third-party license notices for the bundled converters are included in the app.

This prototype is intended for internal academic use. Do not send identifiable
DICOM files unless your institution's data-sharing rules allow it.
