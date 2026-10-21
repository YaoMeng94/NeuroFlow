#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="all"
QSIPREP_IMAGE="${PRISMA_QSIPREP_IMAGE:-}"
QSIRECON_IMAGE="${PRISMA_QSIRECON_IMAGE:-}"
QSIPREP_DWI_MERGE_PATCH="${PRISMA_QSIPREP_DWI_MERGE_PATCH:-}"
APPTAINER="${PRISMA_APPTAINER:-apptainer}"
FS_SUBJECTS_DIR=""
FS_LICENSE=""
PROCESSING_BIDS_DIR=""
WORK_DIR=""
DIRECT_BIDS_INPUT=0
BIDS_FILTER_FILE=""
TEMPLATEFLOW_HOME="${TEMPLATEFLOW_HOME:-$HOME/.cache/templateflow}"
THREADS=""
OMP_THREADS=""
MEM_GB=""
OUTPUT_RESOLUTION="1.5"
SDC_METHOD="topup"
B0_POLICY="auto"
DWI_B0_SDC="auto"
DWI_B0_SOURCE=""
GEOMETRY_POLICY="auto"
DENOISE_METHOD="dwidenoise"
UNRINGING_METHOD="mrdegibbs"
RECON_SPEC="auto"
DISTORTION_GROUP_MERGE="concat"
SEPARATE_ALL_DWIS=0
GPU_MODE="auto"
EDDY_CONFIG=""
# Keep the default executable with the verified project resource. Additional
# atlases can be selected explicitly with repeatable --atlases/--atlas-dataset.
ATLASES=(Brainnetome246Ext Glasser360)
ATLAS_DATASETS=()
PARTICIPANTS=()
SESSIONS=()
DRY_RUN=0
SKIP_BIDS_VALIDATION=0

is_builtin_qsirecon_atlas() {
    case "$1" in
        AAL116|AICHA384Ext|Brainnetome246Ext|Gordon333Ext|4S156Parcels|4S256Parcels|4S456Parcels|4S556Parcels|4S856Parcels)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

usage() {
    cat <<'EOF'
Usage: run_dwi_bidsapp.sh <bids_dir> <output_dir> participant [options]

The output_dir is a derivatives parent. The app writes sibling qsiprep,
qsirecon, and dwiprep derivative datasets.

Stages: preflight, qsiprep, qsirecon, metrics, connectome, verify, all

Options:
  --participant-label LABEL [LABEL ...]
  --session-id SESSION [SESSION ...]
  --stage STAGE              preflight, qsiprep, qsirecon, metrics, connectome, verify, or all
  --qsiprep-image PATH
  --qsirecon-image PATH
  --fs-subjects-dir PATH    existing FreeSurfer subjects directory
  --fs-license-file PATH
  --processing-bids-dir PATH processing-only BIDS copy (default: work/bids_input)
  --direct-bids-input        pass the source BIDS tree directly to QSIPrep
  --bids-filter-file PATH    optional QSIPrep JSON filter for selected inputs
  --templateflow-home PATH
  --threads N
  --nprocs N                alias for --threads
  --omp-nthreads N
  --mem-gb N
  --mem-mb N                standard memory limit in MiB
  --eddy-config PATH       QSIPrep Eddy JSON configuration
  --work-dir PATH
  --output-resolution MM
  --sdc-method METHOD       topup, drbuddi, topup+drbuddi, or auto
  --b0-policy POLICY        auto, include, or exclude b0-only *_dwi inputs
  --dwi-b0-sdc MODE         auto or none; use reverse-PE b0 as DWI TOPUP fmap
  --dwi-b0-source PATH      optional source-relative b0-only DWI to use for TOPUP
  --geometry-policy POLICY auto, preserve, or resample differing DWI grids
  --denoise-method METHOD   dwidenoise, patch2self, or none
  --unringing-method METHOD mrdegibbs, rpg, or none
  --recon-spec SPEC          auto, metrics-noddi, metrics-dki-noddi, preset, or YAML path
  --distortion-group-merge MODE  concat (default), average, or none
  --separate-all-dwis        explicitly process acquisitions independently
  --atlases ATLAS [ATLAS ...]
  --atlas-dataset NAME=PATH  BIDS-Atlas dataset for QSIRecon (repeatable)
  --no-atlases               skip QSIRecon connectome atlas collection
  --gpu MODE                 auto, none, eddy, diffprep, drbuddi, or all
  --skip-bids-validation
  --dry-run
EOF
}

