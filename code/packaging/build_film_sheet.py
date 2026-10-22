"""Build the MRI film sheet desktop app for the current operating system."""

from __future__ import annotations

import plistlib
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = ROOT / "code" / "packaging" / "run_film_sheet.py"
EXCLUDED_OPTIONAL_MODULES = (
    "IPython", "bokeh", "botocore", "cupy", "dask", "distributed",
    "cryptography", "h5netcdf", "h5py", "ipykernel", "jupyter", "jupyterlab",
    "lxml", "matplotlib", "nbformat", "netCDF4", "notebook", "numba",
    "numpy.testing", "openpyxl", "pandas", "psutil", "pyarrow", "pytables",
    "requests", "scipy", "statsmodels", "tables", "torch", "urllib3", "wx", "xarray",
    "zmq", "zarr",
)


def _project_version() -> str:
    project_file = ROOT / "pyproject.toml"
    text = project_file.read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
    version = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']\s*$', project.group(1) if project else "")
    if version is None:
        raise RuntimeError(f"Could not read the project version from {project_file}.")
    return version.group(1)


def _set_macos_bundle_version() -> None:
    bundle = ROOT / "dist" / "film_sheet" / "NeuroFlowMRIFilmSheet.app"
    info_path = bundle / "Contents" / "Info.plist"
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)
    version = _project_version()
    info["CFBundleName"] = "NeuroFlow MRI Film Sheet"
    info["CFBundleDisplayName"] = "NeuroFlow MRI Film Sheet"
    info["CFBundleShortVersionString"] = version
    info["CFBundleVersion"] = version
    with info_path.open("wb") as stream:
        plistlib.dump(info, stream)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)], check=True)


def main() -> int:
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "NeuroFlowMRIFilmSheet",
        "--osx-bundle-identifier", "org.neuroflow.mri-film-sheet",
        "--paths", str(ROOT / "code"),
        "--specpath", str(ROOT / "build" / "film_sheet"),
        "--workpath", str(ROOT / "build" / "film_sheet"),
        "--distpath", str(ROOT / "dist" / "film_sheet"),
        "--collect-all", "pydicom",
        "--collect-all", "reportlab",
        "--collect-all", "PIL",
        str(ENTRY_POINT),
    ]
    for module in EXCLUDED_OPTIONAL_MODULES:
        command.extend(("--exclude-module", module))
    result = subprocess.call(command, cwd=ROOT)
    if result == 0 and sys.platform == "darwin":
        _set_macos_bundle_version()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
