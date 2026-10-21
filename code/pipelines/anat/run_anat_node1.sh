#!/usr/bin/env bash
set -Eeuo pipefail

# FreeSurfer 8 anatomy stage for the Prisma test project.
# The runner is deliberately staged so later modalities can consume the
# verified FreeSurfer and fsLR outputs without rerunning reconstruction.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'USAGE'
Usage:
  run_anat_node1.sh [options]

Stages:
  all       Run recon-all and fsLR export; use T2 when a suitable scan exists
  recon     Run or resume the T1w/T2 FreeSurfer reconstruction
  t2        Add T2-assisted pial refinement to an existing T1-only subject
  fslr      Export the verified surfaces and metrics to fsLR 32k with MSM-Sulc

Options:
  --subject ID              BIDS subject label, e.g. sub-TEST01
  --bids-root PATH          BIDS dataset root
  --project-root PATH       Project root containing derivatives and logs
  --t1 PATH                 Primary T1w NIfTI (default: BIDS MPRAGE)
  --t2 PATH                 T2w NIfTI for pial refinement (default: BIDS SPC)
  --stage STAGE              all|recon|t2|fslr
  --threads N               FreeSurfer OpenMP threads (default: min(16,nproc))
  --atlas-root PATH         standard_mesh_atlases root for fsLR export
  --msm-config PATH         Override bundled newMSM MSMSulc configuration
  --newmsm PATH             newMSM executable
  --freesurfer-home PATH    FreeSurfer installation root
  --fs-license-file PATH    FreeSurfer license path
  --msm-threads N           Threads per hemisphere, default 4
  --subjects-dir PATH       FreeSurfer SUBJECTS_DIR
  --fsLR-root PATH          fsLR derivative output root
  --log-file PATH           Log file (default: project logs)
  --dry-run                 Print commands without executing them
  -h, --help                Show this help

The script is intended to run on node-1. Existing stages are skipped using
completion markers; an interrupted recon-all can be resumed safely.
USAGE
}

SUBJECT=""
BIDS_ROOT=""
PROJECT_ROOT=""
STAGE="all"
THREADS=""
ATLAS_ROOT=""
MSM_CONFIG=""
NEWMSM="${PRISMA_NEWMSM:-newmsm}"
WB_COMMAND="${PRISMA_WB_COMMAND:-wb_command}"
QC_PYTHON="${PRISMA_QC_PYTHON:-python3}"
MSM_THREADS=4
SUBJECTS_DIR=""
FSLR_ROOT=""
T1=""
T2=""
LOG_FILE=""
DRY_RUN=0
FS_HOME="${FREESURFER_HOME:-}"
FS_LICENSE_FILE=""

while (($#)); do
    case "$1" in
        --subject) SUBJECT="$2"; shift 2 ;;
        --bids-root) BIDS_ROOT="$2"; shift 2 ;;
        --project-root) PROJECT_ROOT="$2"; shift 2 ;;
        --t1) T1="$2"; shift 2 ;;
        --t2) T2="$2"; shift 2 ;;
        --stage) STAGE="$2"; shift 2 ;;
        --threads) THREADS="$2"; shift 2 ;;
        --atlas-root) ATLAS_ROOT="$2"; shift 2 ;;
        --wb-command) WB_COMMAND="$2"; shift 2 ;;
        --qc-python) QC_PYTHON="$2"; shift 2 ;;
        --msm-config) MSM_CONFIG="$2"; shift 2 ;;
        --newmsm) NEWMSM="$2"; shift 2 ;;
        --freesurfer-home) FS_HOME="$2"; shift 2 ;;
        --fs-license-file) FS_LICENSE_FILE="$2"; shift 2 ;;
        --msm-threads) MSM_THREADS="$2"; shift 2 ;;
        --subjects-dir) SUBJECTS_DIR="$2"; shift 2 ;;
        --fsLR-root) FSLR_ROOT="$2"; shift 2 ;;
        --log-file) LOG_FILE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$STAGE" in
    all|recon|t2|fslr) ;;
    *) echo "Invalid stage: $STAGE" >&2; exit 2 ;;
