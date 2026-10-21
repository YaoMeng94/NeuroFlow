# NeuroFlow macOS Install and Internal Test

The macOS DICOM sorter is built as a native `.app` bundle and distributed in a
ZIP archive. NeuroFlow's browser controller and scientific processing tools
are installed separately; the sorter app does not bundle FreeSurfer, fMRIPrep,
tedana, QSIPrep, QSIRecon, Workbench, or Apptainer images.

## Current distribution status

An arm64 **0.4.13 internal-test candidate** is available locally as
`dist/standalone_sorter/NeuroFlowDICOMSorter-macOS-arm64-0.4.13-internal.zip`
with an adjacent SHA-256 file. It was built from commit `0795388` on macOS 27
with CPython 3.10.11, Tk 8.6, and PyInstaller 6.22.3. Its resource self-test,
strict ad-hoc signature verification, checksum verification after extraction,
and an 8-second packaged GUI startup check passed on this Mac. This demonstrates
that this Python/Tk build path starts on macOS 27; the separate native GitHub
Actions build uses CPython 3.11 and has not yet been run for 0.4.13.

The candidate is **not notarized and is not a public release**. The available
Apple Development signing identity is not a Developer ID Application identity.
Public distribution requires Developer ID signing and Apple notarization. The
older 0.4.12 app built with Python 3.13/Tk 8.6.15 failed during GUI startup on
macOS 27; do not use that older archive on macOS 27. The legacy unversioned app
has no reliable version metadata and is not a release artifact. The Intel
macOS build also remains unverified until its native CI job passes.

## Internal test steps

1. Obtain the 0.4.13 internal-test archive and its `.zip.sha256` file through
   the lab's approved channel. Do not use this archive for public distribution.
2. Verify the archive checksum before opening it:

   ```bash
   archive="NeuroFlowDICOMSorter-macOS-arm64-0.4.13-internal.zip"
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
Stop if the signature or checksum does not match. Native CI builds must pass
their installation/startup checks before a versioned public release is
considered.
