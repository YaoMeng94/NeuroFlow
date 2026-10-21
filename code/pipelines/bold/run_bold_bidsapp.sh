#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="all"
FMRIPREP_IMAGE="${PRISMA_FMRIPREP_IMAGE:-}"
TEDANA_IMAGE="${PRISMA_TEDANA_IMAGE:-}"
TEDANA_COMMAND="${PRISMA_TEDANA_COMMAND:-}"
TEDANA_FITTYPE="loglin"
TEDPCA="aic"
ICA_METHOD="fastica"
APPTAINER="${PRISMA_APPTAINER:-apptainer}"
FS_SUBJECTS_DIR=""
ANAT_FSLR_DIR=""
ANAT_FS_SUBJECTS_DIR=""
FS_LICENSE=""
THREADS=""
OMP_THREADS=""
MEM_GB=""
WORK_DIR=""
FMRIPREP_DIR_OVERRIDE=""
TEDANA_DIR_OVERRIDE=""
MEFMRIPREP_DIR_OVERRIDE=""
FS_STAGE_DIR=""
BIDS_FILTER_FILE=""
WB_COMMAND="${PRISMA_WB_COMMAND:-wb_command}"
ANTS_APPLY="${PRISMA_ANTS_APPLY:-antsApplyTransforms}"
ATLAS_ROOT="${PRISMA_HCP_ATLAS_ROOT:-}"
FD_THRESHOLD="0.20"
SMOOTHING_FWHM_MM="2"
FMRIPREP_CIFTI=0
COMPARISON_ECHO=""
COMPARISON_TE_MS=""
# Standard-space volume output preserves the T1w-to-MNI transform required by
# the custom 91k subcortical stage. fMRIPrep CIFTI output is opt-in only.
OUTPUT_SPACES=(T1w MNI152NLin6Asym)
PARTICIPANTS=()
SESSIONS=()
DRY_RUN=0
SKIP_BIDS_VALIDATION=0

usage() {
    cat <<'EOF'
Usage: run_bold_bidsapp.sh <bids_dir> <output_dir> participant [options]

The output_dir is a derivatives parent. The app writes sibling fmriprep,
tedana, and mefmriprep derivative datasets.

Developer stages: preflight, fmriprep, tedana, postprocess, cifti, qc, verify, all

Options:
  --participant-label LABEL [LABEL ...]
  --session-id SESSION [SESSION ...]
  --stage STAGE
  --fmriprep-image PATH
  --tedana-image PATH       standalone tedana image (default: tedana_26.0.3.sif)
  --tedana-command PATH     native tedana executable, alternative to image
  --tedana-fittype TYPE      loglin or curvefit
  --tedpca METHOD             tedana PCA selection method (default: aic)
  --ica-method METHOD         fastica or robustica
  --fs-subjects-dir PATH    optional compatible subjects directory for fMRIPrep reuse
  --anat-fslr-dir PATH      anatomy pipeline fsLR derivative root (required for CIFTI)
  --anat-fs-subjects-dir PATH anatomy FreeSurfer subjects directory for native surface projection
  --fs-license-file PATH
  --threads N
  --nprocs N              alias for --threads
  --omp-nthreads N
  --mem-gb N
  --mem-mb N              standard memory limit in MiB
  --work-dir PATH
  --fmriprep-dir PATH      reuse/write an explicit fMRIPrep derivative root
  --tedana-dir PATH        reuse/write an explicit tedana derivative root
  --mefmriprep-dir PATH    explicit final derivative root
  --bids-filter-file PATH
  --wb-command PATH
  --atlas-root PATH
  --ants-apply PATH
  --fd-threshold MM       clean-volume threshold in mm (default: 0.20)
  --smoothing-fwhm MM     final spatial smoothing FWHM; 0 disables (default: 2)
  --fmriprep-cifti        also generate fMRIPrep's comparison 91k CIFTI
  --comparison-echo N      explicit single-echo tSNR baseline (default: automatic closest-TE)
  --comparison-te-ms MS    override automatic target TE (starting defaults: 30 ms at 3 T, 20 ms at 7 T)
  --output-spaces "SPACE ..."
  --skip-bids-validation
  --dry-run
EOF
}

