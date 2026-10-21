#!/usr/bin/env bash
set -Eeuo pipefail

FMRIPREP_DIR=""; INPUT_DIR=""; ANAT_FSLR_DIR=""; OUTPUT_DIR=""; WORK_DIR=""
FS_SUBJECTS_DIR=""
MRIS_CONVERT="${PRISMA_MRIS_CONVERT:-mris_convert}"
WB_COMMAND="${PRISMA_WB_COMMAND:-wb_command}"
ANTS_APPLY="${PRISMA_ANTS_APPLY:-antsApplyTransforms}"
ATLAS_ROOT="${PRISMA_HCP_ATLAS_ROOT:-}"
SMOOTHING_FWHM_MM="2"; THREADS=8; DRY_RUN=0
PARTICIPANTS=()

usage() {
    cat <<'EOF'
Usage: export_cifti_bold.sh --fmriprep-dir PATH --input-dir PATH
       --anat-fslr-dir PATH --output-dir PATH [options]

Create full-length tedana-denoised T1w NIfTI and fsLR 91k CIFTI derivatives.

Options:
  --fmriprep-dir PATH
  --input-dir PATH            post-tedana native-space GSR/non-GSR inputs
  --anat-fslr-dir PATH       anatomy derivative root containing sub-*/anat
  --fs-subjects-dir PATH     FreeSurfer subjects directory containing native white/pial surfaces
  --output-dir PATH
  --work-dir PATH             scratch directory outside the derivative dataset
  --atlas-root PATH
  --wb-command PATH
  --ants-apply PATH
  --smoothing-fwhm MM        0 disables smoothing (default: 2)
  --threads N                 Workbench/ANTs thread limit (maximum: 50)
  --participant-label LABEL [LABEL ...]  Process only selected participants
  --dry-run
EOF
}