[[ $# -ge 3 ]] || { usage >&2; exit 2; }
BIDS_ROOT="$1"; OUTPUT_ROOT="$2"; ANALYSIS_LEVEL="$3"; shift 3
[[ "$ANALYSIS_LEVEL" == "participant" ]] || { echo "Only participant analysis is supported" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --participant-label) shift; while [[ $# -gt 0 && "$1" != --* ]]; do PARTICIPANTS+=("$1"); shift; done ;;
        --session-id) shift; while [[ $# -gt 0 && "$1" != --* ]]; do SESSIONS+=("$1"); shift; done ;;
        --stage) STAGE="$2"; shift 2 ;;
        --qsiprep-image) QSIPREP_IMAGE="$2"; shift 2 ;;
        --qsirecon-image) QSIRECON_IMAGE="$2"; shift 2 ;;
        --fs-subjects-dir) FS_SUBJECTS_DIR="$2"; shift 2 ;;
        --fs-license-file) FS_LICENSE="$2"; shift 2 ;;
        --processing-bids-dir) PROCESSING_BIDS_DIR="$2"; shift 2 ;;
        --direct-bids-input) DIRECT_BIDS_INPUT=1; shift ;;
        --bids-filter-file) BIDS_FILTER_FILE="$2"; shift 2 ;;
        --templateflow-home) TEMPLATEFLOW_HOME="$2"; shift 2 ;;
        --threads) THREADS="$2"; shift 2 ;;
        --nprocs) THREADS="$2"; shift 2 ;;
        --omp-nthreads) OMP_THREADS="$2"; shift 2 ;;
        --mem-gb) MEM_GB="$2"; shift 2 ;;
        --mem-mb) MEM_GB="$((($2 + 1023) / 1024))"; shift 2 ;;
        --eddy-config) EDDY_CONFIG="$2"; shift 2 ;;
        --work-dir) WORK_DIR="$2"; shift 2 ;;
        --output-resolution) OUTPUT_RESOLUTION="$2"; shift 2 ;;
        --sdc-method) SDC_METHOD="$2"; shift 2 ;;
        --b0-policy) B0_POLICY="$2"; shift 2 ;;
        --dwi-b0-sdc) DWI_B0_SDC="$2"; shift 2 ;;
        --dwi-b0-source) DWI_B0_SOURCE="$2"; shift 2 ;;
        --geometry-policy) GEOMETRY_POLICY="$2"; shift 2 ;;
        --denoise-method) DENOISE_METHOD="$2"; shift 2 ;;
        --unringing-method) UNRINGING_METHOD="$2"; shift 2 ;;
        --recon-spec) RECON_SPEC="$2"; shift 2 ;;
        --distortion-group-merge) DISTORTION_GROUP_MERGE="$2"; shift 2 ;;
        --separate-all-dwis) SEPARATE_ALL_DWIS=1; shift ;;
        --atlases) shift; ATLASES=(); while [[ $# -gt 0 && "$1" != --* ]]; do ATLASES+=("$1"); shift; done ;;
        --atlas-dataset) ATLAS_DATASETS+=("$2"); shift 2 ;;
        --no-atlases) ATLASES=(); shift ;;
        --gpu) GPU_MODE="$2"; shift 2 ;;
        --skip-bids-validation) SKIP_BIDS_VALIDATION=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if printf '%s\n' "${ATLASES[@]}" | grep -Fxq Glasser360 \
    && { ((${#ATLAS_DATASETS[@]} == 0)) \
        || ! printf '%s\n' "${ATLAS_DATASETS[@]}" | sed 's/=.*//' | grep -Fxq Glasser360; }; then
    bundled_glasser="$SCRIPT_DIR/../../../resources/atlases/Glasser360"
    if [[ -d "$bundled_glasser" ]]; then
        ATLAS_DATASETS+=("Glasser360=$bundled_glasser")
    fi
fi

case "$STAGE" in preflight|qsiprep|qsirecon|metrics|connectome|verify|all) ;; *) echo "Invalid --stage: $STAGE" >&2; exit 2 ;; esac
case "$DWI_B0_SDC" in auto|none) ;; *) echo "Invalid --dwi-b0-sdc: $DWI_B0_SDC" >&2; exit 2 ;; esac
[[ -d "$BIDS_ROOT" ]] || { echo "BIDS directory not found: $BIDS_ROOT" >&2; exit 2; }
BIDS_ROOT="$(cd "$BIDS_ROOT" && pwd -P)"
mkdir -p "$OUTPUT_ROOT"
OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd -P)"
WORK_DIR="${WORK_DIR:-$OUTPUT_ROOT/.work/dwiprep}"
if [[ -n "$WORK_DIR" ]]; then mkdir -p "$WORK_DIR"; fi
for path_var in FS_SUBJECTS_DIR FS_LICENSE PROCESSING_BIDS_DIR BIDS_FILTER_FILE EDDY_CONFIG QSIPREP_DWI_MERGE_PATCH; do
    path="${!path_var}"
    if [[ -n "$path" ]]; then
        printf -v "$path_var" '%s/%s' "$(cd "$(dirname "$path")" && pwd -P)" "$(basename "$path")"
    fi
