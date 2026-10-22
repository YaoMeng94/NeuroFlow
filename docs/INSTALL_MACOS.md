# NeuroFlow macOS Install and Internal Test

The macOS DICOM sorter is built as a native `.app` bundle and distributed in a
ZIP archive. NeuroFlow's browser controller and scientific processing tools
are installed separately; the sorter app does not bundle FreeSurfer, fMRIPrep,
tedana, QSIPrep, QSIRecon, Workbench, or Apptainer images.

## Current distribution status

Native GitHub Actions packaging passed for both macOS architectures on commit
`f6c9ac9` (run `39712203406`): the ARM64 and Intel sorter app archives were
built, checksummed, and passed their packaged self-tests and GUI startup checks.
Download the `NeuroFlowDICOMSorter-macOS-ARM64` or
`NeuroFlowDICOMSorter-macOS-X64` artifact from that workflow run. These are
temporary Actions artifacts, not a versioned GitHub Release; do not treat them
as a stable public release.

The apps are **not notarized**. No valid code-signing identity was available on
the build Mac when checked on 2026-10-22. Public distribution requires a
Developer ID Application identity and Apple notarization. Until the required
credentials are available, use only the controlled internal-test route below
and verify the adjacent checksum before opening an app. The older 0.4.12 app built with
Python 3.13/Tk 8.6.15 failed during GUI startup on macOS 27; do not use that
older archive on macOS 27. The legacy unversioned app has no reliable version
metadata and is not a release artifact.

## Internal test steps

1. Download one of the macOS sorter artifacts from the
   [`neuroflow-v0.4.14` draft release](https://github.com/YaoMeng94/NeuroFlow/releases/tag/neuroflow-v0.4.14)
   and locate the packaged `.zip` and adjacent `.zip.sha256` file.
   Do not use this archive for public distribution.
2. Verify the archive checksum before opening it:

   ```bash
   archive="NeuroFlowDICOMSorter-macOS-ARM64-0.4.14.zip"
   shasum -a 256 -c "$archive.sha256"
   ```

3. Extract the app bundle without changing its contents:

   ```bash
   ditto -x -k "$archive" .
   ```

4. Run the bundled resource check, then launch the app:

   ```bash
   ./NeuroFlowDICOMSorter.app/Contents/MacOS/NeuroFlowDICOMSorter --self-test
   open ./NeuroFlowDICOMSorter.app
   ```

If macOS blocks the app, verify the approved source and checksum first. For
this internal test only, review the warning in **System Settings > Privacy &
Security** and use **Open Anyway** for this specific app. Do not disable
Gatekeeper globally or remove quarantine attributes as a blanket workaround.
Stop if the signature or checksum does not match. Keep Gatekeeper enabled.
Native CI checks establish that both architectures package and start; they do
not replace testing on the target macOS version.
