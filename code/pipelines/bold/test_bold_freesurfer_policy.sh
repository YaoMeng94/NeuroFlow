#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/bids/sub-TEST01/func" "$tmp/fs-subjects/sub-TEST01" "$tmp/anat-fslr"
printf '{"Name":"BOLD launcher test","BIDSVersion":"1.9.0"}\n' > "$tmp/bids/dataset_description.json"
cat > "$tmp/bin/python3" <<'PYTHON_WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$1" == */preflight_bold.py ]]; then
    manifest=""
    while (($#)); do
        if [[ "$1" == "--output-manifest" ]]; then manifest="$2"; shift 2; else shift; fi
    done
    [[ -n "$manifest" ]]
    mkdir -p "$(dirname "$manifest")"
    printf '{"Groups": []}\n' > "$manifest"
    exit 0
fi
echo "Unexpected Python command: $*" >&2
exit 2
PYTHON_WRAPPER
chmod +x "$tmp/bin/python3"

base=("$tmp/bids" "$tmp/derivatives" participant --participant-label TEST01
    --stage fmriprep --nprocs 4 --omp-nthreads 2 --mem-mb 4096 --dry-run)
default_output="$(PATH="$tmp/bin:$PATH" bash "$SCRIPT_DIR/run_bold_bidsapp.sh" "${base[@]}")"
[[ "$default_output" == *"--fs-no-reconall"* ]] || {
    echo "Default fMRIPrep plan must disable redundant FreeSurfer reconstruction" >&2
    exit 1
}
[[ "$default_output" != *"--fs-subjects-dir"* ]] || {
    echo "Default fMRIPrep plan must not import an implicit subjects directory" >&2
    exit 1
}

reuse_output="$(PATH="$tmp/bin:$PATH" bash "$SCRIPT_DIR/run_bold_bidsapp.sh" "${base[@]}" --fs-subjects-dir "$tmp/fs-subjects" --anat-fs-subjects-dir "$tmp/fs-subjects")"
[[ "$reuse_output" == *"--fs-subjects-dir /fs_subjects"* ]] || {
    echo "Explicit compatible FreeSurfer subjects were not forwarded" >&2
    exit 1
}
[[ "$reuse_output" != *"--fs-no-reconall"* ]] || {
    echo "Explicit FreeSurfer reuse conflicts with --fs-no-reconall" >&2
    exit 1
}

echo "BOLD FreeSurfer reuse policy: PASS"
