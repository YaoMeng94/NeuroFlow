#!/usr/bin/env bash
set -Eeuo pipefail

# HCP-style FreeSurfer -> fs_LR registration.
# The registration is performed at 164k with MSM-Sulc and only then
# resampled to the requested low-resolution fs_LR mesh (32k by default).

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'USAGE'
Usage:
  export_fsLR32k_node1.sh [options]

Options:
  --subject ID              BIDS subject label, e.g. sub-TEST01
  --subjects-dir PATH       FreeSurfer SUBJECTS_DIR
  --output-root PATH        fsLR derivative root
  --source-t1 PATH          FreeSurfer-space T1 volume, default brain.mgz
  --source-t2 PATH          FreeSurfer-space T2 volume, default T2.mgz
  --atlas-root PATH         HCP standard_mesh_atlases root
  --msm-config PATH         HCP MSMSulc configuration
  --newmsm PATH             newMSM executable
  --selected-sphere-root PATH  Reuse verified 164k newMSM spheres from PATH/sub-*/anat
  --msm-threads N           Threads per hemisphere, default 20 (40 total with --jobs 2)
  --qc-python PATH          Python with nibabel for registration QC
  --density LABEL           Output fsLR density, default 32k
  --jobs N                  Parallel hemisphere jobs (1 or 2), default 2
  --log-file PATH           Optional log file
  --dry-run                 Print commands without executing them
  -h, --help                Show this help
USAGE
}

SUBJECT=""
SUBJECTS_DIR=""
OUTPUT_ROOT=""
ATLAS_ROOT=""
MSM_CONFIG="$SCRIPT_DIR/MSMSulcStrainFinalconf.newMSM"
NEWMSM="${PRISMA_NEWMSM:-newmsm}"
SELECTED_SPHERE_ROOT=""
MSM_THREADS=20
QC_PYTHON="${PRISMA_QC_PYTHON:-python3}"
WB_COMMAND="${PRISMA_WB_COMMAND:-wb_command}"
SOURCE_T1=""
SOURCE_T2=""
DENSITY="32k"
JOBS=2
LOG_FILE=""
DRY_RUN=0

while (($#)); do
    case "$1" in
        --subject) SUBJECT="$2"; shift 2 ;;
        --subjects-dir) SUBJECTS_DIR="$2"; shift 2 ;;
        --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
        --source-t1) SOURCE_T1="$2"; shift 2 ;;
        --source-t2) SOURCE_T2="$2"; shift 2 ;;
        --atlas-root) ATLAS_ROOT="$2"; shift 2 ;;
        --msm-config) MSM_CONFIG="$2"; shift 2 ;;
        --newmsm) NEWMSM="$2"; shift 2 ;;
        --selected-sphere-root) SELECTED_SPHERE_ROOT="$2"; shift 2 ;;
        --msm-threads) MSM_THREADS="$2"; shift 2 ;;
        --qc-python) QC_PYTHON="$2"; shift 2 ;;
        --wb-command) WB_COMMAND="$2"; shift 2 ;;
        --density) DENSITY="$2"; shift 2 ;;
        --jobs) JOBS="$2"; shift 2 ;;
        --log-file) LOG_FILE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "$DENSITY" =~ ^[0-9]+k$ ]] || { echo "--density must look like 32k" >&2; exit 2; }
[[ "$JOBS" == 1 || "$JOBS" == 2 ]] || { echo "--jobs must be 1 or 2" >&2; exit 2; }
[[ "$MSM_THREADS" =~ ^[1-9][0-9]*$ ]] || { echo "--msm-threads must be a positive integer" >&2; exit 2; }
((MSM_THREADS * JOBS <= 50)) || {
    echo "newMSM allocation exceeds the 50-thread ceiling: $MSM_THREADS x $JOBS" >&2
    exit 2
}

