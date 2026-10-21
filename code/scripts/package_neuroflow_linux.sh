#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

usage() {
  cat <<'EOF'
Usage: package_neuroflow_linux.sh WHEELHOUSE OUTPUT_DIR

Create a versioned Linux x86_64 / CPython 3.11 / glibc 2.17 install bundle.
WHEELHOUSE must contain exactly one NeuroFlow wheel and a verified SHA256SUMS.
EOF
}

if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
  usage
  exit 0
fi
if [[ $# -ne 2 ]]; then
  usage >&2
  exit 2
fi

wheelhouse="$(cd "$1" 2>/dev/null && pwd -P)" || {
  echo "Wheelhouse not found: $1" >&2
  exit 2
}
output="$2"
mkdir -p "$output"
output="$(cd "$output" && pwd -P)"
[[ -z "$(find "$output" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
  echo "Output directory must be empty: $output" >&2
  exit 2
}

shopt -s nullglob
wheels=("$wheelhouse"/neuroflow-*.whl)
[[ ${#wheels[@]} -eq 1 && -f "$wheelhouse/SHA256SUMS" ]] || {
  echo "Expected one NeuroFlow wheel and SHA256SUMS in $wheelhouse" >&2
  exit 2
}
if command -v sha256sum >/dev/null 2>&1; then
  (cd "$wheelhouse" && sha256sum -c SHA256SUMS)
elif command -v shasum >/dev/null 2>&1; then
  (cd "$wheelhouse" && shasum -a 256 -c SHA256SUMS)
else
  echo "Install sha256sum or shasum to verify the wheelhouse" >&2
  exit 2
fi

version="$(python3 - "${wheels[0]}" <<'PY'
from email.parser import Parser
from zipfile import ZipFile
import sys

with ZipFile(sys.argv[1]) as archive:
    metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
    message = Parser().parsestr(archive.read(metadata).decode("utf-8"))
if message.get("Name", "").lower() != "neuroflow":
    raise SystemExit("Wheelhouse package is not NeuroFlow")
print(message["Version"])
PY
)"
bundle_name="NeuroFlow-Linux-x86_64-cp311-glibc217-$version"
archive_name="$bundle_name.tar.gz"
stage="$(mktemp -d "$output/.bundle.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
bundle="$stage/$bundle_name"
wheelhouse_destination="$bundle/dist/hpc-wheelhouses/neuroflow-$version"
mkdir -p "$bundle/code/scripts" "$bundle/docs" "$wheelhouse_destination"
cp "$ROOT/code/scripts/install_neuroflow_linux.sh" "$bundle/code/scripts/"
cp "$ROOT/code/scripts/install_neuroflow_hpc.sh" "$bundle/code/scripts/"
cp "$ROOT/code/scripts/uninstall_neuroflow_linux.sh" "$bundle/code/scripts/"
cp "$ROOT/docs/INSTALL_LINUX_HPC.md" "$bundle/docs/"
cp "$wheelhouse"/* "$wheelhouse_destination/"
if command -v xattr >/dev/null 2>&1; then
  xattr -cr "$bundle"
fi
if [[ "$(uname -s)" == Darwin ]]; then
  COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata \
    -czf "$output/$archive_name" -C "$stage" "$bundle_name"
else
  tar -czf "$output/$archive_name" -C "$stage" "$bundle_name"
fi
(
  cd "$output"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$archive_name" > "$archive_name.sha256"
  else
    shasum -a 256 "$archive_name" > "$archive_name.sha256"
  fi
)
echo "Linux install bundle ready: $output/$archive_name"