esac

[[ -n "$SUBJECT" ]] || { echo "Set --subject to a BIDS subject label" >&2; exit 2; }
[[ -n "$BIDS_ROOT" && -d "$BIDS_ROOT" ]] || { echo "Set --bids-root to an existing BIDS dataset" >&2; exit 2; }
[[ -n "$PROJECT_ROOT" ]] || { echo "Set --project-root to a writable work directory" >&2; exit 2; }
[[ -n "$FS_HOME" && -d "$FS_HOME" ]] || { echo "Set --freesurfer-home or FREESURFER_HOME" >&2; exit 2; }
if [[ "$STAGE" == all || "$STAGE" == fslr ]]; then
    [[ -n "$ATLAS_ROOT" && -d "$ATLAS_ROOT" ]] || { echo "Set --atlas-root to HCP standard_mesh_atlases" >&2; exit 2; }
    command -v "$WB_COMMAND" >/dev/null 2>&1 || { echo "Workbench not found: $WB_COMMAND" >&2; exit 2; }
    command -v "$QC_PYTHON" >/dev/null 2>&1 || { echo "QC Python not found: $QC_PYTHON" >&2; exit 2; }
fi

if [[ -z "$SUBJECTS_DIR" ]]; then
    SUBJECTS_DIR="$PROJECT_ROOT/derivatives/freesurfer8/subjects"
fi
if [[ -z "$FSLR_ROOT" ]]; then
    FSLR_ROOT="$PROJECT_ROOT/derivatives/anat_fsLR"
fi
if [[ -z "$T1" ]]; then
    T1="$BIDS_ROOT/$SUBJECT/anat/${SUBJECT}_acq-mprage_T1w.nii.gz"
fi
if [[ -z "$T2" ]]; then
    T2="$BIDS_ROOT/$SUBJECT/anat/${SUBJECT}_acq-spc_T2w.nii.gz"
fi
if [[ -z "$LOG_FILE" ]]; then
    LOG_FILE="$PROJECT_ROOT/logs/${SUBJECT}_anat_${STAGE}.log"
fi

mkdir -p "$SUBJECTS_DIR" "$FSLR_ROOT" "$PROJECT_ROOT/logs"
SUBJECTS_DIR="$(cd "$SUBJECTS_DIR" && pwd -P)"
FSLR_ROOT="$(cd "$FSLR_ROOT" && pwd -P)"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd -P)"
T1="$(cd "$(dirname "$T1")" && pwd -P)/$(basename "$T1")"
T2="$(cd "$(dirname "$T2")" && pwd -P)/$(basename "$T2")"

FS_SUBJECT="$SUBJECTS_DIR/$SUBJECT"
RECON_DONE="$FS_SUBJECT/.recon_complete"
T2_DONE="$FS_SUBJECT/.t2_pial_complete"
FSLR_STAGE_DIR="$(dirname "$FSLR_ROOT")/.work_$(basename "$FSLR_ROOT")_${SUBJECT}_32k"
FSLR_DONE="$FSLR_STAGE_DIR/.fslr32k_MSMSulc_complete"

if [[ -z "$THREADS" ]]; then
    NPROC="$(nproc 2>/dev/null || printf '1')"
    if ((NPROC > 16)); then THREADS=16; else THREADS="$NPROC"; fi
fi
if ! [[ "$THREADS" =~ ^[1-9][0-9]*$ ]]; then
    echo "--threads must be a positive integer: $THREADS" >&2
    exit 2
fi

mkdir -p "$(dirname "$LOG_FILE")" "$SUBJECTS_DIR" "$FSLR_ROOT"
if ((DRY_RUN == 0)); then
    exec > >(tee -a "$LOG_FILE") 2>&1
fi

run() {
    printf '\n+ $'
    printf ' %q' "$@"
    printf '\n'
    if ((DRY_RUN == 0)); then "$@"; fi
}