FS_SUBJECT="$SUBJECTS_DIR/$SUBJECT"
WORKDIR="$OUTPUT_ROOT/$SUBJECT/anat"
DERIVATIVES_PARENT="$(dirname "$OUTPUT_ROOT")"
# Keep MSM scratch files outside the public derivative dataset.
STAGE_DIR="$DERIVATIVES_PARENT/.work_$(basename "$OUTPUT_ROOT")_${SUBJECT}_${DENSITY}"
MSM_DIR="$STAGE_DIR/MSMSulc"
MSM_CONFIG_USED="$STAGE_DIR/MSMSulcStrainFinalconf.newMSM.runtime"
DONE_MARKER="$STAGE_DIR/.fslr${DENSITY}_MSMSulc_complete"
MSM_VERSION="newMSM"
if [[ -n "$SELECTED_SPHERE_ROOT" ]]; then
    SELECTED_SPHERE_ROOT="$(cd "$SELECTED_SPHERE_ROOT" && pwd -P)"
    for hemi in L R; do
        [[ -s "$SELECTED_SPHERE_ROOT/$SUBJECT/anat/${SUBJECT}_hemi-${hemi}_space-fsLR_den-164k_desc-MSMSulc_sphere.surf.gii" ]] || {
            echo "Missing selected ${hemi} newMSM sphere" >&2; exit 2;
        }
    done
fi

FS_HOME="${FREESURFER_HOME:-}"
[[ -n "$SUBJECT" ]] || { echo "Set --subject" >&2; exit 2; }
[[ -n "$SUBJECTS_DIR" && -d "$SUBJECTS_DIR" ]] || { echo "Set --subjects-dir to an existing directory" >&2; exit 2; }
[[ -n "$OUTPUT_ROOT" ]] || { echo "Set --output-root" >&2; exit 2; }
[[ -n "$ATLAS_ROOT" && -d "$ATLAS_ROOT" ]] || { echo "Set --atlas-root to HCP standard_mesh_atlases" >&2; exit 2; }
[[ -n "$FS_HOME" && -d "$FS_HOME" ]] || { echo "Set FREESURFER_HOME before launching the fsLR exporter" >&2; exit 2; }
export FREESURFER_HOME="$FS_HOME"
export SUBJECTS_DIR
command -v "$WB_COMMAND" >/dev/null 2>&1 || { echo "Workbench not found: $WB_COMMAND" >&2; exit 2; }
QC_PYTHON="$(command -v "$QC_PYTHON" 2>/dev/null || true)"
NEWMSM="$(command -v "$NEWMSM" 2>/dev/null || printf '%s' "$NEWMSM")"
[[ -n "$QC_PYTHON" ]] || { echo "QC Python not found" >&2; exit 2; }
export PATH="$FS_HOME/bin:$(dirname "$(command -v "$WB_COMMAND")"):$PATH"

# mri_vol2surf and the FreeSurfer conversion helpers require the standard
# environment. The setup script references legacy unset variables.
set +u
source "$FS_HOME/SetUpFreeSurfer.sh" >/dev/null 2>&1 || true
set -u

SOURCE_T1="${SOURCE_T1:-$FS_SUBJECT/mri/brain.mgz}"
SOURCE_T2="${SOURCE_T2:-$FS_SUBJECT/mri/T2.mgz}"
HAVE_T2=0
if [[ -f "$SOURCE_T2" ]]; then HAVE_T2=1; else SOURCE_T2=""; fi
SURFACE_MODEL="FreeSurfer 8.1.0 white/pial surfaces"
SOURCE_LIST_JSON="\"$SOURCE_T1\""
if ((HAVE_T2)); then
    SURFACE_MODEL+=" with T2-assisted pial refinement"
    SOURCE_LIST_JSON+=", \"$SOURCE_T2\""
fi

