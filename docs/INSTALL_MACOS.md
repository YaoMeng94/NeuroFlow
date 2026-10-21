# NeuroFlow macOS Internal Test

The macOS DICOM sorter is built as a native `.app` bundle and distributed in a
ZIP archive. The native GitHub Actions build runs the bundled resource self-test
before creating each architecture-specific archive.

## Current distribution status

`dist/standalone_sorter/NeuroFlowDICOMSorter-macOS-arm64-0.4.12.zip` is the
current arm64 internal-test candidate. It was built on a native arm64 Mac from
the 0.4.12 source with Python 3.13.11 and PyInstaller 6.22.3. The bundled
`--self-test` passed, the app passed strict ad-hoc signature verification, and
the desktop session opened the app and displayed its main window in the original
test environment. The archive was extracted and retested; its SHA-256 is
recorded in the adjacent `.zip.sha256` file.

### Current macOS 27 compatibility blocker

On macOS 27.0.1, the local Python 3.13.11 runtime reports Tk 8.6.15. The
0.4.12 archive's `--self-test` passes, but its packaged GUI executable exits
immediately with SIGABRT (`-6`) when launched on this host. The equivalent
source GUI launch reproduces an AppKit `NSMenuItem` exception during Tk's Aqua
menu initialization (`Invalid parameter not satisfying: aString != nil`).
Treat the 0.4.12 app archive as **not verified for macOS 27** and do not use it
there as a release candidate. This is a runtime compatibility failure, not a
DICOM-data issue.

Tk's upstream macOS 27 menu issue was fixed on its 8.6/9.0 development branches
in October 2026, but the Python runtime bundled in this app predates that fix.
That upstream report is a plausible runtime-level lead, not proof that its
specific fix resolves this app's startup exception. Rebuild and retest with a
Tk build containing the fix before drawing that conclusion. A passing
`--self-test` only checks bundled resources and does not prove that the GUI
opens. See the [upstream Tk issue](https://core.tcl-lang.org/tk/tktview/33b3af1ceac681ad0f77)
and [Python's macOS Tcl/Tk version notes](https://docs.python.org/3.13/whatsnew/changelog.html).

The legacy unversioned app and ZIP in `dist/standalone_sorter/` have no reliable
release version (the app metadata reports `0.0.0`) and must not be presented as
0.4.12. This 0.4.12 candidate is ad-hoc signed only;
an Apple Development identity is present, but no Developer ID Application
identity or notarization credentials were verified. It is not ready for
public distribution. The native GitHub Actions build and Intel macOS artifact
still require a run from the private repository.

## Internal test steps

1. Obtain the versioned archive and its `.zip.sha256` file through the lab's
   approved channel.
2. Verify the archive checksum before opening it:

   ```bash
   archive="NeuroFlowDICOMSorter-macOS-arm64-0.4.12.zip"
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

If macOS blocks the app, first verify that the archive came from the approved
lab channel and that its checksum matches. For this internal test only, review
the block in **System Settings > Privacy & Security** and use **Open Anyway**
for this specific app. Do not disable Gatekeeper globally or remove quarantine
attributes as a blanket workaround. Stop testing if the signature or checksum
does not match the distributed artifact.

The sorter does not bundle FreeSurfer, fMRIPrep, tedana, QSIPrep, QSIRecon,
Workbench, Apptainer images, or site reference data. Those remain separately
installed dependencies of the NeuroFlow controller workflows.