echo "Subject       : $SUBJECT"
echo "Stage         : $STAGE"
echo "Primary T1w   : $T1"
echo "T2w           : $T2"
echo "Subjects dir  : $SUBJECTS_DIR"
echo "fsLR output   : $FSLR_ROOT"
echo "Threads       : $THREADS"
echo "FreeSurfer    : $FS_HOME"

[[ -d "$FS_HOME" ]] || { echo "Missing FreeSurfer: $FS_HOME" >&2; exit 1; }
FS_LICENSE_FILE="${FS_LICENSE_FILE:-$FS_HOME/license.txt}"
[[ -f "$FS_LICENSE_FILE" ]] || { echo "Missing FreeSurfer license: $FS_LICENSE_FILE" >&2; exit 1; }
[[ -f "$T1" ]] || { echo "Missing T1w input: $T1" >&2; exit 1; }
if [[ "$STAGE" == t2 ]]; then
    [[ -f "$T2" ]] || { echo "Stage $STAGE requires T2w input: $T2" >&2; exit 1; }
fi

export FREESURFER_HOME="$FS_HOME"
export FS_LICENSE="$FS_LICENSE_FILE"
export SUBJECTS_DIR
export PATH="$FS_HOME/bin:$(dirname "$(command -v "$WB_COMMAND" 2>/dev/null || printf '%s' "$WB_COMMAND")"):$PATH"

# FreeSurfer's setup script references optional variables that may be unset;
# keep nounset enabled for this runner while allowing the vendor script to load.
set +e +u
source "$FS_HOME/SetUpFreeSurfer.sh" >/dev/null
set -e -u
command -v recon-all >/dev/null || { echo "recon-all is unavailable" >&2; exit 1; }

mkdir -p "$SUBJECTS_DIR" "$FSLR_ROOT"
exec 9>"$PROJECT_ROOT/logs/${SUBJECT}.anat.lock"
if ! flock -n 9; then
    echo "Anatomy job is already running for $SUBJECT" >&2
    exit 3
fi

run_recon() {
    if [[ -f "$RECON_DONE" ]]; then
        echo "Primary recon-all already completed: $RECON_DONE"
        return
    fi
    local resume_flag=""
    local running_file="$FS_SUBJECT/scripts/IsRunning.lh+rh"
    if [[ -f "$running_file" ]]; then
        local recorded_pid
        recorded_pid="$(awk '$1 == "PROCESSID" {print $2; exit}' "$running_file")"
        if [[ -z "$recorded_pid" || ! "$recorded_pid" =~ ^[0-9]+$ ]]; then
            echo "Cannot verify FreeSurfer running marker owner: $running_file" >&2
            exit 1
        fi
        if kill -0 "$recorded_pid" 2>/dev/null; then
            echo "FreeSurfer reconstruction is already active (PID $recorded_pid)" >&2
            exit 3
        fi
        echo "Resuming after stale FreeSurfer running marker (PID $recorded_pid)"
        resume_flag="-no-isrunning"
    fi
    local cmd=(recon-all -sd "$SUBJECTS_DIR" -s "$SUBJECT")
    if [[ ! -f "$FS_SUBJECT/mri/orig/001.mgz" ]]; then cmd+=(-i "$T1"); fi
    if [[ -f "$T2" ]]; then cmd+=(-T2 "$T2" -T2pial); fi
    cmd+=(-all -parallel -openmp "$THREADS")
    if [[ -n "$resume_flag" ]]; then cmd+=("$resume_flag"); fi
    run "${cmd[@]}"
    if ((DRY_RUN == 0)); then
        [[ -f "$FS_SUBJECT/surf/lh.pial" && -f "$FS_SUBJECT/surf/rh.pial" ]] || {
            echo "FreeSurfer completed without both pial surfaces" >&2; exit 1;
        }
        touch "$RECON_DONE"
        if [[ -f "$T2" ]]; then touch "$T2_DONE"; fi
    fi
}