mkdir -p "$WORKDIR" "$STAGE_DIR" "$MSM_DIR"
if [[ -n "$LOG_FILE" && "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$(dirname "$LOG_FILE")"
    exec > >(tee -a "$LOG_FILE") 2>&1
fi

run() {
    printf '\n+ $'
    printf ' %q' "$@"
    printf '\n'
    if ((DRY_RUN == 0)); then "$@"; fi
}

echo "Subject      : $SUBJECT"
echo "Method       : HCP-style MSM-Sulc"
echo "MSM mesh     : 164k fs_LR"
echo "Output mesh  : $DENSITY fs_LR"
echo "FreeSurfer   : $FS_SUBJECT"
echo "Atlas root   : $ATLAS_ROOT"
echo "MSM config   : $MSM_CONFIG"
echo "newMSM       : $NEWMSM"
echo "MSM threads  : $MSM_THREADS per hemisphere"
echo "Jobs         : $JOBS"

[[ -d "$FS_SUBJECT" ]] || { echo "Missing FreeSurfer subject: $FS_SUBJECT" >&2; exit 1; }
[[ -f "$MSM_CONFIG" ]] || { echo "Missing MSM config: $MSM_CONFIG" >&2; exit 1; }
[[ -f "$SOURCE_T1" ]] || { echo "Missing T1 source volume: $SOURCE_T1" >&2; exit 1; }
if ((HAVE_T2 == 0)); then
    echo "No T2 volume available; T2w and T1w/T2w surface metrics will be omitted"
fi
command -v mris_convert >/dev/null || { echo "mris_convert not found" >&2; exit 1; }
command -v mri_vol2surf >/dev/null || { echo "mri_vol2surf not found" >&2; exit 1; }
[[ -x "$NEWMSM" ]] || { echo "newMSM binary not found or not executable: $NEWMSM" >&2; exit 1; }
[[ -x "$QC_PYTHON" ]] || { echo "QC Python not found or not executable: $QC_PYTHON" >&2; exit 1; }
[[ -f "$SCRIPT_DIR/qc_surface_registration.py" ]] || { echo "Missing registration QC script" >&2; exit 1; }
command -v "$WB_COMMAND" >/dev/null || { echo "wb_command not found: $WB_COMMAND" >&2; exit 1; }
awk '!/^--numthreads=/' "$MSM_CONFIG" > "$MSM_CONFIG_USED"
printf '%s\n' "--numthreads=$MSM_THREADS" >> "$MSM_CONFIG_USED"
echo "MSM config used: $MSM_CONFIG_USED"

for hemi in L R; do
    lower="${hemi,,}"
    for surface in white pial sphere sphere.reg; do
        [[ -f "$FS_SUBJECT/surf/${lower}h.${surface}" ]] || {
            echo "Missing native surface: $FS_SUBJECT/surf/${lower}h.${surface}" >&2; exit 1;
        }
    done
    [[ -f "$FS_SUBJECT/surf/${lower}h.sulc" ]] || {
        echo "Missing native sulc map: $FS_SUBJECT/surf/${lower}h.sulc" >&2; exit 1;
    }
    for asset in \
        "$ATLAS_ROOT/fs_${hemi}/fsaverage.${hemi}.sphere.164k_fs_${hemi}.surf.gii" \
        "$ATLAS_ROOT/fs_${hemi}/fs_${hemi}-to-fs_LR_fsaverage.${hemi}_LR.spherical_std.164k_fs_${hemi}.surf.gii" \
        "$ATLAS_ROOT/fsaverage.${hemi}_LR.spherical_std.164k_fs_LR.surf.gii" \
        "$ATLAS_ROOT/${hemi}.refsulc.164k_fs_LR.shape.gii" \
        "$ATLAS_ROOT/${hemi}.sphere.${DENSITY}_fs_LR.surf.gii"; do
        [[ -f "$asset" ]] || { echo "Missing HCP asset: $asset" >&2; exit 1; }
    done
done

export_hemi() {
    local hemi="$1" lower="$2"
    local hdir="$STAGE_DIR/$lower"
    local msm_hdir="$MSM_DIR/${hemi}"
    local native_sphere="$hdir/${SUBJECT}.${hemi}.sphere.native.surf.gii"
    local native_sphere_reg="$hdir/${SUBJECT}.${hemi}.sphere.reg.native.surf.gii"
    local fsaverage_sphere="$ATLAS_ROOT/fs_${hemi}/fsaverage.${hemi}.sphere.164k_fs_${hemi}.surf.gii"
    local def_sphere="$ATLAS_ROOT/fs_${hemi}/fs_${hemi}-to-fs_LR_fsaverage.${hemi}_LR.spherical_std.164k_fs_${hemi}.surf.gii"
    local target_sphere="$ATLAS_ROOT/fsaverage.${hemi}_LR.spherical_std.164k_fs_LR.surf.gii"
    local ref_sulc="$ATLAS_ROOT/${hemi}.refsulc.164k_fs_LR.shape.gii"
    local target_lowres="$ATLAS_ROOT/${hemi}.sphere.${DENSITY}_fs_LR.surf.gii"
    local affine_sphere="$hdir/${SUBJECT}.${hemi}.sphere.reg.reg_LR.native.surf.gii"
    local rotate_mat="$hdir/rotate.${hemi}.mat"
    local rotated_sphere="$hdir/${hemi}.sphere_rot.surf.gii"
    local sphere_rot="$hdir/${SUBJECT}.${hemi}.sphere.rot.native.surf.gii"
    local native_white="$hdir/${SUBJECT}.${hemi}.white.native.surf.gii"
    local native_pial="$hdir/${SUBJECT}.${hemi}.pial.native.surf.gii"
    local native_mid="$hdir/${SUBJECT}.${hemi}.midthickness.native.surf.gii"
    local native_thickness="$hdir/${SUBJECT}.${hemi}.thickness.native.shape.gii"
    local native_sulc_raw="$hdir/${SUBJECT}.${hemi}.sulc.raw.native.shape.gii"
    local native_sulc="$hdir/${SUBJECT}.${hemi}.sulc.native.shape.gii"
    local native_curv="$hdir/${SUBJECT}.${hemi}.curv.native.shape.gii"
    local native_t1="$hdir/${SUBJECT}.${hemi}.T1w.native.shape.gii"
    local native_t2="$hdir/${SUBJECT}.${hemi}.T2w.native.shape.gii"
    local native_t1t2="$hdir/${SUBJECT}.${hemi}.T1wT2w.native.shape.gii"
    local msm_prefix="$msm_hdir/$hemi"
    local msm_sphere="$msm_hdir/${hemi}sphere.reg.surf.gii"
    local final_sphere="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-164k_desc-MSMSulc_sphere.surf.gii"
    local white32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_white.surf.gii"
    local pial32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_pial.surf.gii"
    local mid32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_midthickness.surf.gii"
    local inflated32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_inflated.surf.gii"
    local vinflated32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_vinflated.surf.gii"
    local thickness32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_thickness.shape.gii"
    local sulc32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_sulc.shape.gii"
    local curv32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_curv.shape.gii"
    local t1_32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-T1w.shape.gii"
    local t2_32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-T2w.shape.gii"
    local t1t2_32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-T1wT2w.shape.gii"
    local va32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-vertexarea.shape.gii"
    local vol32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-wedgevolume.shape.gii"
    local areal="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-164k_desc-MSMSulcArealDistortion.shape.gii"
    local edge="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-164k_desc-MSMSulcEdgeDistortion.shape.gii"
    local strain="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-164k_desc-MSMSulcStrain.shape.gii"
    local areal32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-MSMSulcArealDistortion.shape.gii"
    local edge32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-MSMSulcEdgeDistortion.shape.gii"
    local strainj32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-MSMSulcStrainJ.shape.gii"
    local strainr32="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-MSMSulcStrainR.shape.gii"
    local native_strain32="$hdir/${SUBJECT}.${hemi}.strain32.shape.gii"
    local ref_sulc32="$hdir/${hemi}.refsulc.${DENSITY}.shape.gii"
    local qc_json="$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-MSMSulc_registration.json"

    mkdir -p "$hdir" "$msm_hdir"
    run mris_convert "$FS_SUBJECT/surf/${lower}.sphere" "$native_sphere"
    run mris_convert "$FS_SUBJECT/surf/${lower}.sphere.reg" "$native_sphere_reg"
    run mris_convert "$FS_SUBJECT/surf/${lower}.white" "$native_white"
    run mris_convert "$FS_SUBJECT/surf/${lower}.pial" "$native_pial"
    run "" -surface-average "$native_mid" -surf "$native_white" -surf "$native_pial"
    run mris_convert -c "$FS_SUBJECT/surf/${lower}.thickness" "$FS_SUBJECT/surf/${lower}.white" "$native_thickness"
    run mris_convert -c "$FS_SUBJECT/surf/${lower}.sulc" "$FS_SUBJECT/surf/${lower}.white" "$native_sulc_raw"
    run mris_convert -c "$FS_SUBJECT/surf/${lower}.curv" "$FS_SUBJECT/surf/${lower}.white" "$native_curv"

    if ((DRY_RUN == 0)); then
        local converted
        for metric in thickness sulc.raw curv; do
            case "$metric" in
                thickness) expected="$native_thickness" ;;
                sulc.raw) expected="$native_sulc_raw" ;;
                curv) expected="$native_curv" ;;
            esac
            if [[ ! -f "$expected" ]]; then
                converted="$(find "$hdir" -maxdepth 1 -type f -name "*${SUBJECT}.${hemi}.${metric}.native.shape.gii" | head -n 1)"
                [[ -n "$converted" && -f "$converted" ]] || { echo "mris_convert did not create $metric metric" >&2; exit 1; }
                mv "$converted" "$expected"
            fi
        done
    fi
    run "" -metric-math "var * -1" "$native_sulc" -var var "$native_sulc_raw"

    # Sample the FreeSurfer-space T1/T2 volumes at the cortical midpoint.
    # The ratio is a T1w/T2w myelin-sensitive proxy, not a quantitative myelin map.
    run mri_vol2surf --mov "$SOURCE_T1" --regheader "$SUBJECT" --hemi "$lower" \
        --projfrac 0.5 --interp trilinear --o "$native_t1" --out_type gii
    if ((HAVE_T2)); then
        run mri_vol2surf --mov "$SOURCE_T2" --regheader "$SUBJECT" --hemi "$lower" \
            --projfrac 0.5 --interp trilinear --o "$native_t2" --out_type gii
        run "" -metric-math "t1 / (t2 + 0.000001)" "$native_t1t2" \
            -var t1 "$native_t1" -var t2 "$native_t2"
    fi

    # HCP's affine initialization: project FreeSurfer sphere.reg through
    # fsaverage into fs_LR, then remove residual rotational mismatch.
    run "" -surface-sphere-project-unproject "$native_sphere_reg" \
        "$fsaverage_sphere" "$def_sphere" "$affine_sphere"
    run "" -surface-affine-regression "$native_sphere" "$affine_sphere" "$rotate_mat"
    run "" -surface-apply-affine "$native_sphere" "$rotate_mat" "$rotated_sphere"
    run "" -surface-modify-sphere "$rotated_sphere" 100 "$sphere_rot"

    # HCP-style nonlinear folding registration at 164k fs_LR.
    if [[ -n "$SELECTED_SPHERE_ROOT" ]]; then
        run cp "$SELECTED_SPHERE_ROOT/$SUBJECT/anat/${SUBJECT}_hemi-${hemi}_space-fsLR_den-164k_desc-MSMSulc_sphere.surf.gii" "$msm_sphere"
    else
        run "$NEWMSM" --conf="$MSM_CONFIG_USED" --inmesh="$sphere_rot" --refmesh="$target_sphere" \
            --indata="$native_sulc" --refdata="$ref_sulc" --out="$msm_prefix" --verbose
    fi

    if ((DRY_RUN == 0)); then
        [[ -f "$msm_sphere" ]] || { echo "MSM did not create $msm_sphere" >&2; exit 1; }
    fi
    run cp "$msm_sphere" "$final_sphere"

    # Retain HCP-style distortion and strain diagnostics for registration QC.
    run "" -surface-distortion "$native_sphere" "$msm_sphere" "$areal"
    run "" -surface-distortion "$native_sphere" "$msm_sphere" "$edge" -edge-method
    run "" -surface-distortion "$native_sphere" "$msm_sphere" "$strain" -local-affine-method -log2

    run "" -surface-resample "$native_white" "$msm_sphere" "$target_lowres" BARYCENTRIC "$white32"
    run "" -surface-resample "$native_pial" "$msm_sphere" "$target_lowres" BARYCENTRIC "$pial32"
    run "" -surface-average "$mid32" -surf "$white32" -surf "$pial32"
    run "" -surface-generate-inflated "$mid32" "$inflated32" "$vinflated32"
    run "" -surface-vertex-areas "$mid32" "$va32"
    run "" -surface-wedge-volume "$white32" "$pial32" "$vol32"

    metric_pairs=(
        "$native_thickness:$thickness32"
        "$native_sulc:$sulc32"
        "$native_curv:$curv32"
        "$native_t1:$t1_32"
    )
    if ((HAVE_T2)); then
        metric_pairs+=("$native_t2:$t2_32" "$native_t1t2:$t1t2_32")
    fi
    for pair in "${metric_pairs[@]}"; do
        native_metric="${pair%%:*}"
        output_metric="${pair#*:}"
        run "" -metric-resample "$native_metric" "$msm_sphere" "$target_lowres" \
            ADAP_BARY_AREA "$output_metric" -area-surfs "$native_mid" "$mid32"
    done
    run "" -metric-resample "$areal" "$msm_sphere" "$target_lowres" ADAP_BARY_AREA "$areal32" -area-surfs "$native_mid" "$mid32"
    run "" -metric-resample "$edge" "$msm_sphere" "$target_lowres" ADAP_BARY_AREA "$edge32" -area-surfs "$native_mid" "$mid32"
    run "" -metric-resample "$strain" "$msm_sphere" "$target_lowres" ADAP_BARY_AREA "$native_strain32" -area-surfs "$native_mid" "$mid32"
    run "" -metric-merge "$strainj32" -metric "$native_strain32" -column 1
    run "" -metric-merge "$strainr32" -metric "$native_strain32" -column 2
    run "" -metric-resample "$ref_sulc" "$target_sphere" "$target_lowres" BARYCENTRIC "$ref_sulc32"
    run "$QC_PYTHON" "$SCRIPT_DIR/qc_surface_registration.py" \
        --sphere "$msm_sphere" \
        --registered-sulc "$sulc32" \
        --reference-sulc "$ref_sulc32" \
        --areal-distortion "$areal32" \
        --edge-distortion "$edge32" \
        --output "$qc_json"
}

