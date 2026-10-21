#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
INSTALLER="$SCRIPT_DIR/install_neuroflow_hpc.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

if (($# > 1)); then
  echo "Usage: test_install_neuroflow_hpc.sh [COMPLETE_WHEELHOUSE]" >&2
  exit 2
fi

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

bash "$INSTALLER" --help >"$TEMP_DIR/help.txt"
grep -Fq 'Install NeuroFlow offline' "$TEMP_DIR/help.txt"
expect_failure 'Usage:' bash "$INSTALLER"
expect_failure 'Wheelhouse directory not found' bash "$INSTALLER" "$TEMP_DIR/missing"

mkdir "$TEMP_DIR/empty-wheelhouse"
expect_failure 'Expected exactly one NeuroFlow wheel' bash "$INSTALLER" \
  "$TEMP_DIR/empty-wheelhouse" runtime "$TEMP_DIR/venv"

mkdir -p "$TEMP_DIR/occupied-wheelhouse"
touch "$TEMP_DIR/occupied-wheelhouse/neuroflow-0.0.0-py3-none-any.whl"
touch "$TEMP_DIR/occupied-wheelhouse/SHA256SUMS"
mkdir "$TEMP_DIR/existing-venv"
expect_failure 'Refusing to replace existing environment' bash "$INSTALLER" \
  "$TEMP_DIR/occupied-wheelhouse" runtime "$TEMP_DIR/existing-venv"
test -d "$TEMP_DIR/existing-venv"

if (($# == 1)); then
  source_wheelhouse="$(cd "$1" && pwd -P)"
  mkdir "$TEMP_DIR/incomplete-wheelhouse"
  cp "$source_wheelhouse"/neuroflow-*.whl "$TEMP_DIR/incomplete-wheelhouse/"
  (cd "$TEMP_DIR/incomplete-wheelhouse" && sha256sum neuroflow-*.whl > SHA256SUMS)
  failed_venv="$TEMP_DIR/failed-install"
  if bash "$INSTALLER" "$TEMP_DIR/incomplete-wheelhouse" runtime "$failed_venv" >"$TEMP_DIR/install-failure.log" 2>&1; then
    echo "Expected offline dependency installation to fail with an incomplete wheelhouse." >&2
    exit 1
  fi
  test ! -e "$failed_venv"
  grep -Fq 'Removed incomplete NeuroFlow environment after installation failure' "$TEMP_DIR/install-failure.log"
fi

echo 'HPC offline installer validation and overwrite guards: PASS'
