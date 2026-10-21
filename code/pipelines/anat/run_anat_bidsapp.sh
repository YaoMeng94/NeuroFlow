#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARTICIPANTS=(); SESSIONS=(); STAGE=all; NPROCS=""; MSM_THREADS=4; WORK_DIR=""
BIDS_FILTER_FILE=""; FS_HOME="${FREESURFER_HOME:-}"
FS_LICENSE=""; ATLAS_ROOT=""; NEWMSM="${PRISMA_NEWMSM:-newmsm}"
WB_COMMAND="${PRISMA_WB_COMMAND:-wb_command}"; QC_PYTHON="${PRISMA_QC_PYTHON:-python3}"
MSM_CONFIG=""; DRY_RUN=0
usage() { cat <<'EOF'
Usage: run_anat_bidsapp.sh <bids_dir> <output_dir> participant [options]

Writes sibling freesurfer and anatprep derivative datasets under output_dir.
Options:
  --participant-label LABEL [LABEL ...]
  --session-id SESSION [SESSION ...]
  --nprocs N
  --omp-nthreads N
  --mem-mb N
  --work-dir PATH
  --bids-filter-file PATH
  --fs-license-file PATH
  --freesurfer-home PATH
  --atlas-root PATH
  --newmsm PATH
  --wb-command PATH
  --qc-python PATH
  --msm-config PATH
  --msm-threads N
  --stage all|preflight|recon|t2|fslr
  --skip-bids-validation
  --dry-run
EOF
}
[[ $# -ge 3 ]] || { usage >&2; exit 2; }
BIDS_ROOT="$1"; OUTPUT_ROOT="$2"; LEVEL="$3"; shift 3
[[ "$LEVEL" == participant ]] || { echo "Only participant analysis is supported" >&2; exit 2; }
[[ -d "$BIDS_ROOT" ]] || { echo "BIDS directory not found: $BIDS_ROOT" >&2; exit 2; }
BIDS_ROOT="$(cd "$BIDS_ROOT" && pwd -P)"
while (($#)); do case "$1" in
  --participant-label) shift; while [[ $# -gt 0 && "$1" != --* ]]; do PARTICIPANTS+=("${1#sub-}"); shift; done ;;
  --session-id) shift; while [[ $# -gt 0 && "$1" != --* ]]; do SESSIONS+=("${1#ses-}"); shift; done ;;
  --nprocs|--omp-nthreads) NPROCS="$2"; shift 2 ;; --mem-mb) shift 2 ;;
  --work-dir) WORK_DIR="$2"; shift 2 ;; --bids-filter-file) BIDS_FILTER_FILE="$2"; shift 2 ;;
  --fs-license-file) FS_LICENSE="$2"; shift 2 ;; --freesurfer-home) FS_HOME="$2"; shift 2 ;;
  --atlas-root) ATLAS_ROOT="$2"; shift 2 ;; --newmsm) NEWMSM="$2"; shift 2 ;;
  --wb-command) WB_COMMAND="$2"; shift 2 ;; --qc-python) QC_PYTHON="$2"; shift 2 ;;
  --msm-config) MSM_CONFIG="$2"; shift 2 ;; --msm-threads) MSM_THREADS="$2"; shift 2 ;;
  --stage) STAGE="$2"; shift 2 ;; --skip-bids-validation) shift ;; --dry-run) DRY_RUN=1; shift ;;
  -h|--help) usage; exit 0 ;; *) echo "Unknown option: $1" >&2; exit 2 ;; esac; done
