# NeuroFlow macOS Install and Internal Test

The macOS DICOM sorter is built as a native `.app` bundle and distributed in a
ZIP archive. NeuroFlow's browser controller and scientific processing tools
are installed separately; the sorter app does not bundle FreeSurfer, fMRIPrep,
tedana, QSIPrep, QSIRecon, Workbench, or Apptainer images.

## Current distribution status

The `0.4.14` draft artifacts at commit `d258c08` are superseded and predate
the current conversion changes. Native GitHub Actions builds for the `0.4.15`
acceptance branch run on both ARM64 and Intel. Check the
[branch build workflow](https://github.com/YaoMeng94/NeuroFlow/actions/workflows/build-dicom-sorter.yml?query=branch%3Adeployment-acceptance)
for current artifacts and results. They are Actions artifacts, not published
release assets; no 0.4.15 release tag exists.

The apps are **not notarized**. No valid code-signing identity was available on
the build Mac when checked on 2026-10-22. Public distribution requires a
Developer ID Application identity and Apple notarization. Until the required
credentials are available, use only the controlled internal-test route below
and verify the adjacent checksum before opening an app. The older 0.4.12 app built with
Python 3.13/Tk 8.6.15 failed during GUI startup on macOS 27; do not use that
older archive on macOS 27. The legacy unversioned app has no reliable version
metadata and is not a release artifact.

## Internal test steps

1. For internal acceptance, download a 0.4.15 macOS sorter artifact from the
   branch build workflow and locate the matching `.zip` and `.zip.sha256`
   files. Do not use this archive for public distribution.
2. Verify the archive checksum before opening it:

   ```bash
   archive="NeuroFlowDICOMSorter-macOS-ARM64-0.4.15.zip"
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
