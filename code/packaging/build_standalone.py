"""Build the native DICOM sorter bundle for the current operating system."""

from __future__ import annotations

import ast
import os
import plistlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINT = ROOT / "code" / "packaging" / "run_standalone.py"
RULES = ROOT / "code" / "neuroflow" / "data" / "dicom_sort_rules_20261016.json"
BIDS_CONFIG = ROOT / "code" / "neuroflow" / "data" / "dcm2bids_config_prisma.json"
CONVERTER_TOOLS = ROOT / "build" / "converter-tools"


def package_version() -> str:
    tree = ast.parse((ROOT / "code" / "neuroflow" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise RuntimeError("Could not find a literal neuroflow.__version__")


def finalize_macos_bundle(version: str) -> None:
    app = ROOT / "dist" / "standalone_sorter" / "NeuroFlowDICOMSorter.app"
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as stream:
        plist = plistlib.load(stream)
    plist["CFBundleShortVersionString"] = version
    plist["CFBundleVersion"] = version
    with plist_path.open("wb") as stream:
        plistlib.dump(plist, stream, sort_keys=True)

    identity = os.environ.get("NEUROFLOW_CODESIGN_IDENTITY", "-")
    sign = ["codesign", "--force", "--deep", "--sign", identity]
    if identity != "-":
        sign.extend(["--options", "runtime", "--timestamp"])
    subprocess.run([*sign, str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)


def main() -> int:
    suffix = ".exe" if sys.platform == "win32" else ""
    bundled_tools = [
        CONVERTER_TOOLS / f"{name}{suffix}"
        for name in ("dcm2bids", "dcm2bids_helper", "dcm2bids_scaffold", "dcm2niix")
    ]
    bundled_tools.append(CONVERTER_TOOLS / "THIRD-PARTY-LICENSES.txt")
    if not all(path.is_file() for path in bundled_tools):
        subprocess.run([sys.executable, str(ROOT / "code" / "packaging" / "prepare_converter_tools.py")], cwd=ROOT, check=True)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "NeuroFlowDICOMSorter",
        "--osx-bundle-identifier", "org.neuroflow.dicom-sorter",
        "--paths", str(ROOT / "code"),
        "--specpath", str(ROOT / "build" / "standalone_sorter"),
        "--collect-all", "pydicom",
        "--exclude-module", "numpy",
        "--exclude-module", "scipy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "nibabel",
        "--exclude-module", "reportlab",
        "--workpath", str(ROOT / "build" / "standalone_sorter"),
        "--distpath", str(ROOT / "dist" / "standalone_sorter"),
        "--add-data", f"{RULES}{os.pathsep}neuroflow/data",
        "--add-data", f"{BIDS_CONFIG}{os.pathsep}neuroflow/data",
    ]
    for tool in sorted(CONVERTER_TOOLS.iterdir()):
        if tool.is_file() and tool.name != "THIRD-PARTY-LICENSES.txt":
            command.extend(["--add-binary", f"{tool}{os.pathsep}tools"])
    licenses = CONVERTER_TOOLS / "THIRD-PARTY-LICENSES.txt"
    command.extend(["--add-data", f"{licenses}{os.pathsep}licenses"])
    command.append(str(ENTRY_POINT))
    result = subprocess.call(command, cwd=ROOT)
    if result != 0:
        return result
    if sys.platform == "darwin":
        finalize_macos_bundle(package_version())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
