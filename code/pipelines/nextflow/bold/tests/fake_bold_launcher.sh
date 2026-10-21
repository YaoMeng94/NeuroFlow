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
for derivative in fmriprep tedana mefmriprep; do
    mkdir -p "$output/$derivative/sub-$participant"
    printf 'smoke-test\n' > "$output/$derivative/sub-$participant/pilot.txt"
done
