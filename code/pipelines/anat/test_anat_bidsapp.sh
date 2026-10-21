#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
tmp="$(mktemp -d)"
tmp="$(cd "$tmp" && pwd -P)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bids/sub-TEST01/anat"
mkdir -p "$tmp/freesurfer"
touch "$tmp/freesurfer/license.txt"
printf 'export FREESURFER_HOME="%s"\n' "$tmp/freesurfer" > "$tmp/freesurfer/SetUpFreeSurfer.sh"
: > "$tmp/bids/sub-TEST01/anat/sub-TEST01_T1w.nii.gz"
preflight="$(bash "$SCRIPT_DIR/run_anat_bidsapp.sh" "$tmp/bids" "$tmp/unused-output" participant --participant-label TEST01 --stage preflight)"
[[ "$preflight" == *'"subject": "sub-TEST01"'* && "$preflight" == *'"t1w":'* ]] || { echo "Anatomy preflight did not resolve the BIDS T1w" >&2; exit 1; }
[[ ! -e "$tmp/unused-output" ]] || { echo "Anatomy preflight unexpectedly created output files" >&2; exit 1; }
output="$(bash "$SCRIPT_DIR/run_anat_bidsapp.sh" "$tmp/bids" "$tmp/derivatives" participant --participant-label TEST01 --nprocs 50 --stage recon --freesurfer-home "$tmp/freesurfer" --dry-run)"
[[ "$output" == *"--threads 50"* ]] || { echo "Explicit 50-thread request was not preserved" >&2; exit 1; }
[[ "$output" == *"--subjects-dir $tmp/derivatives/freesurfer/subjects"* ]] || { echo "FreeSurfer path is not absolute" >&2; exit 1; }
[[ "$output" == *"--t1 $tmp/bids/sub-TEST01/anat/sub-TEST01_T1w.nii.gz"* ]] || { echo "T1w path is not absolute" >&2; exit 1; }
[[ "$output" == *"--log-file $tmp/derivatives/logs/sub-TEST01.log"* ]] || { echo "Anatomy log is inside the BIDS derivative dataset" >&2; exit 1; }
[[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["BIDSVersion"])' "$tmp/derivatives/anatprep/dataset_description.json")" == "1.11.2" ]] || { echo "Anatomy derivative BIDSVersion is incorrect" >&2; exit 1; }
echo "anat BIDS App path and thread test: PASS"
