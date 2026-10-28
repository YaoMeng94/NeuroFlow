# NeuroFlow Linux and HPC installation

This guide applies to Linux x86_64 systems with CPython 3.11 and glibc 2.17 or
newer. The controller installs into a per-user virtual environment and does not
need sudo or network access when the offline bundle is available. NeuroFlow is
the control app; neuroimaging programs and container images are separate site
dependencies. A system administrator may install those shared dependencies once
for all users, or a user may point the profile at personal installations.

## 1. Verify and unpack the bundle

In the directory containing the downloaded archive and checksum:

```bash
archive="$(printf '%s\n' NeuroFlow-Linux-x86_64-cp311-glibc217-*.tar.gz | sort -V | tail -n 1)"
test -f "$archive" || { echo "Linux bundle not found" >&2; exit 1; }
sha256sum -c "$archive.sha256"
tar -xzf "$archive"
cd "${archive%.tar.gz}"
```

The archive contains the controller wheel, its offline Python wheelhouse,
installation scripts, documentation, and a compact reference-atlas library.

## 2. Install NeuroFlow for your account

```bash
bash code/scripts/install_neuroflow_linux.sh
```

The installer creates a versioned environment under `~/.venvs`, keeps earlier
versions available for rollback, and preserves an existing site profile. It
installs the controller plus the Python packages needed by its pipeline
launchers. It does not install FreeSurfer, fMRIPrep, tedana, QSIPrep, QSIRecon,
ANTs, Connectome Workbench, newMSM, or Apptainer.

The installer looks for CPython 3.11 as `python3.11`, then `python3`. To choose
an interpreter explicitly, set `NEUROFLOW_PYTHON` to its absolute path before
installing. Add `~/.local/bin` to `PATH` for short command names. The desktop
DICOM sorter launcher is available when the selected Python includes Tk; the
browser interface works on headless systems.

## 3. Locate dependencies and their absolute paths

Use the installation already provided by your site when available. Do not copy
multi-gigabyte container images into each user's home directory. Ask the system
administrator which shared paths are intended for use, then verify each path:

```bash
command -v apptainer
command -v wb_command
command -v antsApplyTransforms
command -v newmsm
printf 'FREESURFER_HOME=%s\n' "$FREESURFER_HOME"
test -x "$FREESURFER_HOME/bin/recon-all"
test -r "$FREESURFER_HOME/license.txt"  # or the separately supplied license path
```

For containerized tools, identify the site's `.sif` files and verify that your
account can read them. Keep the versions already validated by your lab unless
you are intentionally testing a new version:

```bash
command -v apptainer
apptainer --version
test -r /absolute/site/path/fmriprep.sif
test -r /absolute/site/path/qsiprep.sif
test -r /absolute/site/path/qsirecon.sif
```

For tedana, configure either a readable tedana container image or an executable
installed in an environment available to the job. For ANAT/BOLD, the Workbench,
newMSM, and ANTs executables must also be visible to the account running the
job. The DWI workflow may additionally use FSL and MRtrix from the QSIPrep/
QSIRecon containers. TemplateFlow is optional unless the selected workflow or
container setup requires a local cache.

Some fMRIPrep images also contain tedana. Check the image directly before
reusing it, then set `tedana_image` to that same absolute path and leave
`tedana_command` empty:

```bash
apptainer exec --cleanenv /absolute/site/path/fmriprep.sif tedana --version
```

If the command is absent, install or obtain a separate tedana runtime and set
the corresponding `tedana_image` or `tedana_command` field instead.

All paths in the site profile should be absolute paths as seen by the compute
node or container runtime. Do not put shell variables such as `$HOME` or
`$FREESURFER_HOME` into the JSON file. Paths to shared software must be readable
from the node where the job will run.

## 4. Configure the site profile

The installer creates `~/.config/neuroflow/site.json` on first installation.
It automatically points `atlas_root` to the bundled HCP standard-mesh files.
Keep this value for ordinary NeuroFlow use; set it to another compatible atlas
root only when your site maintains a validated replacement.

