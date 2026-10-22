# NeuroFlow Windows Install

The Windows controller bundle installs NeuroFlow for the current Windows user
without administrator privileges. It requires 64-bit CPython 3.11 and the
Python Launcher (`py.exe`). If these are not installed, install Python 3.11 x64
from [python.org](https://www.python.org/downloads/windows/) and rerun the
installer.

The Windows controller bundle was built and its native smoke tests passed on
commit `bdb6750` (Actions run `39710399285`). The tests cover current-user
installation, reinstall/profile preservation, live UI startup, and uninstall;
the incomplete-wheelhouse failure path was also exercised. The standalone
Windows DICOM sorter installer and app were built and tested in the same
workflow. These are temporary Actions artifacts, not a versioned GitHub
Release, so do not treat them as a stable public release.

Download the `NeuroFlow-Windows-controller` artifact from Actions run
`39710399285`. Verify the bundle archive and adjacent `.zip.sha256` file before
extracting it:

```powershell
$archives = @(Get-ChildItem . -Filter 'NeuroFlow-Windows-x86_64-cp311-*.zip')
if ($archives.Count -ne 1) { throw 'Place exactly one NeuroFlow Windows bundle in this folder.' }
$archive = $archives[0]
$expected = ((Get-Content "$($archive.FullName).sha256" -Raw).Trim() -split '\s+')[0]
$actual = (Get-FileHash $archive.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected.ToLowerInvariant()) { throw 'NeuroFlow bundle checksum mismatch.' }
```

1. Extract the verified archive.
2. In the extracted folder, run `install_neuroflow_windows.ps1` from PowerShell.
   If script execution is restricted, use:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_neuroflow_windows.ps1
   ```

3. Start **NeuroFlow** from the Start Menu. The installer also creates command
   launchers under `%LOCALAPPDATA%\NeuroFlow\bin`.

Installing a newer bundle creates a separate versioned environment and keeps
older versions for rollback. The existing site profile and job data are kept.
To remove one version while preserving user configuration and job state, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall_neuroflow_windows.ps1
```

The bundle includes NeuroFlow and Python package dependencies only. FreeSurfer,
fMRIPrep, tedana, QSIPrep, QSIRecon, Workbench, Apptainer images, and site
reference data must be installed separately and configured in
`%APPDATA%\NeuroFlow\site.json`.

The Windows-installed controller does not execute the anatomy, BOLD, or DWI
launchers locally. Those require a POSIX shell and a Linux Apptainer
environment; do not configure a Windows path to a Linux `.sif` image and expect
local execution to work. For pipeline jobs, install NeuroFlow on the HPC and
use its browser UI from Windows through SSH forwarding:

1. In an SSH session on the HPC, start the per-user UI and keep it running:

   ```bash
   ~/.local/bin/neuroflow-ui --port 8765
   ```

2. In Windows PowerShell, forward the local port to that HPC session:

   ```powershell
   ssh -N -L 8765:127.0.0.1:8765 <user>@<hpc-host>
   ```

3. While the tunnel is open, visit `http://127.0.0.1:8765` in a browser.
   Press Ctrl+C in PowerShell to close the tunnel, then stop the HPC UI process.

The HPC UI binds to loopback; do not expose it directly on a shared network
interface. The Windows installation remains useful for local controller
functions, but it is not a remote executor by itself.
