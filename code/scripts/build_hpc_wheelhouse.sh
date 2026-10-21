#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: build_hpc_wheelhouse.sh NEUROFLOW_WHEEL OUTPUT_DIR [runtime|pipeline|test]

Resolve binary wheels for Linux x86_64, CPython 3.11. Run this on a machine
with PyPI access. The output directory must not exist or be empty.
EOF
}

if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
  usage
  exit 0
fi
if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage >&2
  exit 2
fi

wheel="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
output="$2"
profile="${3:-runtime}"

[[ -f "$wheel" ]] || { echo "NeuroFlow wheel not found: $wheel" >&2; exit 2; }
case "$profile" in
  runtime) extras=""; extra_requirements=() ;;
  pipeline)
    extras="[pipeline]"
    extra_requirements=("importlib-resources>=5.12")
    ;;
  test)
    extras="[test]"
    # Nibabel declares this only for Python <3.12; the builder itself may run on 3.12+.
    extra_requirements=("importlib-resources>=5.12")
    ;;
  *) echo "Profile must be runtime, pipeline, or test" >&2; exit 2 ;;
esac

if [[ -e "$output" ]]; then
  [[ -d "$output" ]] || { echo "Output path is not a directory: $output" >&2; exit 2; }
  [[ -z "$(find "$output" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
    echo "Output directory must be empty: $output" >&2
    exit 2
  }
else
  mkdir -p "$output"
fi
output="$(cd "$output" && pwd -P)"

version="$(python3 - "$wheel" <<'PY'
from email.parser import Parser
from zipfile import ZipFile
import sys

with ZipFile(sys.argv[1]) as archive:
    metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
    message = Parser().parsestr(archive.read(metadata).decode("utf-8"))
if message.get("Name", "").lower() != "neuroflow":
    raise SystemExit("Input wheel is not NeuroFlow")
print(message["Version"])
PY
)"

requirement="${wheel}${extras}"
python3 -m pip download \
  --dest "$output" \
  --platform manylinux2014_x86_64 \
  --python-version 3.11 \
  --implementation cp \
  --abi cp311 \
  --only-binary=:all: \
  "$requirement" \
  "${extra_requirements[@]}"

(
  cd "$output"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum ./*.whl > SHA256SUMS
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 ./*.whl > SHA256SUMS
  else
    echo "Install sha256sum or shasum to write the wheel checksums" >&2
    exit 2
  fi
)
echo "Wheelhouse ready: $output (NeuroFlow $version, Linux x86_64, CPython 3.11, $profile)"