while (($#)); do
    case "$1" in
        --fmriprep-dir) FMRIPREP_DIR="$2"; shift 2 ;;
        --input-dir) INPUT_DIR="$2"; shift 2 ;;
        --anat-fslr-dir) ANAT_FSLR_DIR="$2"; shift 2 ;;
        --fs-subjects-dir) FS_SUBJECTS_DIR="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --work-dir) WORK_DIR="$2"; shift 2 ;;
        --atlas-root) ATLAS_ROOT="$2"; shift 2 ;;
        --wb-command) WB_COMMAND="$2"; shift 2 ;;
        --ants-apply) ANTS_APPLY="$2"; shift 2 ;;
        --smoothing-fwhm) SMOOTHING_FWHM_MM="$2"; shift 2 ;;
        --threads) THREADS="$2"; shift 2 ;;
        --participant-label) shift; while [[ $# -gt 0 && "$1" != --* ]]; do PARTICIPANTS+=("${1#sub-}"); shift; done ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -d "$FMRIPREP_DIR" ]] || { echo "Missing fMRIPrep directory: $FMRIPREP_DIR" >&2; exit 1; }
[[ -d "$INPUT_DIR" ]] || { echo "Missing post-tedana input directory: $INPUT_DIR" >&2; exit 1; }
[[ -d "$ANAT_FSLR_DIR" ]] || { echo "Missing fsLR anatomy directory: $ANAT_FSLR_DIR" >&2; exit 1; }
if [[ -z "$FS_SUBJECTS_DIR" ]]; then FS_SUBJECTS_DIR="$(dirname "$ANAT_FSLR_DIR")/freesurfer/subjects"; fi
[[ -d "$FS_SUBJECTS_DIR" ]] || { echo "Missing anatomy FreeSurfer subjects directory: $FS_SUBJECTS_DIR" >&2; exit 1; }
[[ -n "$OUTPUT_DIR" ]] || { echo "--output-dir is required" >&2; exit 2; }
[[ -n "$ATLAS_ROOT" && -d "$ATLAS_ROOT" ]] || { echo "Set --atlas-root to HCP standard_mesh_atlases" >&2; exit 2; }
WORK_DIR="${WORK_DIR:-$OUTPUT_DIR/../.work/mefmriprep-cifti}"
command -v "$WB_COMMAND" >/dev/null 2>&1 || { echo "Missing wb_command: $WB_COMMAND" >&2; exit 1; }
command -v "$ANTS_APPLY" >/dev/null 2>&1 || { echo "Missing antsApplyTransforms: $ANTS_APPLY" >&2; exit 1; }
command -v "$MRIS_CONVERT" >/dev/null 2>&1 || { echo "Missing mris_convert executable: $MRIS_CONVERT" >&2; exit 1; }
[[ "$SMOOTHING_FWHM_MM" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "Invalid smoothing FWHM" >&2; exit 2; }
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] && ((THREADS <= 50)) || { echo "--threads must be between 1 and 50" >&2; exit 2; }
export OMP_NUM_THREADS="$THREADS"
export ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS="$THREADS"

ATLAS_VOLUME="$ATLAS_ROOT/Atlas_ROIs.2.nii.gz"
L_ROI="$ATLAS_ROOT/L.atlasroi.32k_fs_LR.shape.gii"; R_ROI="$ATLAS_ROOT/R.atlasroi.32k_fs_LR.shape.gii"
L_TARGET_SPHERE="$ATLAS_ROOT/L.sphere.32k_fs_LR.surf.gii"; R_TARGET_SPHERE="$ATLAS_ROOT/R.sphere.32k_fs_LR.surf.gii"
for path in "$ATLAS_VOLUME" "$L_ROI" "$R_ROI" "$L_TARGET_SPHERE" "$R_TARGET_SPHERE"; do
    [[ -f "$path" ]] || { echo "Missing HCP grayordinate resource: $path" >&2; exit 1; }
done

mkdir -p "$OUTPUT_DIR" "$WORK_DIR"
cat > "$OUTPUT_DIR/dataset_description.json" <<'EOF'
{
  "Name": "mefmriprep derivatives",
  "BIDSVersion": "1.8.0",
  "DatasetType": "derivative",
  "GeneratedBy": [{"Name": "mefmriprep", "Version": "0.3.0"}, {"Name": "tedana"}, {"Name": "Connectome Workbench"}, {"Name": "ANTs"}]
}
EOF

run() { printf '+'; printf ' %q' "$@"; printf '\n'; ((DRY_RUN)) || "$@"; }

one_file() {
    local description="$1"; shift; local matches=()
    while IFS= read -r -d '' path; do matches+=("$path"); done < <(find "$@" -print0 | sort -z)
    ((${#matches[@]} == 1)) || { echo "$description: expected one file, found ${#matches[@]}" >&2; printf '  %s\n' "${matches[@]}" >&2; return 1; }
    printf '%s\n' "${matches[0]}"
}

sidecar_for() { local path="$1"; [[ "$path" == *.nii.gz ]] && printf '%s.json\n' "${path%.nii.gz}" || printf '%s.json\n' "${path%.dtseries.nii}"; }

count=0
while IFS= read -r -d '' denoised; do
    filename="$(basename "$denoised")"
    name="${filename%_desc-tedanaGSR_bold.nii.gz}"
    variant="tedanaGSR"
    if [[ "$name" == "$filename" ]]; then
        name="${filename%_desc-tedanaNoGSR_bold.nii.gz}"
        variant="tedanaNoGSR"
    fi
    subject="${name%%_*}"
    if ((${#PARTICIPANTS[@]})); then
        selected=0
        for participant in "${PARTICIPANTS[@]}"; do
            [[ "$subject" == "sub-$participant" ]] && selected=1
        done
        ((selected)) || continue
    fi
    subject_root="$FMRIPREP_DIR/$subject"; custom_root="$ANAT_FSLR_DIR/$subject"
    fs_subject="$FS_SUBJECTS_DIR/$subject"
    [[ -d "$fs_subject/surf" ]] || { echo "Missing FreeSurfer subject surfaces: $fs_subject/surf" >&2; exit 1; }
    session=""; [[ "$name" =~ _ses-([^_]+) ]] && session="ses-${BASH_REMATCH[1]}"
    work="$WORK_DIR/${name}_${variant}"; out_func="$OUTPUT_DIR/$subject/${session:+$session/}func"; mkdir -p "$work" "$out_func"
    mask="$(one_file "$name T1w mask" "$subject_root" -type f -name "${name}_space-T1w_desc-brain_mask.nii.gz")"
    coreg="$(one_file "$name boldref-to-T1w transform" "$subject_root" -type f -name "${name}_from-boldref_to-T1w_mode-image_desc-coreg_xfm.txt")"
    transform="$(one_file "$subject T1w-to-MNI transform" "$subject_root" -type f -name "${subject}*_from-T1w_to-MNI152NLin6Asym_mode-image_xfm.h5")"
    t1w="$work/${name}_space-T1w_desc-denoised_bold.nii.gz"
    # A 3D reference is required here. Some ANTs builds reinterpret a 4D
    # reference as vector data and reverse spatial axes.
    run "$ANTS_APPLY" -d 3 -e 3 -i "$denoised" -r "$mask" -t "$coreg" -n LanczosWindowedSinc -o "$t1w"

    for hemi in L R; do
        case "$hemi" in L) structure=CORTEX_LEFT; target_sphere="$L_TARGET_SPHERE" ;; R) structure=CORTEX_RIGHT; target_sphere="$R_TARGET_SPHERE" ;; esac
        lower="lh"; [[ "$hemi" == R ]] && lower="rh"
        white="$work/${hemi}.white.native.surf.gii"
        pial="$work/${hemi}.pial.native.surf.gii"
        mid="$work/${hemi}.midthickness.native.surf.gii"
        sphere="$(one_file "$subject $hemi MSM sphere" "$custom_root" -type f -name "${subject}*_hemi-${hemi}_space-fsLR_den-164k_desc-MSMSulc_sphere.surf.gii")"
        metric_native="$work/${hemi}.native.func.gii"; metric_32k="$work/${hemi}.32k.func.gii"; mid_32k="$work/${hemi}.midthickness.32k.surf.gii"
        run "$MRIS_CONVERT" "$fs_subject/surf/${lower}.white" "$white"
        run "$MRIS_CONVERT" "$fs_subject/surf/${lower}.pial" "$pial"
        run "$WB_COMMAND" -surface-average "$mid" -surf "$white" -surf "$pial"
        run "$WB_COMMAND" -volume-to-surface-mapping "$t1w" "$mid" "$metric_native" -ribbon-constrained "$white" "$pial" -volume-roi "$mask"
        run "$WB_COMMAND" -surface-resample "$mid" "$sphere" "$target_sphere" BARYCENTRIC "$mid_32k"
        run "$WB_COMMAND" -metric-resample "$metric_native" "$sphere" "$target_sphere" ADAP_BARY_AREA "$metric_32k" -area-surfs "$mid" "$mid_32k"
        run "$WB_COMMAND" -set-structure "$metric_32k" "$structure"
        run "$WB_COMMAND" -set-structure "$mid_32k" "$structure" -surface-type ANATOMICAL -surface-secondary-type MIDTHICKNESS
    done

    mni="$work/${name}_space-MNI152NLin6Asym_res-2_desc-denoised_bold.nii.gz"
    run "$ANTS_APPLY" -d 3 -e 3 -i "$t1w" -r "$ATLAS_VOLUME" -t "$transform" -n LanczosWindowedSinc -o "$mni"
    tr="$(python3 - "$denoised" <<'PY'
import nibabel as nib, sys
print(float(nib.load(sys.argv[1]).header.get_zooms()[3]))
PY
)"
    final_vol="$out_func/${name}_space-T1w_desc-${variant}_bold.nii.gz"
    final_cifti="$out_func/${name}_space-fsLR_den-91k_desc-${variant}_bold.dtseries.nii"
    run cp "$t1w" "$final_vol"
    run "$WB_COMMAND" -cifti-create-dense-timeseries "$final_cifti" -volume "$mni" "$ATLAS_VOLUME" -left-metric "$work/L.32k.func.gii" -roi-left "$L_ROI" -right-metric "$work/R.32k.func.gii" -roi-right "$R_ROI" -timestep "$tr"

    smooth_vol=""; smooth_cifti=""
    if [[ "$SMOOTHING_FWHM_MM" != 0 && "$SMOOTHING_FWHM_MM" != 0.0 ]]; then
        smooth_vol="$out_func/${name}_space-T1w_desc-${variant}Smoothed_bold.nii.gz"
        smooth_cifti="$out_func/${name}_space-fsLR_den-91k_desc-${variant}Smoothed_bold.dtseries.nii"
        run "$WB_COMMAND" -volume-smoothing "$t1w" "$SMOOTHING_FWHM_MM" "$smooth_vol" -fwhm -roi "$mask" -fix-zeros
        run "$WB_COMMAND" -cifti-smoothing "$final_cifti" "$SMOOTHING_FWHM_MM" "$SMOOTHING_FWHM_MM" COLUMN "$smooth_cifti" -fwhm -left-surface "$work/L.midthickness.32k.surf.gii" -right-surface "$work/R.midthickness.32k.surf.gii" -fix-zeros-surface -fix-zeros-volume
    fi

    if ((DRY_RUN == 0)); then
        for output in "$final_vol" "$final_cifti"; do
            json="$(sidecar_for "$output")"; spatial="T1w"; [[ "$output" == *.dtseries.nii ]] && spatial="fsLR-91k"
            printf '{\n  "Sources": ["%s"],\n  "Description": "Full-length tedana denoised native-space BOLD transformed with the fMRIPrep boldref-to-T1w registration; no frames were removed.",\n  "SpatialReference": "%s",\n  "SurfaceRegistration": "newMSM MSMSulc sphere supplied by the anatomy pipeline",\n  "RepetitionTime": %s\n}\n' "$denoised" "$spatial" "$tr" > "$json"
        done
        for output in "$smooth_vol" "$smooth_cifti"; do
            [[ -n "$output" ]] || continue; json="$(sidecar_for "$output")"
            printf '{\n  "Sources": ["%s"],\n  "Description": "Full-length tedana denoised BOLD with spatial smoothing; no frames were removed.",\n  "SmoothingFWHM": %s,\n  "RepetitionTime": %s\n}\n' "$([[ "$output" == *.dtseries.nii ]] && echo "$final_cifti" || echo "$final_vol")" "$SMOOTHING_FWHM_MM" "$tr" > "$json"
        done
        "$WB_COMMAND" -file-information "$final_cifti" >/dev/null
    fi
    count=$((count + 1))
done < <(find "$INPUT_DIR" -type f \( -name '*_desc-tedanaGSR_bold.nii.gz' -o -name '*_desc-tedanaNoGSR_bold.nii.gz' \) -print0 | sort -z)

((count > 0)) || { echo "No post-tedana GSR/non-GSR BOLD files found" >&2; exit 1; }
echo "Created final mefmriprep derivatives for $count variants"
