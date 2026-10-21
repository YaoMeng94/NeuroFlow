#!/usr/bin/env bash
set -euo pipefail
[[ "$3" == participant ]]
output="$2"
shift 3
participant=""
while (($#)); do
    case "$1" in
        --participant-label) participant="$2"; shift 2 ;;
        --stage) [[ "$2" == all ]]; shift 2 ;;
        *) shift ;;
    esac
done
[[ -n "$participant" ]]
echo "Fake pipeline output: ${PIPELINE_KIND:?}"
case "$PIPELINE_KIND" in
    anat) derivatives=(freesurfer anatprep) ;;
    bold) derivatives=(fmriprep tedana mefmriprep) ;;
    dwi) derivatives=(qsiprep qsirecon dwiprep) ;;
    *) exit 2 ;;
esac
for derivative in "${derivatives[@]}"; do
    subject_path="sub-$participant"
    [[ "$derivative" == freesurfer ]] && subject_path="subjects/$subject_path"
    mkdir -p "$output/$derivative/$subject_path"
    printf '%s\n' "$PIPELINE_KIND" > "$output/$derivative/$subject_path/pilot.txt"
done
