# NeuroFlow

NeuroFlow is a local CLI and browser-based controller for BIDS-compatible
anatomical MRI, multi-echo BOLD, and diffusion MRI workflows. It can also
inventory DICOM exports and convert reviewed series mappings to BIDS. Existing
BIDS datasets can be processed directly; DICOM conversion is optional.

## Install

For a source checkout, use Python 3.11 or newer in a per-user virtual environment:

```bash
git clone https://github.com/YaoMeng94/NeuroFlow.git
cd NeuroFlow
python3 -m venv ~/.venvs/neuroflow
~/.venvs/neuroflow/bin/pip install '.[pipeline]'
~/.venvs/neuroflow/bin/neuroflow init-site
```

For versioned offline bundles, use the matching assets from a NeuroFlow GitHub
Release. Verify each archive against its adjacent checksum before extraction.

The controller and pipeline launchers are packaged together. Processing
software remains an external dependency: install compatible host tools and
container images, then set their paths in `~/.config/neuroflow/site.json`.
NeuroFlow does not download containers or require administrator privileges.
Each user should have their own environment, site profile, job state, and
writable derivatives/scratch locations.

## Run

Start the local interface:

```bash
~/.venvs/neuroflow/bin/neuroflow serve --port 8765
```

Open `http://127.0.0.1:8765`. On a remote HPC, bind locally and use an SSH
tunnel; do not expose the service on a shared network interface:

```bash
ssh -N -L 8765:127.0.0.1:8765 user@hpc-host
```

Choose Anatomy, Multi-echo BOLD, or Diffusion, set a BIDS input directory and a
separate derivatives directory, select one or more stages, and review the
planned command before starting. A preflight-only run checks inputs without
launching FreeSurfer, fMRIPrep, tedana, QSIPrep, or QSIRecon. DICOM inventory
and conversion are separate jobs so series mappings can be reviewed before
conversion.

The CLI uses the same planner:

```bash
neuroflow --help
neuroflow doctor --workflow all
neuroflow plan job.json
neuroflow run job.json
neuroflow jobs
```

`doctor` checks required paths against the current site profile without
launching processing. It does not execute or version-check container images.
Use `--workflow anat|bold|dwi` to check one workflow; add `--engine nextflow`
to validate the Nextflow and Java configuration too.

## Offline Install

The Linux offline bundle includes a platform- and Python-version-specific
wheelhouse with the pipeline Python dependencies. Verify its checksum manifest
before installation:

```bash
sha256sum -c SHA256SUMS
```

For a controller-only or different Python/platform wheelhouse, use
`code/scripts/build_hpc_wheelhouse.sh` on a machine with package-index access.
Install offline using `code/scripts/install_neuroflow_hpc.sh`. Wheelhouses are
platform- and Python-version-specific; do not use this bundle for an
incompatible target.

On Linux x86_64 with Python 3.11, the user-level installer wraps the offline
installer, defaults to the wheelhouse shipped in this project, and adds
commands under `~/.local/bin` without sudo:

```bash
bash code/scripts/install_neuroflow_linux.sh
~/.local/bin/neuroflow-ui
```

Pass `--wheelhouse DIR` if the bundle was downloaded separately, and
`--venv ABSOLUTE_PATH` to choose a different environment. The installer creates
a site-profile template but does not install scientific tools or Apptainer
images. On an HPC, start `neuroflow-ui` in the user's account and use an SSH
tunnel from the workstation to access the loopback-only browser UI. Use
`neuroflow-dicom-sorter` on a Linux desktop with Tk available; headless HPC
sessions should use the browser sorter instead.

The versioned Linux/HPC release bundle is built with
`code/scripts/package_neuroflow_linux.sh`; see
`docs/INSTALL_LINUX_HPC.md` for the end-user install steps.

For the Windows controller bundle and per-user Start Menu launcher, see
`docs/INSTALL_WINDOWS.md`. It uses an offline Python wheelhouse and requires
64-bit CPython 3.11; the DICOM sorter remains a separate standalone installer.
The existing `neuroflow-v0.4.14` tag and unpublished draft release refer to
older source at `d258c08`; those assets do not include the latest converter
workflow changes. The `deployment-acceptance` branch carries the distinct
`0.4.15` candidate. Linux/HPC acceptance passed on the node-1 glibc 2.17 test
account, including install, upgrade, uninstall, DICOM/BIDS preflights, and
BIDS-only planning. The candidate's native platform artifacts are generated
by the branch build workflow and remain Actions artifacts, not release assets.
See the [deployment-acceptance build runs](https://github.com/YaoMeng94/NeuroFlow/actions/workflows/build-dicom-sorter.yml?query=branch%3Adeployment-acceptance)
and [test runs](https://github.com/YaoMeng94/NeuroFlow/actions/workflows/test-neuroflow.yml?query=branch%3Adeployment-acceptance).
No release tag or draft was created for 0.4.15. See
`docs/INSTALL_MACOS.md`, `docs/INSTALL_WINDOWS.md`, and
`docs/DICOM_SORTER_START_HERE.md` for platform-specific acceptance status.
Public macOS distribution remains unavailable until Developer ID signing and
notarization are configured. The 0.4.14 draft must not be presented as the
0.4.15 candidate.

## Site Configuration

`neuroflow init-site` creates a per-user JSON profile. Configure only the tools
and images required by selected workflows, such as FreeSurfer, Workbench,
newMSM, fMRIPrep, tedana, QSIPrep, QSIRecon, Nextflow, and DICOM conversion
utilities. Use absolute paths readable/executable by the user. External atlas
datasets can be placed in a shared read-only location; the Glasser360
BIDS-Atlas resource in `resources/atlases/Glasser360/` is included.

For HPC use, administrators may provide versioned images and shared reference
assets in read-only locations. Users still need read access to BIDS inputs and
write access/quota for outputs, scratch, TemplateFlow cache, and their own
NeuroFlow job-state directory.

## DICOM Notes

The bundled DICOM sorter preset and dcm2bids configuration are starting points
for the Siemens Prisma protocol used during development, not universal
mappings. Review or replace them for each scanner/protocol before conversion.
The sorter preserves the source export and copies selected series; conversion
must be validated as a BIDS dataset before downstream processing.

## Development Checks

Create an isolated development environment and install the declared test and
pipeline dependencies before running the checks:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test,pipeline]'
python -m pip wheel --no-deps . -w /tmp/neuroflow-wheel
PYTHONPATH=code python -m unittest discover -s code/neuroflow/tests -v
bash code/pipelines/anat/test_anat_bidsapp.sh
bash code/pipelines/bold/test_bold_stage_dependencies.sh
bash code/pipelines/bold/test_bold_freesurfer_policy.sh
python code/pipelines/dwi/test_dwi_pipeline.py
python code/pipelines/dwi/test_verify_dwi_metrics.py
bash code/scripts/test_build_hpc_wheelhouse.sh
```

On Linux x86_64 with CPython 3.11, the offline installer can also be tested
against a complete wheelhouse. The test intentionally creates an incomplete
wheelhouse to verify that a failed install leaves no partial environment:

```bash
bash code/scripts/test_install_neuroflow_hpc.sh \
  dist/hpc-wheelhouses/neuroflow-0.4.12-pipeline
```

These checks cover planning, packaging, and launcher behavior; they do not
replace visual QC or an approved pilot on representative data.
