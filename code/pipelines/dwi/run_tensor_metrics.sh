#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -ge 6 ]] || {
    echo "Usage: $0 QSIPREP_DIR QSIRECON_DIR PARTICIPANT THREADS APPTAINER IMAGE" >&2
    exit 2
}
QSIPREP_DIR="$(cd "$1" && pwd -P)"
QSIRECON_DIR="$2"
PARTICIPANT="sub-${3#sub-}"
THREADS="$4"
APPTAINER="$5"
IMAGE="$6"
DATASET_DIR="$QSIRECON_DIR/derivatives/dwiprep-tensor"
WORK_DIR="$QSIRECON_DIR/.tensor-work/$PARTICIPANT"
mkdir -p "$DATASET_DIR" "$WORK_DIR"

mapfile -t dwi_files < <(find "$QSIPREP_DIR/$PARTICIPANT" -type f -name '*_desc-preproc_dwi.nii.gz' -print)
((${#dwi_files[@]})) || { echo "No preprocessed DWI found for $PARTICIPANT" >&2; exit 1; }

for dwi in "${dwi_files[@]}"; do
    relative="${dwi#"$QSIPREP_DIR"/}"
    stem="$(basename "$dwi" .nii.gz)"
    prefix="${stem%_desc-preproc_dwi}"
    input_dir="$(dirname "$relative")"
    bval="${relative%.nii.gz}.bval"
    bvec="${relative%.nii.gz}.bvec"
    mask_name="${prefix}_desc-brain_mask.nii.gz"
    mask="$(find "$QSIPREP_DIR" -type f -name "$mask_name" -print -quit)"
    [[ -f "$QSIPREP_DIR/$bval" && -f "$QSIPREP_DIR/$bvec" && -n "$mask" ]] || {
        echo "Missing gradients or mask for $dwi" >&2
        exit 1
    }
    mask_relative="${mask#"$QSIPREP_DIR"/}"
    output_dir="$DATASET_DIR/$input_dir"
    mkdir -p "$output_dir"
    tensor="$WORK_DIR/${prefix}_tensor.mif"
    eigenvalues="$WORK_DIR/${prefix}_eigenvalues.nii.gz"
    effective_mask="$WORK_DIR/${prefix}_tensor_mask.nii.gz"

    python3 "$(dirname "$0")/make_tensor_mask.py" \
        "$QSIPREP_DIR/$relative" "$mask" "$effective_mask"

    "$APPTAINER" exec --cleanenv \
        -B "$QSIPREP_DIR:/qsiprep:ro" -B "$DATASET_DIR:/out" -B "$WORK_DIR:/work" \
        "$IMAGE" dwi2tensor "/qsiprep/$relative" "/work/$(basename "$tensor")" \
        -fslgrad "/qsiprep/$bvec" "/qsiprep/$bval" \
        -mask "/work/$(basename "$effective_mask")" -nthreads "$THREADS" -force
    "$APPTAINER" exec --cleanenv \
        -B "$DATASET_DIR:/out" -B "$WORK_DIR:/work" \
        "$IMAGE" tensor2metric "/work/$(basename "$tensor")" \
        -value "/work/$(basename "$eigenvalues")" -num 1,2,3 \
        -nthreads "$THREADS" -force
    "$APPTAINER" exec --cleanenv \
        -B "$DATASET_DIR:/out" -B "$WORK_DIR:/work" \
        -B "$(dirname "$0"):/pipeline:ro" \
        "$IMAGE" python /pipeline/tensor_eigen_metrics.py \
        "/work/$(basename "$eigenvalues")" "/out/$input_dir" "$prefix"
    python3 "$(dirname "$0")/write_tensor_sidecars.py" \
        --output-dir "$output_dir" --prefix "$prefix" --source "$relative"
done

python3 - "$DATASET_DIR/dataset_description.json" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "Name": "dwiprep MRtrix tensor metrics",
    "BIDSVersion": "1.10.0",
    "DatasetType": "derivative",
    "GeneratedBy": [{"Name": "MRtrix3 dwi2tensor/tensor2metric"}],
}, indent=2) + "\n")
PY
