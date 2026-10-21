# NeuroFlow Linux/HPC Install

This bundle installs the NeuroFlow controller and DICOM sorter for one Linux
x86_64 user. It targets CPython 3.11 and glibc 2.17 or newer, and does not
require sudo or network access during installation.

1. In the directory containing the downloaded bundle, verify its checksum,
   then extract and enter the versioned directory:

   ```bash
   archive="$(printf '%s\n' NeuroFlow-Linux-x86_64-cp311-glibc217-*.tar.gz | sort -V | tail -n 1)"
   test -f "$archive" || { echo "Linux bundle not found" >&2; exit 1; }
   sha256sum -c "$archive.sha256"
   tar -xzf "$archive"
   cd "${archive%.tar.gz}"
   ```

2. Install NeuroFlow for your account:

   ```bash
   bash code/scripts/install_neuroflow_linux.sh
   ```

3. Configure paths to the external applications, images, and reference data in
   `~/.config/neuroflow/site.json`, then check the workflows you intend to run:

   ```bash
   ~/.local/bin/neuroflow doctor --workflow all
   ```

   The check uses the same planner requirements as job submission and exits
   nonzero for missing paths. It does not launch processing or inspect the
   contents/version of container images.

   The `doctor` command checks configured paths for selected workflows. It
   does not launch processing or inspect container contents or versions.

4. Start the browser controller in the foreground on a Linux desktop:

   ```bash
   ~/.local/bin/neuroflow-ui
   ```

   On an HPC, use a persistent terminal session if needed, for example
   `tmux new -s neuroflow ~/.local/bin/neuroflow-ui`. Keep the service bound to
   localhost and access it from a workstation through an SSH tunnel; do not
   expose it on a shared network interface:

   ```bash
   ssh -N -L 8765:127.0.0.1:8765 user@hpc-host
   ```

To remove one installed version while keeping user settings and job history:

```bash
bash code/scripts/uninstall_neuroflow_linux.sh \
  --venv "$HOME/.venvs/neuroflow-<version>"
```

The uninstaller prompts before removal. Add `--yes` for unattended use. It
removes only NeuroFlow launchers that point to that exact virtual environment;
site configuration, job state, BIDS data, and derivatives are not removed.

The installer creates a versioned environment under `~/.venvs`, leaves earlier
versions available for rollback, and preserves an existing site profile. Add
`~/.local/bin` to `PATH` to use short command names. The `neuroflow-dicom-sorter`
desktop command is installed only when the selected Python has Tk support; it
requires a graphical session. The browser sorter remains available on headless
systems.

The bundle contains NeuroFlow only. FreeSurfer, fMRIPrep, tedana, QSIPrep,
QSIRecon, Workbench, Apptainer images, and site reference data remain external
dependencies. Configure their paths in `~/.config/neuroflow/site.json`. The
installer does not alter BIDS inputs or derivatives.

The installer locates CPython 3.11 using `python3.11` first, then `python3`;
set `NEUROFLOW_PYTHON` to an explicit interpreter path when needed.
The Linux bundle also installs the `pipeline` Python extra (NumPy, NiBabel,
and SciPy) into NeuroFlow's private environment. Jobs launched by the controller
use that environment for pipeline scripts; the large neuroimaging applications
and container images remain separate site dependencies.