Edit the profile and replace each example path with the absolute path found in
the previous step. Keep `pipeline_root` and the bundled `atlas_root` written by
the installer; they point into that versioned installation and are refreshed
on upgrade. Change only host dependency paths and optional workflow settings.
Leave unused optional values empty:

```json
{
  "pipeline_root": "/path/managed/by/neuroflow/installation",
  "freesurfer_home": "/absolute/path/to/freesurfer",
  "fs_license_file": "/absolute/path/to/freesurfer/license.txt",
  "newmsm": "/absolute/path/to/newmsm",
  "atlas_root": "/path/managed/by/neuroflow/installation/data/atlases/standard_mesh_atlases",
  "wb_command": "/absolute/path/to/wb_command",
  "ants_apply_transforms": "/absolute/path/to/antsApplyTransforms",
  "fmriprep_image": "/absolute/path/to/fmriprep.sif",
  "tedana_image": "",
  "tedana_command": "/absolute/path/to/tedana",
  "qsiprep_image": "/absolute/path/to/qsiprep.sif",
  "qsirecon_image": "/absolute/path/to/qsirecon.sif",
  "apptainer": "/absolute/path/to/apptainer",
  "templateflow_home": "",
  "atlas_datasets": {}
}
```

The complete profile also contains optional workflow settings. Preserve keys
created by `init-site`; add or change only the paths relevant to your site.
For example, `tedana_image` and `tedana_command` are alternatives. A custom
QSIRecon atlas can be registered in `atlas_datasets` as a name-to-path mapping
when the atlas is correctly formatted for the QSIRecon template space.

The bundled parcellation library contains Glasser360 and Schaefer2018 volumes
at 2 mm in FSL MNI152 space, including 100–1000 parcels with both 7- and
17-network labelings. Their source metadata and license notes are included.
The Schaefer volumes are provided as reference assets: do not pass them to
QSIRecon as custom atlases until their template space, orientation, and header
have been validated against the installed QSIRecon version. QSIRecon also ships
recognized built-in atlases such as AAL116, AICHA384Ext, Brainnetome246Ext,
Gordon333Ext, and 4S atlases; see the [QSIRecon atlas documentation](https://qsirecon.readthedocs.io/en/stable/builtin_workflows.html#atlases)
for supported names and citations.

## 5. Check paths before a job

Run the configuration check for each workflow you plan to use:

```bash
~/.local/bin/neuroflow doctor --workflow anat
~/.local/bin/neuroflow doctor --workflow bold
~/.local/bin/neuroflow doctor --workflow dwi
```

`doctor` checks the paths required by the planner. It does not launch a job or
prove that an image is runnable, that its internal software version is correct,
or that a dataset is scientifically suitable. Before a real run, confirm image
read access and version, validate the BIDS input, choose a new output directory,
and review the GUI/CLI job plan.

## 6. Start the GUI

On a desktop, start the server in the foreground:

```bash
~/.local/bin/neuroflow-ui
```

On a remote system, use a persistent session if needed and keep the service
bound to localhost. From the workstation, forward the port over SSH:

```bash
# On the compute host
tmux new -s neuroflow '~/.local/bin/neuroflow-ui'

# On the workstation
ssh -N -L 8765:127.0.0.1:8765 user@compute-host
```

Open `http://127.0.0.1:8765` on the workstation. If that port is already in
use, start NeuroFlow on another port with `~/.local/bin/neuroflow-ui --port
8766` and forward the same port in the SSH command. Do not expose the GUI on a
shared network interface.

## Uninstall one version

```bash
bash code/scripts/uninstall_neuroflow_linux.sh \
  --venv "$HOME/.venvs/neuroflow-<version>"
```

The uninstaller asks before removing that version. Add `--yes` for an
unattended removal. It removes only NeuroFlow launchers that point to the
specified environment; the site profile, job history, datasets, derivatives,
shared dependencies, and other NeuroFlow versions are kept.
