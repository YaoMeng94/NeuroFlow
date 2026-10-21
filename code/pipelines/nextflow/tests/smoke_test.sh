#!/usr/bin/env bash
set -Eeuo pipefail

if (($# != 2)); then
    echo "Usage: $0 <nextflow-executable> <java-home>" >&2
    exit 2
fi
NEXTFLOW="$1"
export JAVA_HOME="$2"
export PATH="$JAVA_HOME/bin:$PATH"
export NXF_OFFLINE=true
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/neuroflow-nextflow-smoke.XXXXXX")"
cleanup() {
    local status=$?
    if ((status == 0)); then
        rm -rf "$TMP_ROOT"
    else
        printf 'Smoke-test artifacts preserved at %s\n' "$TMP_ROOT" >&2
    fi
}
trap cleanup EXIT
mkdir -p "$TMP_ROOT/bids/sub-01" "$TMP_ROOT/external/sub-01" "$TMP_ROOT/launch"
printf '{"Name":"NeuroFlow Nextflow smoke test","BIDSVersion":"1.9.0"}\n' > "$TMP_ROOT/bids/dataset_description.json"
printf 'synthetic image payload\n' > "$TMP_ROOT/external/sub-01/image.dat"
ln -s "$TMP_ROOT/external/sub-01" "$TMP_ROOT/bids/sub-01/external"
cd "$TMP_ROOT/launch"

for workflow in anat bold dwi; do
    case "$workflow" in
        anat) derivatives="freesurfer,anatprep" ;;
        bold) derivatives="fmriprep,tedana,mefmriprep" ;;
        dwi) derivatives="qsiprep,qsirecon,dwiprep" ;;
    esac
    output="$TMP_ROOT/$workflow"
    if [[ "$workflow" == dwi ]]; then
        mkdir -p "$output/qsiprep" "$output/qsirecon" "$output/dwiprep"
    fi
    export NEUROFLOW_TASK_LOG="$TMP_ROOT/$workflow-pipeline.log"
    python3 - "$TMP_ROOT" "$SCRIPT_DIR/tests/fake_pipeline_launcher.sh" "$workflow" "$output" <<'PY'
import base64
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
bundle = {
    "command": [
        "bash", sys.argv[2], str(root / "bids"), sys.argv[4],
        "participant", "--participant-label", "01", "--stage", "all",
    ],
    "environment": {"PIPELINE_KIND": sys.argv[3]},
    "participant": "01",
    "workflow": sys.argv[3],
}
(root / f"{sys.argv[3]}-bundle.txt").write_text(base64.urlsafe_b64encode(json.dumps(bundle).encode()).decode())
PY
    bundle="$(<"$TMP_ROOT/$workflow-bundle.txt")"
    mkdir -p "$output/.neuroflow/nextflow"
    common=("$NEXTFLOW" -log "$output/.neuroflow/nextflow/nextflow.log" run "$SCRIPT_DIR/main.nf"
        -work-dir "$output/.neuroflow/nextflow/work"
        -c "$SCRIPT_DIR/nextflow.config"
        --launcher_bundle_b64 "$bundle" --input_dir "$TMP_ROOT/bids" --output_dir "$output"
        --workflow "$workflow" --expected_derivatives "$derivatives"
        --nprocs 2 --mem_mb 4096 --publish_mode symlink)
    "${common[@]}"
    IFS=, read -ra derivative_list <<< "$derivatives"
    for derivative in "${derivative_list[@]}"; do
        subject_path="sub-01"
        [[ "$derivative" == freesurfer ]] && subject_path="subjects/$subject_path"
        test -f "$output/$derivative/$subject_path/pilot.txt"
    done
    grep -q "Fake pipeline output: $workflow" "$NEUROFLOW_TASK_LOG"
    "${common[@]}" -resume
    test "$(grep -c "Fake pipeline output: $workflow" "$NEUROFLOW_TASK_LOG")" -eq 1
    printf ' changed' >> "$TMP_ROOT/external/sub-01/image.dat"
    "${common[@]}" -resume
    test "$(grep -c "Fake pipeline output: $workflow" "$NEUROFLOW_TASK_LOG")" -eq 2
done
echo "Nextflow anatomy, BOLD, DWI, BIDS-aware cache invalidation, live task logs, publish, and resume smoke tests passed."