if [[ -f "$DONE_MARKER" && -f "$WORKDIR/${SUBJECT}_hemi-L_space-fsLR_den-${DENSITY}_thickness.shape.gii" \
    && -f "$WORKDIR/${SUBJECT}_hemi-R_space-fsLR_den-${DENSITY}_thickness.shape.gii" \
    && -f "$WORKDIR/${SUBJECT}_hemi-L_space-fsLR_den-164k_desc-MSMSulcStrain.json" ]]; then
    echo "MSMSulc fsLR export already completed: $DONE_MARKER"
    exit 0
fi

if ((DRY_RUN == 1 || JOBS == 1)); then
    export_hemi L lh
    export_hemi R rh
else
    export_hemi L lh & pid_l=$!
    export_hemi R rh & pid_r=$!
    rc_l=0; rc_r=0
    wait "$pid_l" || rc_l=$?
    wait "$pid_r" || rc_r=$?
    ((rc_l == 0 && rc_r == 0)) || exit 1
fi

metric_suffixes=(thickness sulc curv desc-T1w desc-vertexarea desc-wedgevolume \
    desc-MSMSulcArealDistortion desc-MSMSulcEdgeDistortion desc-MSMSulcStrainJ desc-MSMSulcStrainR)
metric_stems=(thickness sulc curv desc-T1w desc-vertexarea desc-wedgevolume \
    desc-MSMSulcArealDistortion desc-MSMSulcEdgeDistortion desc-MSMSulcStrainJ desc-MSMSulcStrainR)
