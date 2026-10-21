#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/neuroflow-bold-stage-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT
mkdir -p "$TMP_ROOT/bin" "$TMP_ROOT/bids/sub-01"

cat > "$TMP_ROOT/bin/python3" <<'PYTHON_WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
case "$1" in
    */preflight_bold.py)
        manifest=""
        while (($#)); do
            if [[ "$1" == "--output-manifest" ]]; then manifest="$2"; shift 2; else shift; fi
        done
        [[ -n "$manifest" ]]
        mkdir -p "$(dirname "$manifest")"
        printf '{"Groups": []}\n' > "$manifest"
        printf 'preflight\n' >> "$TEST_EVENTS"
        ;;
    */compute_bold_qc.py)
        manifest=""
        while (($#)); do
            if [[ "$1" == "--manifest" ]]; then manifest="$2"; shift 2; else shift; fi
        done
        [[ -f "$manifest" ]]
        printf 'qc\n' >> "$TEST_EVENTS"
        ;;
    *)
        echo "Unexpected Python command: $*" >&2
        exit 2
        ;;
esac
PYTHON_WRAPPER
chmod +x "$TMP_ROOT/bin/python3"

TEST_EVENTS="$TMP_ROOT/events"
export TEST_EVENTS
PATH="$TMP_ROOT/bin:$PATH" bash "$ROOT/code/pipelines/bold/run_bold_bidsapp.sh" \
    "$TMP_ROOT/bids" "$TMP_ROOT/derivatives" participant \
    --participant-label 01 --stage qc --nprocs 1 --omp-nthreads 1 \
    --mem-mb 1024 --work-dir "$TMP_ROOT/work"

[[ "$(<"$TEST_EVENTS")" == $'preflight\nqc' ]]
echo "BOLD QC stage preflight dependency test passed."
