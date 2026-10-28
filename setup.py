from __future__ import annotations

import os
import shutil
from pathlib import Path

import setuptools
from setuptools import find_packages, setup
from setuptools.command.build_py import build_py as _build_py


ROOT = Path(__file__).parent


def ignore_nonportable(path: str, names: list[str]) -> set[str]:
    excluded = {"__pycache__"}
    excluded.update(name for name in names if name.startswith("test_"))
    excluded.update(
        name for name in names
        if name in {"tests", "run_SCJ_dwi_postprep_node2.sh", "tune_newmsm_node1.sh"}
    )
    return excluded


class build_py(_build_py):
    """Ship the standalone launcher tree alongside the Python controller."""

    def run(self):
        super().run()
        source = ROOT / "code" / "pipelines"
        target = Path(self.build_lib) / "neuroflow" / "pipelines"
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=ignore_nonportable,
        )
        for directory, _, files in os.walk(target):
            for filename in files:
                path = Path(directory) / filename
                self._pipeline_outputs.append(str(path))

    def initialize_options(self):
        super().initialize_options()
        self._pipeline_outputs = []

    def get_outputs(self, include_bytecode=1):
        return super().get_outputs(include_bytecode) + self._pipeline_outputs


metadata = {}
if tuple(int(part) for part in setuptools.__version__.split(".")[:2]) < (61, 0):
    metadata.update(name="neuroflow", version="0.4.17")

setup(
    package_dir={"": "code"},
    packages=find_packages(where="code", include=["neuroflow*"], exclude=["neuroflow.tests*"]),
    package_data={"neuroflow": ["templates/*.html", "static/*.css", "data/*.json", "data/atlases/**/*"]},
    include_package_data=True,
    cmdclass={"build_py": build_py},
    **metadata,
)
