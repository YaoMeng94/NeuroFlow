# NeuroFlow DICOM Sorter

The desktop sorter copies scanner DICOM files into one folder per sequence and
can optionally convert a sorted subject to BIDS. The scanner export stays
unchanged. NeuroFlow's processing tools and external neuroimaging containers
are installed separately.

## Release status

- macOS arm64 0.4.11 is available for controlled internal testing at
  `dist/standalone_sorter/NeuroFlowDICOMSorter-macOS-arm64-0.4.11.zip`.
  Verify the adjacent `.zip.sha256` file and follow `INSTALL_MACOS.md`.
- The macOS app is ad-hoc signed and not notarized. Do not distribute it
  publicly.
- The Windows installer and Intel macOS archive are not yet validated by
  their native CI jobs. Do not redistribute older unversioned artifacts as
  current releases.

## Install and launch

Use only a versioned, checksum-verified package for your operating system. No
Python installation is needed to run a published standalone desktop package.

- **macOS arm64:** use the versioned archive above and follow the checksum and
  internal-test steps in `INSTALL_MACOS.md`.
- **Windows:** wait for a versioned installer from a passing native Windows
  CI run; no validated installer is currently available in this checkout.
- **Linux desktop:** install the Linux/HPC NeuroFlow bundle. The
  `neuroflow-dicom-sorter` command is added when its Python has Tk support.
  Headless HPC sessions should use the browser sorter.

When the Windows installer is available, download its adjacent `.exe.sha256`
file and verify the executable in PowerShell before running it:

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

## Convert a sorted subject to BIDS

1. Preview and sort the export first.
2. Select a `sub-…` entry under **Subject to convert**.
3. Choose a new or empty BIDS output folder. The app selects an included
   `dcm2bids` config for the tested Prisma sequences; use **Open config** to
   review or edit it. You can also browse to another config.
4. Optionally enter a session label, then click **Convert to BIDS…**.

`dcm2bids` and `dcm2niix` must be installed and available on `PATH`. Review the
mapping before conversion. The default config covers the observed anatomical,
multi-echo REST, diffusion, and spin-echo fieldmap acquisitions. Scouts,
PhysioLog recordings, and presentation-state reconstructions are left out of
BIDS for manual review. The converter writes NIfTI images and JSON sidecars,
and the app adds `dataset_description.json` when needed. DICOM sorting works
without these conversion tools.

This prototype is intended for internal academic use. Do not send identifiable
DICOM files unless your institution's data-sharing rules allow it.