done
if [[ -n "$DWI_B0_SOURCE" && "$DWI_B0_SOURCE" != /* ]]; then
    DWI_B0_SOURCE="$BIDS_ROOT/$DWI_B0_SOURCE"
fi
if [[ -z "$QSIPREP_DWI_MERGE_PATCH" && "$(basename "$QSIPREP_IMAGE")" =~ ^qsiprep_26([._-]|$) ]]; then
    bundled_merge_patch="$SCRIPT_DIR/qsiprep26_dwi_merge_patched.py"
    if [[ -f "$bundled_merge_patch" ]]; then QSIPREP_DWI_MERGE_PATCH="$bundled_merge_patch"; fi
fi
if [[ -n "$QSIPREP_DWI_MERGE_PATCH" ]]; then
    [[ -f "$QSIPREP_DWI_MERGE_PATCH" ]] || {
        ((DRY_RUN)) || { echo "QSIPrep DWI merge patch not found: $QSIPREP_DWI_MERGE_PATCH" >&2; exit 1; }
    }
fi

# Prevent two stages from writing the same derivative tree concurrently. The
# lock path is stable, and the fallback directory records the owning process
# while it is active on systems without flock.
mkdir -p "$OUTPUT_ROOT"
LOCK_FILE="$OUTPUT_ROOT/.dwiprep.pipeline.lock"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "Another DWI pipeline process is using output root: $OUTPUT_ROOT" >&2
        exit 75
    fi
else
    LOCK_DIR="$OUTPUT_ROOT/.dwiprep.pipeline.lock.d"
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        echo "Another DWI pipeline process is using output root: $OUTPUT_ROOT" >&2
        exit 75
    fi
    printf 'pid=%s\nhost=%s\n' "$$" "$(hostname 2>/dev/null || printf unknown)" > "$LOCK_DIR/owner"
    trap 'rm -f "$LOCK_DIR/owner" 2>/dev/null || true; rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
fi
if ((DIRECT_BIDS_INPUT)) && [[ "$DWI_B0_SDC" == "auto" ]] && [[ "$STAGE" == "qsiprep" || "$STAGE" == "all" ]]; then
    echo "--direct-bids-input cannot promote reverse-PE b0 files; use a processing copy or set --dwi-b0-sdc none" >&2
    exit 2
fi

if [[ -z "$THREADS" ]]; then THREADS="$(nproc 2>/dev/null || printf '8')"; ((THREADS > 32)) && THREADS=32; fi
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] && ((THREADS <= 50)) || { echo "--nprocs/--threads must be between 1 and 50" >&2; exit 2; }
if [[ -z "$OMP_THREADS" ]]; then OMP_THREADS=$((THREADS > 16 ? 16 : THREADS)); fi
[[ "$OMP_THREADS" =~ ^[1-9][0-9]*$ ]] && ((OMP_THREADS <= THREADS)) || { echo "--omp-nthreads must be between 1 and --nprocs" >&2; exit 2; }
if [[ -z "$MEM_GB" ]]; then MEM_GB=64; fi
if [[ -z "$FS_SUBJECTS_DIR" && -d "$OUTPUT_ROOT/freesurfer/subjects" ]]; then FS_SUBJECTS_DIR="$OUTPUT_ROOT/freesurfer/subjects"; fi

container_has_eddy_cuda() {
    # A host GPU is not enough: QSIPrep must contain a CUDA eddy executable.
    if [[ ! -f "$QSIPREP_IMAGE" ]]; then
        ((DRY_RUN)) && return 0
        return 1
    fi
    "$APPTAINER" exec --nv "$QSIPREP_IMAGE" sh -lc \
        'for path_dir in $(printf "%s" "$PATH" | tr ":" " "); do
            for eddy in "$path_dir"/eddy_cuda*; do
                [ -x "$eddy" ] || continue
                exit 0
            done
        done
        exit 1'
}

if [[ "$GPU_MODE" == "auto" ]]; then
    if command -v nvidia-smi >/dev/null 2>&1 \
        && nvidia-smi -L >/dev/null 2>&1 \
        && container_has_eddy_cuda; then
        GPU_MODE=eddy
    else
        GPU_MODE=none
    fi
fi
case "$GPU_MODE" in none|eddy|diffprep|drbuddi|all) ;; *) echo "Invalid --gpu mode: $GPU_MODE" >&2; exit 2 ;; esac
if [[ "$GPU_MODE" != "none" ]]; then
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        echo "WARNING: GPU mode '$GPU_MODE' requested but nvidia-smi is unavailable; falling back to CPU mode" >&2
        GPU_MODE=none
    elif ! nvidia-smi -L >/dev/null 2>&1; then
        echo "WARNING: GPU mode '$GPU_MODE' requested but the NVIDIA driver is unavailable; falling back to CPU mode" >&2
        GPU_MODE=none
    elif [[ "$GPU_MODE" == "eddy" || "$GPU_MODE" == "all" ]] && ! container_has_eddy_cuda; then
        echo "WARNING: GPU mode '$GPU_MODE' requested but the QSIPrep image has no runnable eddy_cuda; falling back to CPU mode" >&2
        GPU_MODE=none
    fi
fi

# Keep the wrapper's stable spelling while translating to QSIPrep 26's CLI.
case "$SDC_METHOD" in
    topup|auto) PEPOLAR_METHOD="TOPUP" ;;
    drbuddi) PEPOLAR_METHOD="DRBUDDI" ;;
    topup+drbuddi) PEPOLAR_METHOD="TOPUP+DRBUDDI" ;;
    *) echo "Invalid --sdc-method: $SDC_METHOD" >&2; exit 2 ;;
esac

QSIPREP_DIR="$OUTPUT_ROOT/qsiprep"
QSIRECON_DIR="$OUTPUT_ROOT/qsirecon"
DWIPREP_DIR="$OUTPUT_ROOT/dwiprep"
mkdir -p "$WORK_DIR"
WORK_DIR="$(cd "$WORK_DIR" && pwd -P)"
PROCESSING_BIDS_DIR="${PROCESSING_BIDS_DIR:-$WORK_DIR/bids_input}"
LOG_DIR="$DWIPREP_DIR/logs"
MANIFEST_DIR="$DWIPREP_DIR/manifests"
PREFLIGHT_MANIFEST="$MANIFEST_DIR/dwi_preflight.json"
if ((${#PARTICIPANTS[@]} == 1)); then
    PREFLIGHT_MANIFEST="$MANIFEST_DIR/sub-${PARTICIPANTS[0]#sub-}_dwi_preflight.json"
fi
mkdir -p "$QSIPREP_DIR" "$QSIRECON_DIR" "$DWIPREP_DIR" "$WORK_DIR/qsiprep" "$WORK_DIR/qsirecon" "$LOG_DIR" "$MANIFEST_DIR"
if [[ ! -f "$DWIPREP_DIR/dataset_description.json" ]]; then
    cat > "$DWIPREP_DIR/dataset_description.json" <<'EOF'
{
  "Name": "dwiprep orchestration and quality-control derivatives",
  "BIDSVersion": "1.8.0",
  "DatasetType": "derivative",
  "GeneratedBy": [{"Name": "dwiprep", "Version": "0.3.0"}, {"Name": "QSIPrep"}, {"Name": "QSIRecon"}]
}
EOF
fi
mkdir -p "$WORK_DIR/apptainer-tmp" "$WORK_DIR/qsiprep/tmp" "$WORK_DIR/qsirecon/tmp"
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-$WORK_DIR/apptainer-tmp}"

run_logged() {
    local log_file="$1"; shift
    printf '+'; printf ' %q' "$@"; printf '\n'
    if ((DRY_RUN == 0)); then "$@" 2>&1 | tee -a "$log_file"; fi
}

run_preflight() {
    local command=(python3 "$SCRIPT_DIR/preflight_dwi.py" "$BIDS_ROOT" --output-manifest "$PREFLIGHT_MANIFEST")
    if ((${#PARTICIPANTS[@]})); then command+=(--participant-label "${PARTICIPANTS[@]}"); fi
    if ((${#SESSIONS[@]})); then command+=(--session-id "${SESSIONS[@]}"); fi
    run_logged "$LOG_DIR/preflight.log" "${command[@]}"
}

prepare_processing_bids() {
    if ((DIRECT_BIDS_INPUT)); then
        PROCESSING_BIDS_DIR="$BIDS_ROOT"
        echo "Using source BIDS directly: $BIDS_ROOT"
        return 0
    fi
    if ((DRY_RUN)); then
        echo "Would create processing BIDS copy: $PROCESSING_BIDS_DIR"
        PROCESSING_BIDS_DIR="$BIDS_ROOT"
        return 0
    fi
    local command=(python3 "$SCRIPT_DIR/prepare_dwi_input.py" "$BIDS_ROOT" "$PROCESSING_BIDS_DIR" --overwrite --overlay-path "$WORK_DIR/dwi_processing_overlay.json" --b0-policy "$B0_POLICY" --dwi-b0-sdc "$DWI_B0_SDC" --geometry-policy "$GEOMETRY_POLICY")
    if [[ -n "${DWI_B0_SOURCE:-}" ]]; then command+=(--dwi-b0-source "$DWI_B0_SOURCE"); fi
    if ((${#PARTICIPANTS[@]})); then command+=(--participant-label "${PARTICIPANTS[@]}"); fi
    if ((${#SESSIONS[@]})); then command+=(--session-id "${SESSIONS[@]}"); fi
    run_logged "$LOG_DIR/prepare_input.log" "${command[@]}"
}

FS_LICENSE_DIR=""
base_binds=()
container_env=()
container_env+=("--env" "TMPDIR=/work/tmp")

CUDA_EDDY_DIR=""
if [[ "$GPU_MODE" == "eddy" || "$GPU_MODE" == "all" ]]; then
    # QSIPrep 26's ExtendedEddy interface invokes eddy_cuda10.2 when
    # use_cuda=true, while the distributed image currently contains
    # eddy_cuda11.0. Provide a container-local compatibility name without
    # altering the shared image.
    CUDA_EDDY_DIR="$WORK_DIR/eddy-cuda-compat"
    mkdir -p "$CUDA_EDDY_DIR"
    if ((DRY_RUN)); then
        cuda_eddy_target="/app/.pixi/envs/qsiprep/bin/eddy_cuda11.0"
        container_path="/app/.pixi/envs/qsiprep/bin:/opt/art/bin:/usr/local/bin:/usr/bin:/bin"
    else
        cuda_eddy_target="$($APPTAINER exec --nv "$QSIPREP_IMAGE" sh -lc '
            for path_dir in $(printf "%s" "$PATH" | tr ":" " "); do
                for eddy in "$path_dir"/eddy_cuda*; do
                    [ -x "$eddy" ] || continue
                    printf "%s\n" "$eddy"
                    exit 0
                done
            done
            exit 1
        ' 2>/dev/null)" || {
            echo "CUDA Eddy executable could not be resolved inside: $QSIPREP_IMAGE" >&2
            exit 1
        }
        container_path="$($APPTAINER exec "$QSIPREP_IMAGE" sh -lc 'printf "%s" "$PATH"')"
    fi
    printf '#!/bin/sh\nexec "%s" "$@"\n' "$cuda_eddy_target" > "$CUDA_EDDY_DIR/eddy_cuda10.2"
    chmod +x "$CUDA_EDDY_DIR/eddy_cuda10.2"
    base_binds+=("-B" "$CUDA_EDDY_DIR:/cuda_eddy:ro")
    container_env+=("--env" "PATH=/cuda_eddy:$container_path")
    if [[ -z "$EDDY_CONFIG" ]]; then
        EDDY_CONFIG="$WORK_DIR/eddy_params_cuda.json"
        if ((DRY_RUN)); then
            echo "Would set default CUDA Eddy num_threads=$OMP_THREADS in $EDDY_CONFIG"
        else
            python3 "$SCRIPT_DIR/configure_eddy_config.py" \
                "$SCRIPT_DIR/eddy_params_cuda.json" "$EDDY_CONFIG" \
                --num-threads "$OMP_THREADS"
        fi
    fi
fi
if [[ -n "$EDDY_CONFIG" ]]; then
    if [[ ! -f "$EDDY_CONFIG" ]]; then
        ((DRY_RUN)) || { echo "Eddy configuration not found: $EDDY_CONFIG" >&2; exit 1; }
    fi
    EDDY_CONFIG_DIR="$(dirname "$EDDY_CONFIG")"
    EDDY_CONFIG_NAME="$(basename "$EDDY_CONFIG")"
    base_binds+=("-B" "$EDDY_CONFIG_DIR:/eddy_config:ro")
fi
if [[ -n "$FS_LICENSE" ]]; then
    [[ -f "$FS_LICENSE" ]] || { ((DRY_RUN)) || { echo "FreeSurfer license not found: $FS_LICENSE" >&2; exit 1; }; }
    FS_LICENSE_DIR="$(dirname "$FS_LICENSE")"
    base_binds+=("-B" "$FS_LICENSE_DIR:/fs_license:ro")
fi
if [[ -n "$FS_SUBJECTS_DIR" ]]; then
    [[ -d "$FS_SUBJECTS_DIR" ]] || { ((DRY_RUN)) || { echo "FreeSurfer subjects directory not found: $FS_SUBJECTS_DIR" >&2; exit 1; }; }
fi
atlas_binds=()
atlas_dataset_args=()
if ((${#ATLASES[@]})); then
    # QSIRecon 26 bundles several atlases. External datasets are needed only
    # for requested names that are not bundled; preflight and QSIPrep remain
    # runnable without any atlas resources.
    if [[ "$STAGE" == "qsirecon" || "$STAGE" == "all" ]]; then
        for requested_atlas in "${ATLASES[@]}"; do
            if is_builtin_qsirecon_atlas "$requested_atlas"; then
                continue
            fi
            if ((${#ATLAS_DATASETS[@]} == 0)) || ! printf '%s\n' "${ATLAS_DATASETS[@]}" | sed 's/=.*//' | grep -Fxq "$requested_atlas"; then
                ((DRY_RUN)) || { echo "QSIRecon atlas '$requested_atlas' is external; provide --atlas-dataset ${requested_atlas}=PATH or use --no-atlases" >&2; exit 2; }
                echo "WARNING: dry-run has no dataset mount for external atlas: $requested_atlas" >&2
            fi
        done
    fi
fi
if ((${#ATLAS_DATASETS[@]})); then
    for atlas_dataset in "${ATLAS_DATASETS[@]}"; do
        atlas_name="${atlas_dataset%%=*}"
        atlas_path="${atlas_dataset#*=}"
        [[ -n "$atlas_name" && "$atlas_dataset" == *=* && -n "$atlas_path" ]] || {
            echo "Invalid --atlas-dataset (expected NAME=PATH): $atlas_dataset" >&2
            exit 2
        }
        if [[ ! -d "$atlas_path" ]]; then
            ((DRY_RUN)) || { echo "Atlas dataset directory not found: $atlas_path" >&2; exit 1; }
            echo "WARNING: dry-run atlas dataset does not exist yet: $atlas_path" >&2
        fi
        if ((${#ATLASES[@]})) && ! printf '%s\n' "${ATLASES[@]}" | grep -Fxq "$atlas_name"; then
            echo "WARNING: atlas dataset '$atlas_name' is mounted but not requested by --atlases" >&2
        fi
        atlas_binds+=("-B" "$atlas_path:/atlas_datasets/$atlas_name:ro")
        atlas_dataset_args+=("$atlas_name=/atlas_datasets/$atlas_name")
    done
fi
if ((${#ATLASES[@]})) && [[ "$STAGE" == "qsirecon" || "$STAGE" == "all" ]]; then
    for requested_atlas in "${ATLASES[@]}"; do
        if is_builtin_qsirecon_atlas "$requested_atlas"; then
            continue
        fi
        if ((${#ATLAS_DATASETS[@]} == 0)) || ! printf '%s\n' "${ATLAS_DATASETS[@]}" | sed 's/=.*//' | grep -Fxq "$requested_atlas"; then
            ((DRY_RUN)) || { echo "No --atlas-dataset mount provided for requested atlas: $requested_atlas" >&2; exit 2; }
            echo "WARNING: dry-run has no dataset mount for requested atlas: $requested_atlas" >&2
        fi
    done
fi
if [[ -n "$BIDS_FILTER_FILE" ]]; then
    [[ -f "$BIDS_FILTER_FILE" ]] || { ((DRY_RUN)) || { echo "BIDS filter file not found: $BIDS_FILTER_FILE" >&2; exit 1; }; }
fi
if [[ -d "$TEMPLATEFLOW_HOME" ]]; then
    base_binds+=("-B" "$TEMPLATEFLOW_HOME:/templateflow:ro")
    container_env+=("--env" "TEMPLATEFLOW_HOME=/templateflow")
elif ((DRY_RUN == 0)); then
    echo "WARNING: TemplateFlow cache not found: $TEMPLATEFLOW_HOME" >&2
fi

gpu_args=()
if [[ "$GPU_MODE" != "none" ]]; then gpu_args+=(--nv); fi
qsiprep_args=(
    /bids /out participant
    --output-resolution "$OUTPUT_RESOLUTION"
    --pepolar-method "$PEPOLAR_METHOD"
    --denoise-method "$DENOISE_METHOD"
    --unringing-method "$UNRINGING_METHOD"
    --nprocs "$THREADS"
    --omp-nthreads "$OMP_THREADS"
    --mem "$((MEM_GB * 1024))"
    --work-dir /work
    --resource-monitor
    --distortion-group-merge "$DISTORTION_GROUP_MERGE"
)
case "$DISTORTION_GROUP_MERGE" in concat|average|none) ;; *) echo "Invalid distortion group merge mode" >&2; exit 2 ;; esac
if ((SEPARATE_ALL_DWIS)); then qsiprep_args+=(--separate-all-dwis); fi
if [[ -n "$EDDY_CONFIG" ]]; then
    qsiprep_args+=(--eddy-config "/eddy_config/$EDDY_CONFIG_NAME")
fi
qsirecon_args=(
    /qsiprep /out participant
    --input-type qsiprep
    --nprocs "$THREADS"
    --omp-nthreads "$OMP_THREADS"
    --mem "$((MEM_GB * 1024))"
    --work-dir /work
    --resource-monitor
    # ODF plots are optional and the QSIPrep 26 container lacks recon_plot.
    # Keep the core reconstruction, including tractography, runnable without
    # that report-only interface.
    --skip-odf-reports
)
if ((${#ATLASES[@]})); then qsirecon_args+=(--atlases "${ATLASES[@]}"); fi
if ((${#atlas_dataset_args[@]})); then qsirecon_args+=(--datasets "${atlas_dataset_args[@]}"); fi
if [[ -n "$FS_LICENSE" ]]; then
    qsiprep_args+=(--fs-license-file "/fs_license/$(basename "$FS_LICENSE")")
    qsirecon_args+=(--fs-license-file "/fs_license/$(basename "$FS_LICENSE")")
fi
if [[ -n "$FS_SUBJECTS_DIR" ]]; then
    # QSIPrep 26.0.0 does not expose --fs-subjects-dir; QSIRecon does.
    qsirecon_args+=(--fs-subjects-dir /fs_subjects)
fi
if [[ -n "$BIDS_FILTER_FILE" ]]; then
    qsiprep_args+=(--bids-filter-file /bids_filter/$(basename "$BIDS_FILTER_FILE"))
fi
if ((${#PARTICIPANTS[@]})); then
    qsiprep_args+=(--participant-label "${PARTICIPANTS[@]}")
    qsirecon_args+=(--participant-label "${PARTICIPANTS[@]}")
fi
if ((${#SESSIONS[@]})); then
    qsiprep_args+=(--session-id "${SESSIONS[@]}")
    qsirecon_args+=(--session-id "${SESSIONS[@]}")
fi
if ((SKIP_BIDS_VALIDATION)); then qsiprep_args+=(--skip-bids-validation); fi

run_qsiprep() {
    [[ -f "$QSIPREP_IMAGE" ]] || { ((DRY_RUN)) || { echo "Missing QSIPrep image: $QSIPREP_IMAGE" >&2; exit 1; }; }
    [[ -d "$PROCESSING_BIDS_DIR" ]] || { ((DRY_RUN)) || { echo "Processing BIDS directory not found: $PROCESSING_BIDS_DIR" >&2; exit 1; }; }
    if [[ -n "$QSIPREP_DWI_MERGE_PATCH" ]]; then
        echo "Using QSIPrep DWI merge compatibility patch: $QSIPREP_DWI_MERGE_PATCH"
    fi
    local common_binds=("-B" "$PROCESSING_BIDS_DIR:/bids:ro")
    if ((${#base_binds[@]})); then common_binds+=("${base_binds[@]}"); fi
    local command=("$APPTAINER" run --cleanenv)
    if ((${#gpu_args[@]})); then command+=("${gpu_args[@]}"); fi
    if ((${#container_env[@]})); then command+=("${container_env[@]}"); fi
    command+=("${common_binds[@]}" -B "$QSIPREP_DIR:/out" -B "$WORK_DIR/qsiprep:/work")
    if [[ -n "$QSIPREP_DWI_MERGE_PATCH" ]]; then
        command+=(-B "$QSIPREP_DWI_MERGE_PATCH:/app/.pixi/envs/qsiprep/lib/python3.10/site-packages/qsiprep/interfaces/dwi_merge.py:ro")
    fi
    if [[ -n "$BIDS_FILTER_FILE" ]]; then command+=(-B "$(dirname "$BIDS_FILTER_FILE"):/bids_filter:ro"); fi
    if [[ -n "$FS_SUBJECTS_DIR" ]]; then command+=(-B "$FS_SUBJECTS_DIR:/fs_subjects:ro"); fi
    command+=("$QSIPREP_IMAGE" "${qsiprep_args[@]}")
    run_logged "$LOG_DIR/qsiprep.log" "${command[@]}"
}

run_qsirecon() {
    [[ -f "$QSIRECON_IMAGE" ]] || { ((DRY_RUN)) || { echo "Missing QSIRecon image: $QSIRECON_IMAGE" >&2; exit 1; }; }
    [[ -d "$QSIPREP_DIR" ]] || { echo "QSIPrep output directory not found: $QSIPREP_DIR" >&2; exit 1; }
    if ((DRY_RUN == 0)); then
        # A dataset description is created early by QSIPrep, so require its
        # actual participant-level DWI products before allowing QSIRecon to
        # consume a separately launched or partially completed stage.
        [[ -f "$QSIPREP_DIR/dataset_description.json" ]] || {
            echo "QSIPrep dataset description not found: $QSIPREP_DIR/dataset_description.json" >&2
            exit 1
        }
        if ! find "$QSIPREP_DIR" -type f -name '*preproc_dwi.nii.gz' -print -quit | grep -q .; then
            echo "QSIPrep has no completed preprocessed DWI output under: $QSIPREP_DIR" >&2
            exit 1
        fi
        local missing_dwi_mask=0
        while IFS= read -r -d '' preproc_dwi; do
            local mask_name="$(basename "${preproc_dwi%_desc-preproc_dwi.nii.gz}")_desc-brain_mask.nii.gz"
            if ! find "$QSIPREP_DIR" -type f -name "$mask_name" -print -quit | grep -q .; then
                echo "QSIPrep is missing the matching DWI mask $mask_name" >&2
                missing_dwi_mask=1
                break
            fi
        done < <(find "$QSIPREP_DIR" -type f -name '*_desc-preproc_dwi.nii.gz' -print0)
        if ((missing_dwi_mask)); then exit 1; fi
    fi
    local qsiprep_recon_input="$WORK_DIR/qsirecon/qsiprep_input_combined"
    if ((DRY_RUN)); then
        echo "Would prepare QSIRecon input overlay with compatible acquisition concatenation: $QSIPREP_DIR -> $qsiprep_recon_input"
    else
        python3 "$SCRIPT_DIR/prepare_qsirecon_input.py" "$QSIPREP_DIR" "$qsiprep_recon_input" --bids-dir "$BIDS_ROOT"
    fi
    local resolved_spec="$RECON_SPEC"
    if ((DRY_RUN == 0)); then
        local selection=(python3 "$SCRIPT_DIR/select_reconstruction.py" "$qsiprep_recon_input"
            --recon-spec "$RECON_SPEC" --output-manifest "$MANIFEST_DIR/reconstruction_selection.json")
        if ((${#PARTICIPANTS[@]})); then selection+=(--participant-label "${PARTICIPANTS[@]}"); fi
        if ((${#SESSIONS[@]})); then selection+=(--session-id "${SESSIONS[@]}"); fi
        resolved_spec=$("${selection[@]}")
        echo "Reconstruction selected from completed gradients: $resolved_spec"
    elif [[ "$RECON_SPEC" == "auto" ]]; then
        echo "Dry-run: auto reconstruction resolves from completed QSIPrep gradients at execution."
    fi
    if [[ "$resolved_spec" == "metrics-dki-noddi" ]]; then
        resolved_spec="$SCRIPT_DIR/specs/metrics_dki_noddi.yaml"
    elif [[ "$resolved_spec" == "metrics-noddi" ]]; then
        resolved_spec="$SCRIPT_DIR/specs/metrics_noddi.yaml"
    fi
    local container_spec="$resolved_spec"
    local spec_bind=()
    local amico_bind=()
    if [[ "$resolved_spec" == */* || "$resolved_spec" == *.yaml || "$resolved_spec" == *.yml ]]; then
        if [[ ! -f "$resolved_spec" ]]; then
            ((DRY_RUN)) || { echo "QSIRecon specification not found: $resolved_spec" >&2; exit 1; }
        else
            resolved_spec="$(cd "$(dirname "$resolved_spec")" && pwd -P)/$(basename "$resolved_spec")"
        fi
        spec_bind=(-B "$(dirname "$resolved_spec"):/recon_specs:ro")
        container_spec="/recon_specs/$(basename "$resolved_spec")"
    fi
    if [[ -f "$resolved_spec" ]] && grep -q 'action:[[:space:]]*fit_noddi' "$resolved_spec"; then
        local amico_cache="$WORK_DIR/amico_directions"
        local amico_container_dir="/app/.pixi/envs/qsirecon/lib/python3.10/site-packages/amico/directions"
        if ((DRY_RUN == 0)); then
            amico_container_dir=$("$APPTAINER" exec "$QSIRECON_IMAGE" python -c \
                'import amico, pathlib; print(pathlib.Path(amico.__file__).parent / "directions")')
            mkdir -p "$amico_cache"
            if [[ ! -f "$amico_cache/.setup_complete" ]]; then
                "$APPTAINER" exec -B "$amico_cache:/amico_cache" "$QSIRECON_IMAGE" sh -lc \
                    "cp -an '$amico_container_dir'/'.' /amico_cache/"
                "$APPTAINER" exec -B "$amico_cache:$amico_container_dir" \
                    "$QSIRECON_IMAGE" python -c 'import amico; amico.setup()'
                touch "$amico_cache/.setup_complete"
            fi
        fi
        amico_bind=(-B "$amico_cache:$amico_container_dir")
    fi
    local command=("$APPTAINER" run --cleanenv)
    if ((${#gpu_args[@]})); then command+=("${gpu_args[@]}"); fi
    if ((${#container_env[@]})); then command+=("${container_env[@]}"); fi
    # Apptainer --cleanenv can leave workflow child processes with a reduced
    # PATH. MRtrix's 5ttgen HSVS helper uses `#!/usr/bin/env python`, so keep
    # QSIRecon's Python and its bundled neuroimaging tools available to child
    # commands as well as to the container entrypoint.
    command+=(--env "PATH=/app/.pixi/envs/qsirecon/bin:/opt/workbench/bin_linux64:/opt/art/bin:/opt/freesurfer/bin:/opt/freesurfer/tktools:/opt/freesurfer/mni/bin:/opt/dsi-studio:/opt/dsi-studio/dsi_studio_64:/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/ants/bin:/opt/dsi-studio-chen:/opt/mrtrix3-latest/bin:/opt/3Tissue/bin:/src/TORTOISEV4/bin")
    command+=(-B "$qsiprep_recon_input:/qsiprep:ro" -B "$QSIRECON_DIR:/out" -B "$WORK_DIR/qsirecon:/work")
    if ((${#atlas_binds[@]})); then command+=("${atlas_binds[@]}"); fi
    if ((${#spec_bind[@]})); then command+=("${spec_bind[@]}"); fi
    if ((${#amico_bind[@]})); then command+=("${amico_bind[@]}"); fi
    if ((${#base_binds[@]})); then command+=("${base_binds[@]}"); fi
    if [[ -n "$FS_SUBJECTS_DIR" ]]; then command+=(-B "$FS_SUBJECTS_DIR:/fs_subjects:ro"); fi
    command+=("$QSIRECON_IMAGE" "${qsirecon_args[@]}" --recon-spec "$container_spec")
    run_logged "$LOG_DIR/qsirecon.log" "${command[@]}"
}

run_metrics() {
    ((${#PARTICIPANTS[@]})) || { echo "--participant-label is required for the metrics stage" >&2; exit 2; }
    for participant in "${PARTICIPANTS[@]}"; do
        run_logged "$LOG_DIR/tensor_metrics.log" bash "$SCRIPT_DIR/run_tensor_metrics.sh" \
            "$QSIPREP_DIR" "$QSIRECON_DIR" "$participant" "$THREADS" "$APPTAINER" "$QSIRECON_IMAGE"
    done
    local metrics_spec="$WORK_DIR/metrics_noddi_nthreads-${OMP_THREADS}.yaml"
    sed "s/num_threads: [0-9][0-9]*/num_threads: $OMP_THREADS/" \
        "$SCRIPT_DIR/specs/metrics_noddi.yaml" > "$metrics_spec"
    RECON_SPEC="$metrics_spec"
    ATLASES=()
    atlas_binds=()
    atlas_dataset_args=()
    qsirecon_args=(
        /qsiprep /out participant --input-type qsiprep
        --nprocs "$THREADS" --omp-nthreads "$OMP_THREADS" --mem "$((MEM_GB * 1024))"
        --work-dir /work --resource-monitor
    )
    if [[ -n "$FS_LICENSE" ]]; then qsirecon_args+=(--fs-license-file "/fs_license/$(basename "$FS_LICENSE")"); fi
    if [[ -n "$FS_SUBJECTS_DIR" ]]; then qsirecon_args+=(--fs-subjects-dir /fs_subjects); fi
    qsirecon_args+=(--participant-label "${PARTICIPANTS[@]}")
    if ((${#SESSIONS[@]})); then qsirecon_args+=(--session-id "${SESSIONS[@]}"); fi
    # The metrics-only AMICO run consumes an already preprocessed DWI and does
    # not need ODF visual reports. Disabling them also avoids QSIRecon's
    # optional recon_plot dependency for this scalar-metrics workflow.
    qsirecon_args+=(--skip-odf-reports)
    run_qsirecon
    for participant in "${PARTICIPANTS[@]}"; do
        run_logged "$LOG_DIR/verify_metrics.log" python3 "$SCRIPT_DIR/verify_dwi_metrics.py" \
            --qsirecon-dir "$QSIRECON_DIR" --qsiprep-dir "$QSIPREP_DIR" \
            --participant-label "$participant" \
            --output-json "$DWIPREP_DIR/manifests/sub-${participant#sub-}_metrics_verification.json"
    done
}

run_connectome() {
    ((${#PARTICIPANTS[@]})) || { echo "--participant-label is required for the connectome stage" >&2; exit 2; }
    ((${#ATLASES[@]})) || { echo "The connectome stage requires at least one --atlases value" >&2; exit 2; }
    for participant in "${PARTICIPANTS[@]}"; do
        run_logged "$LOG_DIR/connectome.log" bash "$SCRIPT_DIR/run_existing_connectome.sh" \
            "$QSIRECON_DIR" "$participant" "$THREADS" "$APPTAINER" "$QSIRECON_IMAGE" "${ATLASES[@]}"
    done
}

run_verify() {
    local command=(python3 "$SCRIPT_DIR/verify_dwi_outputs.py" --output-dir "$OUTPUT_ROOT" --manifest "$PREFLIGHT_MANIFEST")
    if ((${#PARTICIPANTS[@]} == 1)); then
        command+=(--participant-label "${PARTICIPANTS[0]}" --output-json "$MANIFEST_DIR/sub-${PARTICIPANTS[0]#sub-}_dwi_verification.json")
    fi
    if ((${#ATLASES[@]})); then command+=(--expected-atlas "${ATLASES[@]}"); fi
    if [[ -f "$WORK_DIR/dwi_processing_overlay.json" ]]; then
        command+=(--processing-overlay "$WORK_DIR/dwi_processing_overlay.json")
    fi
    if [[ -f "$WORK_DIR/dwi_processing_overlay.json" && -d "$WORK_DIR/qsiprep" ]]; then
        command+=(--qsiprep-work-dir "$WORK_DIR/qsiprep" --require-topup-provenance)
    fi
    run_logged "$LOG_DIR/verify.log" "${command[@]}"
}

case "$STAGE" in
    preflight) run_preflight ;;
    qsiprep) run_preflight; prepare_processing_bids; run_qsiprep ;;
    qsirecon) run_qsirecon ;;
    metrics) run_metrics ;;
    connectome) run_connectome ;;
    verify) run_preflight; run_verify ;;
    all)
        ((${#PARTICIPANTS[@]})) || { echo "--participant-label is required for the complete DWI workflow" >&2; exit 2; }
        run_preflight; prepare_processing_bids; run_qsiprep; run_qsirecon
        (run_metrics)
        if ((${#ATLASES[@]})); then run_connectome; fi
        run_verify
        ;;
esac