[[ $# -ge 3 ]] || { usage >&2; exit 2; }
BIDS_ROOT="$1"; OUTPUT_ROOT="$2"; ANALYSIS_LEVEL="$3"; shift 3
[[ "$ANALYSIS_LEVEL" == "participant" ]] || { echo "Only participant analysis is supported" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --participant-label)
            shift
            while [[ $# -gt 0 && "$1" != --* ]]; do PARTICIPANTS+=("$1"); shift; done ;;
        --session-id)
            shift
            while [[ $# -gt 0 && "$1" != --* ]]; do SESSIONS+=("$1"); shift; done ;;
        --stage) STAGE="$2"; shift 2 ;;
        --fmriprep-image) FMRIPREP_IMAGE="$2"; shift 2 ;;
        --tedana-image) TEDANA_IMAGE="$2"; shift 2 ;;
        --tedana-command) TEDANA_COMMAND="$2"; shift 2 ;;
        --tedana-fittype) TEDANA_FITTYPE="$2"; shift 2 ;;
        --tedpca) TEDPCA="$2"; shift 2 ;;
        --ica-method) ICA_METHOD="$2"; shift 2 ;;
        --fs-subjects-dir) FS_SUBJECTS_DIR="$2"; shift 2 ;;
        --anat-fslr-dir) ANAT_FSLR_DIR="$2"; shift 2 ;;
        --anat-fs-subjects-dir) ANAT_FS_SUBJECTS_DIR="$2"; shift 2 ;;
        --fs-license-file) FS_LICENSE="$2"; shift 2 ;;
        --threads) THREADS="$2"; shift 2 ;;
        --nprocs) THREADS="$2"; shift 2 ;;
        --omp-nthreads) OMP_THREADS="$2"; shift 2 ;;
        --mem-gb) MEM_GB="$2"; shift 2 ;;
        --mem-mb) MEM_GB="$((($2 + 1023) / 1024))"; shift 2 ;;
        --work-dir) WORK_DIR="$2"; shift 2 ;;
        --fmriprep-dir) FMRIPREP_DIR_OVERRIDE="$2"; shift 2 ;;
        --tedana-dir) TEDANA_DIR_OVERRIDE="$2"; shift 2 ;;
        --mefmriprep-dir) MEFMRIPREP_DIR_OVERRIDE="$2"; shift 2 ;;
        --bids-filter-file) BIDS_FILTER_FILE="$2"; shift 2 ;;
        --wb-command) WB_COMMAND="$2"; shift 2 ;;
        --atlas-root) ATLAS_ROOT="$2"; shift 2 ;;
        --ants-apply) ANTS_APPLY="$2"; shift 2 ;;
        --fd-threshold) FD_THRESHOLD="$2"; shift 2 ;;
        --smoothing-fwhm) SMOOTHING_FWHM_MM="$2"; shift 2 ;;
        --fmriprep-cifti) FMRIPREP_CIFTI=1; shift ;;
        --comparison-echo) COMPARISON_ECHO="$2"; shift 2 ;;
        --comparison-te-ms) COMPARISON_TE_MS="$2"; shift 2 ;;
        --output-spaces) read -r -a OUTPUT_SPACES <<< "$2"; shift 2 ;;
        --skip-bids-validation) SKIP_BIDS_VALIDATION=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$STAGE" in
    preflight|fmriprep|tedana|postprocess|cifti|qc|verify|all) ;;
    *) echo "Invalid --stage: $STAGE" >&2; exit 2 ;;