if ((HAVE_T2)); then
    metric_suffixes+=(desc-T2w desc-T1wT2w)
    metric_stems+=(desc-T2w desc-T1wT2w)
fi

for hemi in L R; do
    if [[ "$hemi" == L ]]; then structure=CORTEX_LEFT; else structure=CORTEX_RIGHT; fi
    run "" -set-structure "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_white.surf.gii" \
        "$structure" -surface-type ANATOMICAL -surface-secondary-type GRAY_WHITE
    run "" -set-structure "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_pial.surf.gii" \
        "$structure" -surface-type ANATOMICAL -surface-secondary-type PIAL
    run "" -set-structure "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_midthickness.surf.gii" \
        "$structure" -surface-type ANATOMICAL -surface-secondary-type MIDTHICKNESS
    for suffix in "${metric_suffixes[@]}"; do
        run "" -set-structure "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_${suffix}.shape.gii" "$structure"
    done
done

create_cifti() {
    local stem="$1"
    run "" -cifti-create-dense-scalar "$WORKDIR/${SUBJECT}_space-fsLR_den-${DENSITY}_${stem}.dscalar.nii" \
        -left-metric "$WORKDIR/${SUBJECT}_hemi-L_space-fsLR_den-${DENSITY}_${stem}.shape.gii" \
        -right-metric "$WORKDIR/${SUBJECT}_hemi-R_space-fsLR_den-${DENSITY}_${stem}.shape.gii"
}