case "$STAGE" in all|preflight|recon|t2|fslr) ;; *) echo "Invalid --stage: $STAGE" >&2; exit 2 ;; esac
if ((${#PARTICIPANTS[@]} == 0)); then while IFS= read -r p; do PARTICIPANTS+=("${p##*/sub-}"); done < <(find "$BIDS_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'sub-*' | sort); fi
if ((${#SESSIONS[@]})); then
  echo "Session-specific anatomy output paths are not implemented; refusing a run that could overwrite another session" >&2
  exit 2
fi
if [[ "$STAGE" == preflight ]]; then
  for participant in "${PARTICIPANTS[@]}"; do
    selector=(python3 "$SCRIPT_DIR/select_anat_inputs.py" "$BIDS_ROOT" --participant-label "$participant")
    [[ -n "$BIDS_FILTER_FILE" ]] && selector+=(--bids-filter-file "$BIDS_FILTER_FILE")
    if ! resolved="$("${selector[@]}")"; then
      echo "Anatomy preflight failed for sub-${participant}" >&2
      exit 1
    fi
    printf '%s\n' "$resolved"
  done
  exit 0
fi
[[ -n "$FS_HOME" ]] || { echo "Set --freesurfer-home or FREESURFER_HOME" >&2; exit 2; }
mkdir -p "$OUTPUT_ROOT"
OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd -P)"
if [[ -z "$NPROCS" ]]; then
  NPROCS="$(nproc 2>/dev/null || printf 8)"
  ((NPROCS > 32)) && NPROCS=32
fi
[[ "$NPROCS" =~ ^[1-9][0-9]*$ ]] && ((NPROCS <= 50)) || { echo "--nprocs must be between 1 and 50" >&2; exit 2; }
[[ "$MSM_THREADS" =~ ^[1-9][0-9]*$ ]] && ((MSM_THREADS * 2 <= 50)) || { echo "--msm-threads permits two hemispheres and must be between 1 and 25" >&2; exit 2; }
WORK_DIR="${WORK_DIR:-$OUTPUT_ROOT/.work/anatprep}"; FS_DIR="$OUTPUT_ROOT/freesurfer/subjects"; ANAT_DIR="$OUTPUT_ROOT/anatprep"; LOG_DIR="$OUTPUT_ROOT/logs"
mkdir -p "$WORK_DIR/logs" "$FS_DIR" "$ANAT_DIR" "$LOG_DIR"
WORK_DIR="$(cd "$WORK_DIR" && pwd -P)"
cat > "$ANAT_DIR/dataset_description.json" <<'EOF'
{"Name":"anatprep structural and fsLR derivatives","BIDSVersion":"1.11.2","DatasetType":"derivative","GeneratedBy":[{"Name":"anatprep","Version":"0.3.0"},{"Name":"FreeSurfer"},{"Name":"newMSM"},{"Name":"Connectome Workbench"}]}
EOF
for participant in "${PARTICIPANTS[@]}"; do
  selected_sessions=(""); ((${#SESSIONS[@]})) && selected_sessions=("${SESSIONS[@]}")
  for session in "${selected_sessions[@]}"; do
    selector=(python3 "$SCRIPT_DIR/select_anat_inputs.py" "$BIDS_ROOT" --participant-label "$participant")
    [[ -n "$session" ]] && selector+=(--session-id "$session"); [[ -n "$BIDS_FILTER_FILE" ]] && selector+=(--bids-filter-file "$BIDS_FILTER_FILE")
    resolved="$("${selector[@]}")"
    IFS=$'\t' read -r subject t1 t2 < <(python3 -c 'import json,sys; x=json.load(sys.stdin); print(x["subject"], x["t1w"], x["t2w"] or "", sep="\t")' <<<"$resolved")
    t1="$(cd "$(dirname "$t1")" && pwd -P)/$(basename "$t1")"
    command=(bash "$SCRIPT_DIR/run_anat_node1.sh" --subject "$subject" --bids-root "$BIDS_ROOT" --project-root "$WORK_DIR" --t1 "$t1" --stage "$STAGE" --threads "$NPROCS" --msm-threads "$MSM_THREADS" --subjects-dir "$FS_DIR" --fsLR-root "$ANAT_DIR" --freesurfer-home "$FS_HOME" --log-file "$LOG_DIR/${subject}${session:+_ses-${session}}.log")
    if [[ -n "$t2" ]]; then
        t2="$(cd "$(dirname "$t2")" && pwd -P)/$(basename "$t2")"
        command+=(--t2 "$t2")
    fi
    [[ -n "$FS_LICENSE" ]] && command+=(--fs-license-file "$FS_LICENSE"); [[ -n "$ATLAS_ROOT" ]] && command+=(--atlas-root "$ATLAS_ROOT"); [[ -n "$MSM_CONFIG" ]] && command+=(--msm-config "$MSM_CONFIG"); command+=(--newmsm "$NEWMSM" --wb-command "$WB_COMMAND" --qc-python "$QC_PYTHON"); ((DRY_RUN)) && command+=(--dry-run)
    printf '+'; printf ' %q' "${command[@]}"; printf '\n'; ((DRY_RUN)) || "${command[@]}"
  done
done