esac
case "$TEDANA_FITTYPE" in loglin|curvefit) ;; *) echo "Invalid --tedana-fittype: $TEDANA_FITTYPE" >&2; exit 2 ;; esac
case "$ICA_METHOD" in fastica|robustica) ;; *) echo "Invalid --ica-method: $ICA_METHOD" >&2; exit 2 ;; esac
[[ -d "$BIDS_ROOT" ]] || { echo "BIDS directory not found: $BIDS_ROOT" >&2; exit 2; }
BIDS_ROOT="$(cd "$BIDS_ROOT" && pwd -P)"
mkdir -p "$OUTPUT_ROOT"
OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd -P)"
for path_var in FS_SUBJECTS_DIR ANAT_FSLR_DIR ANAT_FS_SUBJECTS_DIR FS_LICENSE BIDS_FILTER_FILE FMRIPREP_DIR_OVERRIDE TEDANA_DIR_OVERRIDE MEFMRIPREP_DIR_OVERRIDE; do
    path="${!path_var}"
    if [[ -n "$path" ]]; then
        printf -v "$path_var" '%s/%s' "$(cd "$(dirname "$path")" && pwd -P)" "$(basename "$path")"
    fi
done

if [[ -z "$THREADS" ]]; then
    THREADS="$(nproc 2>/dev/null || printf '8')"
    ((THREADS > 32)) && THREADS=32
fi
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] && ((THREADS <= 50)) || { echo "--nprocs/--threads must be between 1 and 50" >&2; exit 2; }
if [[ -z "$OMP_THREADS" ]]; then OMP_THREADS=$((THREADS > 8 ? 4 : 2)); fi
[[ "$OMP_THREADS" =~ ^[1-9][0-9]*$ ]] && ((OMP_THREADS <= THREADS)) || { echo "--omp-nthreads must be between 1 and --nprocs" >&2; exit 2; }
if [[ -z "$MEM_GB" ]]; then MEM_GB=64; fi
if [[ -z "$WORK_DIR" ]]; then WORK_DIR="$OUTPUT_ROOT/.work/mefmriprep"; fi
mkdir -p "$WORK_DIR"
WORK_DIR="$(cd "$WORK_DIR" && pwd -P)"
if [[ -z "$TEDANA_COMMAND" && -x "$HOME/.local/bin/tedana" ]]; then
    TEDANA_COMMAND="$HOME/.local/bin/tedana"
fi