run_t2() {
    if ((DRY_RUN == 0)); then
        [[ -f "$RECON_DONE" || ( -f "$FS_SUBJECT/surf/lh.pial" && -f "$FS_SUBJECT/surf/rh.pial" ) ]] || {
            echo "T2 stage requires a completed primary recon-all" >&2; exit 1;
        }
    fi
    if [[ -f "$T2_DONE" ]]; then
        echo "T2 pial refinement already completed: $T2_DONE"
        return
    fi
    run recon-all -sd "$SUBJECTS_DIR" -s "$SUBJECT" -T2 "$T2" -T2pial -autorecon3 -parallel -openmp "$THREADS"
    if ((DRY_RUN == 0)); then
        [[ -f "$FS_SUBJECT/surf/lh.pial" && -f "$FS_SUBJECT/surf/rh.pial" ]] || {
            echo "T2 stage completed without both pial surfaces" >&2; exit 1;
        }
        touch "$T2_DONE"
    fi
}

run_fslr() {
    if ((DRY_RUN == 0)); then
    [[ -f "$RECON_DONE" || ( -f "$FS_SUBJECT/surf/lh.pial" && -f "$FS_SUBJECT/surf/rh.pial" ) ]] || {
        echo "fsLR stage requires completed recon-all surfaces" >&2; exit 1;
    }
    fi
    if [[ -f "$FSLR_DONE" && -f "$FSLR_ROOT/$SUBJECT/anat/${SUBJECT}_hemi-L_space-fsLR_den-32k_thickness.shape.gii" ]]; then
        echo "fsLR 32k export already completed: $FSLR_DONE"
        return
    fi
    [[ -f "$SCRIPT_DIR/export_fsLR32k_node1.sh" ]] || {
        echo "Missing pipeline-local fsLR exporter: $SCRIPT_DIR/export_fsLR32k_node1.sh" >&2; exit 1;
    }
    local msm_config_args=()
    [[ -n "$MSM_CONFIG" ]] && msm_config_args=(--msm-config "$MSM_CONFIG")
    run bash "$SCRIPT_DIR/export_fsLR32k_node1.sh" \
        --subject "$SUBJECT" --subjects-dir "$SUBJECTS_DIR" \
        --output-root "$FSLR_ROOT" --atlas-root "$ATLAS_ROOT" \
        --source-t1 "$FS_SUBJECT/mri/brain.mgz" --source-t2 "$FS_SUBJECT/mri/T2.mgz" \
        --newmsm "$NEWMSM" --msm-threads "$MSM_THREADS" \
        --wb-command "$WB_COMMAND" --qc-python "$QC_PYTHON" \
        --density 32k --jobs 2 "${msm_config_args[@]}"
    if ((DRY_RUN == 0)); then
        [[ -f "$FSLR_ROOT/$SUBJECT/anat/${SUBJECT}_hemi-L_space-fsLR_den-32k_midthickness.surf.gii" ]] || {
            echo "fsLR export did not create the left midthickness surface" >&2; exit 1;
        }
        [[ -f "$FSLR_ROOT/$SUBJECT/anat/${SUBJECT}_hemi-L_space-fsLR_den-32k_thickness.shape.gii" ]] || {
            echo "fsLR export did not create the left thickness metric" >&2; exit 1;
        }
        if [[ -f "$T2" ]]; then
            [[ -f "$FSLR_ROOT/$SUBJECT/anat/${SUBJECT}_hemi-R_space-fsLR_den-32k_desc-T1wT2w.shape.gii" ]] || {
                echo "fsLR export did not create the right T1w/T2w metric" >&2; exit 1;
            }
        else
            [[ -f "$FSLR_ROOT/$SUBJECT/anat/${SUBJECT}_hemi-R_space-fsLR_den-32k_desc-T1w.shape.gii" ]] || {
                echo "fsLR export did not create the right T1w metric" >&2; exit 1;
            }
        fi
        touch "$FSLR_DONE"
    fi
}

case "$STAGE" in
    recon) run_recon ;;
    t2) run_t2 ;;
    fslr) run_fslr ;;
    all)
        run_recon
        if [[ -f "$T2" ]]; then
            run_t2
        else
            echo "No suitable T2w found; skipping T2-assisted pial refinement and T1w/T2w metrics"
        fi
        run_fslr
        ;;
esac

echo "Anatomy stage finished: $STAGE"