for metric_stem in "${metric_stems[@]}"; do
    create_cifti "$metric_stem"
done

write_sidecar() {
    local data_file="$1" description="$2" units="$3" json_file
    case "$data_file" in
        *.shape.gii) json_file="${data_file%.shape.gii}.json" ;;
        *.surf.gii) json_file="${data_file%.surf.gii}.json" ;;
        *.dscalar.nii) json_file="${data_file%.dscalar.nii}.json" ;;
        *) return 0 ;;
    esac
    cat > "$json_file" <<EOF
{
  "Description": "$description",
  "Units": "$units",
  "SpatialReference": "fsLR",
  "Density": "$(case "$(basename "$data_file")" in *_den-164k_*) printf 164k ;; *) printf '%s' "$DENSITY" ;; esac)",
  "Registration": "HCP-style affine initialization followed by MSM-Sulc at 164k fsLR",
  "SurfaceModel": "$SURFACE_MODEL",
  "Sources": [$SOURCE_LIST_JSON],
  "Software": {
    "FreeSurfer": "8.1.0",
    "ConnectomeWorkbench": "2.1.0",
    "MSM": "$MSM_VERSION ($NEWMSM)"
  }
}
EOF
}

if ((DRY_RUN == 0)); then
    for hemi in L R; do
        [[ -f "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_midthickness.surf.gii" ]] || {
            echo "Missing final ${hemi} midthickness surface" >&2; exit 1;
        }
    done
    cat > "$OUTPUT_ROOT/dataset_description.json" <<EOF
{
  "Name": "NeuroFlow anatomy surface derivatives",
  "BIDSVersion": "1.8.0",
  "DatasetType": "derivative",
  "GeneratedBy": [
    {"Name": "FreeSurfer", "Version": "8.1.0"},
    {"Name": "Connectome Workbench", "Version": "2.1.0"},
    {"Name": "newMSM", "Version": "$MSM_VERSION"},
    {"Name": "NeuroFlow anatomy fsLR pipeline", "Version": "0.4.5"}
  ],
  "SourceDatasets": [{"Name": "Prisma Test raw BIDS source"}]
}
EOF

    for hemi in L R; do
        for suffix in white pial midthickness inflated vinflated; do
            write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_${suffix}.surf.gii" \
                "${suffix} cortical surface in fsLR ${DENSITY}" "mm"
        done
        for suffix in desc-MSMSulcArealDistortion desc-MSMSulcEdgeDistortion desc-MSMSulcStrain; do
            write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-164k_${suffix}.shape.gii" \
                "MSM-Sulc registration diagnostic at the 164k fsLR registration mesh: ${suffix#desc-MSMSulc}" "log2 ratio"
        done
        for suffix in thickness sulc curv; do
            write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_${suffix}.shape.gii" \
                "FreeSurfer ${suffix} projected to fsLR ${DENSITY}" "mm"
        done
        write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-T1w.shape.gii" \
            "T1w intensity sampled at cortical midpoint and projected to fsLR" "arbitrary"
        if ((HAVE_T2)); then
            write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-T2w.shape.gii" \
                "T2w intensity sampled at cortical midpoint and projected to fsLR" "arbitrary"
            write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-T1wT2w.shape.gii" \
                "T1w/T2w myelin-sensitive intensity proxy at cortical midpoint" "arbitrary"
        fi
        write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-vertexarea.shape.gii" \
            "Area associated with each fsLR midthickness vertex" "mm^2"
        write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_desc-wedgevolume.shape.gii" \
            "White-pial wedge volume associated with each fsLR vertex" "mm^3"
        for suffix in desc-MSMSulcArealDistortion desc-MSMSulcEdgeDistortion desc-MSMSulcStrainJ desc-MSMSulcStrainR; do
            write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-${DENSITY}_${suffix}.shape.gii" \
                "MSM-Sulc registration diagnostic: ${suffix#desc-MSMSulc}" "log2 ratio"
        done
    done
    for suffix in "${metric_stems[@]}"; do
        write_sidecar "$WORKDIR/${SUBJECT}_space-fsLR_den-${DENSITY}_${suffix}.dscalar.nii" \
            "Bilateral fsLR dense scalar: ${suffix#desc-}" "see component metric sidecars"
    done
    for hemi in L R; do
        write_sidecar "$WORKDIR/${SUBJECT}_hemi-${hemi}_space-fsLR_den-164k_desc-MSMSulc_sphere.surf.gii" \
            "164k fsLR registration sphere produced by MSM-Sulc" "unit sphere"
    done

    cat > "$WORKDIR/${SUBJECT}_space-fsLR_den-${DENSITY}_desc-MSMSulc_registration.tsv" <<EOF
subject	$SUBJECT
method	HCP-style MSMSulc
registration_mesh	164k fs_LR
output_mesh	$DENSITY fs_LR
input_sphere	FreeSurfer sphere.reg
input_metric	FreeSurfer sulc (sign adjusted to HCP convention)
reference_sulc	$ATLAS_ROOT/{L,R}.refsulc.164k_fs_LR.shape.gii
msm_config_original	$MSM_CONFIG
msm_config_used	$MSM_CONFIG_USED
msm_binary	$NEWMSM
selected_sphere_root	$SELECTED_SPHERE_ROOT
msm_threads_per_hemisphere	$MSM_THREADS
EOF
    touch "$DONE_MARKER"
fi
echo "HCP-style MSMSulc fsLR export finished: $DENSITY"