FMRIPREP_DIR="${FMRIPREP_DIR_OVERRIDE:-$OUTPUT_ROOT/fmriprep}"
TEDANA_DIR="${TEDANA_DIR_OVERRIDE:-$OUTPUT_ROOT/tedana}"
MEFMRIPREP_DIR="${MEFMRIPREP_DIR_OVERRIDE:-$OUTPUT_ROOT/mefmriprep}"
CIFTI_DIR="$MEFMRIPREP_DIR"
QC_DIR="$MEFMRIPREP_DIR/qc"
MANIFEST_DIR="$MEFMRIPREP_DIR/manifests"
LOG_DIR="$MEFMRIPREP_DIR/logs"
REGRESSED_DIR="$WORK_DIR/post_tedana"
PREFLIGHT_MANIFEST="$MANIFEST_DIR/bold_preflight.json"
if ((${#PARTICIPANTS[@]} == 1)); then
    PREFLIGHT_MANIFEST="$MANIFEST_DIR/sub-${PARTICIPANTS[0]#sub-}_bold_preflight.json"
fi
mkdir -p "$OUTPUT_ROOT" "$FMRIPREP_DIR" "$TEDANA_DIR" "$MEFMRIPREP_DIR" "$QC_DIR" "$MANIFEST_DIR" "$LOG_DIR" "$WORK_DIR/fmriprep" "$WORK_DIR/tedana" "$REGRESSED_DIR"

run_logged() {
    local log_file="$1"; shift
    printf '+'; printf ' %q' "$@"; printf '\n'
    if ((DRY_RUN == 0)); then
        "$@" 2>&1 | tee -a "$log_file"
    fi
}

run_preview() {
    local log_file="$1"; shift
    printf '+'; printf ' %q' "$@"; printf '\n'
    "$@" 2>&1 | tee -a "$log_file"
}

run_preflight() {
    local command=(python3 "$SCRIPT_DIR/preflight_bold.py" "$BIDS_ROOT" --output-manifest "$PREFLIGHT_MANIFEST")
    if ((${#PARTICIPANTS[@]})); then command+=(--participant-label "${PARTICIPANTS[@]}"); fi
    if ((${#SESSIONS[@]})); then command+=(--session-id "${SESSIONS[@]}"); fi
    if ((DRY_RUN)); then
        printf '+'; printf ' %q' "${command[@]}"; printf '\n'
        "${command[@]}" 2>&1 | tee -a "$LOG_DIR/preflight.log"
    else
        run_logged "$LOG_DIR/preflight.log" "${command[@]}"
    fi
}

stage_freesurfer_subjects() {
    [[ -n "$FS_SUBJECTS_DIR" ]] || return 0
    FS_STAGE_DIR="$WORK_DIR/freesurfer_subjects"
    if ((DRY_RUN)); then
        echo "Would stage FreeSurfer subjects from $FS_SUBJECTS_DIR to $FS_STAGE_DIR"
        return 0
    fi
    mkdir -p "$FS_STAGE_DIR"
    local labels=()
    if ((${#PARTICIPANTS[@]})); then
        labels=("${PARTICIPANTS[@]}")
    else
        while IFS= read -r -d '' subject_dir; do
            labels+=("$(basename "$subject_dir")")
        done < <(find "$FS_SUBJECTS_DIR" -mindepth 1 -maxdepth 1 -type d -name 'sub-*' -print0)
    fi
    ((${#labels[@]})) || { echo "No FreeSurfer subjects found in: $FS_SUBJECTS_DIR" >&2; exit 1; }
    local label subject_id
    for label in "${labels[@]}"; do
        subject_id="$label"
        [[ "$subject_id" == sub-* ]] || subject_id="sub-$subject_id"
        [[ -d "$FS_SUBJECTS_DIR/$subject_id" ]] || {
            echo "FreeSurfer subject not found: $FS_SUBJECTS_DIR/$subject_id" >&2
            exit 1
        }
        rsync -a "$FS_SUBJECTS_DIR/$subject_id/" "$FS_STAGE_DIR/$subject_id/"
    done
}

run_fmriprep() {
    if [[ ! -f "$FMRIPREP_IMAGE" ]]; then
        ((DRY_RUN)) || { echo "Missing fMRIPrep image: $FMRIPREP_IMAGE" >&2; exit 1; }
        echo "WARNING: dry-run image does not exist yet: $FMRIPREP_IMAGE" >&2
    fi
    local command=(
        "$APPTAINER" exec --cleanenv
        -B "$BIDS_ROOT:/bids:ro"
        -B "$FMRIPREP_DIR:/out"
        -B "$WORK_DIR/fmriprep:/work"
    )
    if [[ -n "$FS_SUBJECTS_DIR" ]]; then
        stage_freesurfer_subjects
    fi
    local fmriprep_args=(
        fmriprep /bids /out participant
        --output-layout bids
        --level full
        --me-output-echos
        --nprocs "$THREADS"
        --omp-nthreads "$OMP_THREADS"
        --mem "$((MEM_GB * 1024))"
        --work-dir /work
    )
    ((FMRIPREP_CIFTI)) && fmriprep_args+=(--cifti-output 91k)
    if [[ -n "$BIDS_FILTER_FILE" ]]; then
        [[ -f "$BIDS_FILTER_FILE" ]] || { ((DRY_RUN)) || { echo "BIDS filter file not found: $BIDS_FILTER_FILE" >&2; exit 1; }; }
        command+=(-B "$BIDS_FILTER_FILE:/bids_filter.json:ro")
        fmriprep_args+=(--bids-filter-file /bids_filter.json)
    fi
    fmriprep_args+=(--resource-monitor)
    fmriprep_args+=(--output-spaces "${OUTPUT_SPACES[@]}")
    if ((${#PARTICIPANTS[@]})); then fmriprep_args+=(--participant-label "${PARTICIPANTS[@]}"); fi
    if ((${#SESSIONS[@]})); then fmriprep_args+=(--session-id "${SESSIONS[@]}"); fi
    if ((SKIP_BIDS_VALIDATION)); then fmriprep_args+=(--skip-bids-validation); fi
    if [[ -n "$FS_LICENSE" ]]; then
        [[ -f "$FS_LICENSE" ]] || { echo "FreeSurfer license not found: $FS_LICENSE" >&2; exit 1; }
        command+=(-B "$(dirname "$FS_LICENSE"):/fs_license:ro")
        fmriprep_args+=(--fs-license-file "/fs_license/$(basename "$FS_LICENSE")")
    fi
    if [[ -n "$FS_SUBJECTS_DIR" ]]; then
        [[ -d "$FS_SUBJECTS_DIR" ]] || { echo "FreeSurfer subjects directory not found: $FS_SUBJECTS_DIR" >&2; exit 1; }
        command+=(-B "$FS_STAGE_DIR:/fs_subjects")
        # fMRIPrep may need write access to complete T2-aware resume steps.
        fmriprep_args+=(--fs-subjects-dir /fs_subjects)
    else
        fmriprep_args+=(--fs-no-reconall)
    fi
    command+=("$FMRIPREP_IMAGE" "${fmriprep_args[@]}")
    run_logged "$LOG_DIR/fmriprep.log" "${command[@]}"
}

run_tedana() {
    if [[ -n "$TEDANA_COMMAND" ]]; then
        if [[ "$TEDANA_COMMAND" == */* ]]; then
            if [[ ! -x "$TEDANA_COMMAND" ]]; then
                ((DRY_RUN)) || { echo "Missing tedana command: $TEDANA_COMMAND" >&2; exit 1; }
                echo "WARNING: dry-run tedana command does not exist yet: $TEDANA_COMMAND" >&2
            fi
        elif ! command -v "$TEDANA_COMMAND" >/dev/null 2>&1; then
            ((DRY_RUN)) || { echo "tedana command not found: $TEDANA_COMMAND" >&2; exit 1; }
            echo "WARNING: dry-run tedana command is not on PATH: $TEDANA_COMMAND" >&2
        fi
    elif [[ ! -f "$TEDANA_IMAGE" ]]; then
        ((DRY_RUN)) || { echo "Missing tedana image: $TEDANA_IMAGE" >&2; exit 1; }
        echo "WARNING: dry-run tedana image does not exist yet: $TEDANA_IMAGE" >&2
    fi
    local command=(python3 "$SCRIPT_DIR/run_tedana_bold.py"
        --fmriprep-dir "$FMRIPREP_DIR"
        --tedana-dir "$TEDANA_DIR"
        --manifest "$PREFLIGHT_MANIFEST"
        --work-dir "$WORK_DIR/tedana"
        --threads "$OMP_THREADS"
        --fittype "$TEDANA_FITTYPE"
        --tedpca "$TEDPCA"
        --ica-method "$ICA_METHOD")
    if [[ -n "$TEDANA_COMMAND" ]]; then
        command+=(--tedana-command "$TEDANA_COMMAND")
    else
        command+=(--tedana-image "$TEDANA_IMAGE")
    fi
    ((DRY_RUN)) && command+=(--dry-run)
    if ((DRY_RUN)); then run_preview "$LOG_DIR/tedana.log" "${command[@]}"; else run_logged "$LOG_DIR/tedana.log" "${command[@]}"; fi
}

run_postprocess() {
    local command=(python3 "$SCRIPT_DIR/regress_bold_confounds.py"
        --fmriprep-dir "$FMRIPREP_DIR"
        --tedana-dir "$TEDANA_DIR"
        --output-dir "$REGRESSED_DIR")
    ((DRY_RUN)) && command+=(--dry-run)
    if ((DRY_RUN)); then run_preview "$LOG_DIR/postprocess.log" "${command[@]}"; else run_logged "$LOG_DIR/postprocess.log" "${command[@]}"; fi
}

run_cifti() {
    [[ -n "$ANAT_FSLR_DIR" ]] || { echo "--anat-fslr-dir is required for the CIFTI stage" >&2; exit 2; }
    if [[ -z "$ANAT_FS_SUBJECTS_DIR" ]]; then
        local inferred_fs_subjects="$(dirname "$ANAT_FSLR_DIR")/freesurfer/subjects"
        [[ -d "$inferred_fs_subjects" ]] && ANAT_FS_SUBJECTS_DIR="$inferred_fs_subjects"
    fi
    [[ -n "$ANAT_FS_SUBJECTS_DIR" ]] || {
        echo "Native FreeSurfer surfaces are required for BOLD CIFTI export; set --anat-fs-subjects-dir" >&2
        exit 2
    }
    local command=(bash "$SCRIPT_DIR/export_cifti_bold.sh"
        --fmriprep-dir "$FMRIPREP_DIR"
        --input-dir "$REGRESSED_DIR"
        --anat-fslr-dir "$ANAT_FSLR_DIR"
        --fs-subjects-dir "$ANAT_FS_SUBJECTS_DIR"
        --output-dir "$CIFTI_DIR"
        --work-dir "$WORK_DIR/cifti"
        --wb-command "$WB_COMMAND"
        --atlas-root "$ATLAS_ROOT"
        --ants-apply "$ANTS_APPLY"
        --smoothing-fwhm "$SMOOTHING_FWHM_MM"
        --threads "$THREADS")
    if ((${#PARTICIPANTS[@]})); then command+=(--participant-label "${PARTICIPANTS[@]}"); fi
    ((DRY_RUN)) && command+=(--dry-run)
    if ((DRY_RUN)); then run_preview "$LOG_DIR/cifti.log" "${command[@]}"; else run_logged "$LOG_DIR/cifti.log" "${command[@]}"; fi
}

run_qc() {
    local command=(python3 "$SCRIPT_DIR/compute_bold_qc.py"
        --fmriprep-dir "$FMRIPREP_DIR"
        --tedana-dir "$TEDANA_DIR"
        --qc-dir "$QC_DIR"
        --manifest "$PREFLIGHT_MANIFEST"
        --bids-dir "$BIDS_ROOT"
        --fd-threshold "$FD_THRESHOLD"
        )
    [[ -n "$COMPARISON_ECHO" ]] && command+=(--comparison-echo "$COMPARISON_ECHO")
    [[ -n "$COMPARISON_TE_MS" ]] && command+=(--comparison-te-ms "$COMPARISON_TE_MS")
    ((DRY_RUN)) && command+=(--dry-run)
    if ((DRY_RUN)); then run_preview "$LOG_DIR/qc.log" "${command[@]}"; else run_logged "$LOG_DIR/qc.log" "${command[@]}"; fi
}

run_verify() {
    local command=(python3 "$SCRIPT_DIR/verify_bold_outputs.py"
        --bids-dir "$BIDS_ROOT"
        --output-dir "$OUTPUT_ROOT"
        --fmriprep-dir "$FMRIPREP_DIR"
        --tedana-dir "$TEDANA_DIR"
        --final-dir "$MEFMRIPREP_DIR"
        --manifest "$PREFLIGHT_MANIFEST")
    if [[ "$STAGE" == "all" || "$STAGE" == "verify" ]]; then command+=(--require-cifti); fi
    if [[ "$STAGE" == "all" || "$STAGE" == "verify" ]]; then command+=(--require-qc); fi
    ((DRY_RUN)) && command+=(--dry-run)
    if ((DRY_RUN)); then run_preview "$LOG_DIR/verify.log" "${command[@]}"; else run_logged "$LOG_DIR/verify.log" "${command[@]}"; fi
}

case "$STAGE" in
    preflight) run_preflight ;;
    fmriprep) run_preflight; run_fmriprep ;;
    tedana) run_tedana ;;
    postprocess) run_postprocess ;;
    cifti) run_cifti ;;
    qc) run_preflight; run_qc ;;
    verify) run_preflight; run_verify ;;
    all) run_preflight; run_fmriprep; run_tedana; run_postprocess; run_cifti; run_qc; run_verify ;;
esac
