# NeuroFlow DICOM Sorter

The desktop sorter copies scanner DICOM files into one folder per sequence and
can optionally convert a sorted subject to BIDS. The scanner export stays
unchanged. NeuroFlow's processing tools and external neuroimaging containers
are installed separately.

## Test artifact status

The `neuroflow-v0.4.14` tag and unpublished draft at commit `d258c08` are the
previous candidate; they predate the latest DICOM conversion changes. Current
acceptance source is on `deployment-acceptance` as version `0.4.15`. Follow
the [branch test runs](https://github.com/YaoMeng94/NeuroFlow/actions/workflows/test-neuroflow.yml?query=branch%3Adeployment-acceptance)
and [native platform builds](https://github.com/YaoMeng94/NeuroFlow/actions/workflows/build-dicom-sorter.yml?query=branch%3Adeployment-acceptance)
for current artifacts. They are Actions artifacts, not release assets.
Linux/HPC install,
upgrade, uninstall, and glibc 2.17 checks also passed on node-1. No 0.4.15 tag
or draft release exists yet. The macOS apps are not notarized; follow the
controlled internal-test steps in `INSTALL_MACOS.md` only for the previous
0.4.14 draft artifacts.

## Install and launch

Use only a versioned, checksum-verified package for your operating system. No
Python installation is needed to run a published standalone desktop package.

- **macOS:** no current release asset is published. Use only a 0.4.15 artifact
  from the branch build workflow for internal acceptance, verify its checksum,
  and follow `INSTALL_MACOS.md`.
- **Windows:** no current release asset is published. Use only a 0.4.15
  artifact from the branch build workflow for internal acceptance and verify
  its checksum before running the sorter installer. Controller setup is in
  `INSTALL_WINDOWS.md`.
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

The 0.4.15 macOS and Windows Actions artifacts include
`dcm2bids` 3.2.0,
`dcm2bids_helper`, and `dcm2niix` 1.0.20260724. No separate converter install
is needed. The existing 0.4.14 draft packages predate this change and still
require both tools on `PATH`. Source-based launches can also use converter executables
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
