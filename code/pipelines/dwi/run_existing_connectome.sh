#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -ge 6 ]] || {
    echo "Usage: $0 QSIRECON_DIR PARTICIPANT THREADS APPTAINER IMAGE ATLAS..." >&2
    exit 2
}
QSIRECON_DIR="$(cd "$1" && pwd -P)"
PARTICIPANT="sub-${2#sub-}"
THREADS="$3"
APPTAINER="$4"
IMAGE="$5"
shift 5
ATLASES=("$@")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WORK_DIR="$(dirname "$QSIRECON_DIR")/.work/dwiprep/qsirecon"
OUTPUT_DIR="$QSIRECON_DIR/derivatives/qsirecon-MRtrix3_act-HSVS/$PARTICIPANT/dwi"
mkdir -p "$WORK_DIR" "$OUTPUT_DIR"

track_gz="$(find "$OUTPUT_DIR" -type f -name '*model-ifod2_streamlines.tck.gz' -print -quit)"
weights="$(find "$OUTPUT_DIR" -type f -name '*model-sift2_streamlineweights.csv' -print -quit)"
[[ -n "$track_gz" && -f "$weights" ]] || {
    echo "Existing iFOD2 tractogram and SIFT2 weights are required under $OUTPUT_DIR" >&2
    exit 1
}
track="$(find "$WORK_DIR" -maxdepth 1 -type f -name '*.tck' -print -quit)"
if [[ -z "$track" ]]; then
    track="$WORK_DIR/$(basename "${track_gz%.gz}")"
    gzip -cd "$track_gz" > "$track"
fi

for atlas in "${ATLASES[@]}"; do
    # Use the ACPC segmentation on the preprocessed DWI grid for both
    # tract assignment and label-order padding. QSIRecon's MIF copy can be a
    # higher-resolution grid with parcels absent after its own resampling.
    segmentation="$(find "$QSIRECON_DIR/$PARTICIPANT/dwi" -type f -name "*_seg-${atlas}_dseg.nii.gz" -print -quit)"
    labels_tsv="$(find "$QSIRECON_DIR/sourcedata/atlases" -type f -name "*atlas-${atlas}*_dseg.tsv" -print -quit)"
    [[ -n "$segmentation" && -n "$labels_tsv" ]] || {
        echo "Missing subject-space segmentation or atlas labels for $atlas" >&2
        exit 1
    }
    track_name="$(basename "$track_gz")"
    stem="${track_name%_model-ifod2_streamlines.tck.gz}"
    raw_matrix="$OUTPUT_DIR/${stem}_atlas-${atlas}_model-ifod2_desc-sift2raw_connectivity.csv"
    matrix="$OUTPUT_DIR/${stem}_atlas-${atlas}_model-ifod2_desc-sift2_connectivity.csv"
    assignments="$OUTPUT_DIR/${stem}_atlas-${atlas}_model-ifod2_desc-sift2_assignments.txt"
    sidecar="$OUTPUT_DIR/${stem}_atlas-${atlas}_model-ifod2_desc-sift2_connectivity.json"
    "$APPTAINER" exec --cleanenv \
        -B "$QSIRECON_DIR:/out" -B "$WORK_DIR:/work" \
        "$IMAGE" tck2connectome \
        "/work/$(basename "$track")" \
        "/out/${segmentation#"$QSIRECON_DIR"/}" \
        "/out/${raw_matrix#"$QSIRECON_DIR"/}" \
        -tck_weights_in "/out/${weights#"$QSIRECON_DIR"/}" \
        -out_assignments "/out/${assignments#"$QSIRECON_DIR"/}" \
        -symmetric -zero_diagonal -nthreads "$THREADS" -force
    python3 "$SCRIPT_DIR/pad_connectome.py" \
        --matrix "$raw_matrix" --output "$matrix" \
        --segmentation "$segmentation" --labels-tsv "$labels_tsv" \
        --sidecar "$sidecar"
done
