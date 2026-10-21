#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BUILDER="$SCRIPT_DIR/build_hpc_wheelhouse.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

expect_failure() {
  local expected="$1"
  shift
  if "$@" >"$TEMP_DIR/command.log" 2>&1; then
    echo "Expected command to fail: $*" >&2
    exit 1
  fi
  if ! grep -Fq "$expected" "$TEMP_DIR/command.log"; then
    cat "$TEMP_DIR/command.log" >&2
    echo "Expected error text not found: $expected" >&2
    exit 1
  fi
}

bash "$BUILDER" --help >"$TEMP_DIR/help.txt"
grep -Fq 'Linux x86_64, CPython 3.11' "$TEMP_DIR/help.txt"
expect_failure 'Usage:' bash "$BUILDER"
expect_failure 'NeuroFlow wheel not found' bash "$BUILDER" "$TEMP_DIR/missing.whl" "$TEMP_DIR/out"

touch "$TEMP_DIR/placeholder.whl"
expect_failure 'Profile must be runtime, pipeline, or test' bash "$BUILDER" \
  "$TEMP_DIR/placeholder.whl" "$TEMP_DIR/out" invalid

mkdir "$TEMP_DIR/nonempty"
touch "$TEMP_DIR/nonempty/keep"
expect_failure 'Output directory must be empty' bash "$BUILDER" \
  "$TEMP_DIR/placeholder.whl" "$TEMP_DIR/nonempty" runtime
test -f "$TEMP_DIR/nonempty/keep"

echo 'HPC wheelhouse builder argument and overwrite guards: PASS'
