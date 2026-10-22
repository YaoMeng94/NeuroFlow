"""Download pinned, official dcm2bids and dcm2niix binaries for packaging."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "build" / "converter-tools"
RELEASES = {
    "dcm2bids": ("UNFmontreal/Dcm2Bids", "3.2.0"),
    "dcm2niix": ("rordenlab/dcm2niix", "v1.0.20260724"),
}


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "NeuroFlow-DICOM-Sorter-build"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            return
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def _select_asset(assets: list[dict], tool: str, system: str, arch: str) -> dict:
    names = [item for item in assets if item.get("name", "").lower().endswith((".zip", ".tar.gz", ".tgz", ".pkg"))]
    tokens = {"windows": ("windows", "win"), "darwin": ("macos", "mac", "darwin")}[system]
    matches = [item for item in names if any(token in item["name"].lower() for token in tokens)]
    if tool == "dcm2bids" and system == "windows":
        matches = [item for item in matches if "linux" not in item["name"].lower()]
    if arch in ("arm64", "aarch64"):
        arch_named = [item for item in matches if any(x in item["name"].lower() for x in ("arm64", "aarch64", "universal"))]
        if arch_named:
            matches = arch_named
    elif system == "darwin":
        arch_named = [item for item in matches if any(x in item["name"].lower() for x in ("x86_64", "amd64", "intel", "universal"))]
        if arch_named:
            matches = arch_named
    if len(matches) != 1:
        available = ", ".join(item.get("name", "?") for item in names)
        raise RuntimeError(f"Could not uniquely select {tool} {system}/{arch} release asset. Available archives: {available}")
    return matches[0]


def _extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(target)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tar:
            tar.extractall(target, filter="data")
    elif archive.suffix.lower() == ".pkg" and sys.platform == "darwin":
        expanded = target / "expanded-pkg"
        subprocess.run(["pkgutil", "--expand-full", str(archive), str(expanded)], check=True)
    else:
        raise RuntimeError(f"Unsupported converter archive: {archive.name}")


def main() -> int:
    if sys.platform not in ("darwin", "win32"):
        raise SystemExit("The standalone DICOM sorter is packaged for macOS and Windows only")
    system = "darwin" if sys.platform == "darwin" else "windows"
    arch = platform.machine().lower()
    if system == "windows" and arch not in ("amd64", "x86_64"):
        raise SystemExit(f"Windows sorter package currently supports x64, not {arch}")
    shutil.rmtree(DESTINATION, ignore_errors=True)
    DESTINATION.mkdir(parents=True)
    staging = ROOT / "build" / "converter-downloads"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    for tool, (repository, tag) in RELEASES.items():
        api_url = f"https://api.github.com/repos/{repository}/releases/tags/{tag}"
        request = urllib.request.Request(api_url, headers={"User-Agent": "NeuroFlow-DICOM-Sorter-build", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.load(response)
        asset = _select_asset(release.get("assets", []), tool, system, arch)
        archive = staging / asset["name"]
        _download(asset["browser_download_url"], archive)
        extracted = staging / tool
        _extract(archive, extracted)
        if tool == "dcm2bids":
            expected = {"dcm2bids", "dcm2bids_helper", "dcm2bids_scaffold"}
        else:
            expected = {"dcm2niix"}
        suffix = ".exe" if system == "windows" else ""
        candidates = {}
        for path in extracted.rglob("*"):
            if path.is_file() and path.name.lower() in {name + suffix for name in expected}:
                candidates[path.name.lower()] = path
        missing = {name + suffix for name in expected} - set(candidates)
        if missing:
            raise RuntimeError(f"{tool} archive {asset['name']} is missing executable(s): {', '.join(sorted(missing))}")
        for name in expected:
            source = candidates[name + suffix]
            destination = DESTINATION / (name + suffix)
            shutil.copyfile(source, destination)
            if system == "darwin":
                destination.chmod(0o755)
        print(f"Prepared {tool} from {repository} {tag}: {asset['name']}")
    licenses = []
    for repository, tag, filename, label in (
        ("UNFmontreal/Dcm2Bids", "3.2.0", "LICENSE.txt", "dcm2bids"),
        ("rordenlab/dcm2niix", "v1.0.20260724", "license.txt", "dcm2niix"),
    ):
        url = f"https://raw.githubusercontent.com/{repository}/{tag}/{filename}"
        request = urllib.request.Request(url, headers={"User-Agent": "NeuroFlow-DICOM-Sorter-build"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                licenses.append(f"--- {label} ({repository}, {tag}) ---\n" + response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Could not retrieve the {label} license from {url}: {exc}") from exc
    (DESTINATION / "THIRD-PARTY-LICENSES.txt").write_text("\n\n".join(licenses), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
