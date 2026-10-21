"""Per-user NeuroFlow installation and launcher-bundle paths."""

from __future__ import annotations

import json
from pathlib import Path

from .core import PlanError, SiteProfile


def bundled_pipeline_root() -> Path:
    """Find the installed launcher bundle, falling back to the source tree."""
    installed = Path(__file__).resolve().parent / "pipelines"
    if installed.is_dir():
        return installed
    checkout = Path(__file__).resolve().parents[2] / "code" / "pipelines"
    if checkout.is_dir():
        return checkout
    raise PlanError("The NeuroFlow pipeline bundle is missing from this installation")


def initialize_site_profile(path: Path) -> Path:
    """Write a per-user profile without replacing an existing configuration."""
    if path.exists():
        raise PlanError(f"Refusing to replace site profile: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = SiteProfile(pipeline_root=str(bundled_pipeline_root()))
    path.write_text(json.dumps(profile.__dict__, indent=2) + "\n")
    return path


def refresh_site_profile(path: Path) -> bool:
    """Move only a previously bundled pipeline root to this installed version."""
    try:
        profile = json.loads(path.read_text())
        if not isinstance(profile, dict):
            raise ValueError("site profile must be a JSON object")
        configured = profile.get("pipeline_root")
        if not isinstance(configured, str) or not configured:
            raise ValueError("pipeline_root must be a nonempty path")
        old_root = Path(configured).expanduser()
        parts = tuple(part.casefold() for part in old_root.parts)
        is_bundled = (
            old_root.name.casefold() == "pipelines"
            and old_root.parent.name.casefold() == "neuroflow"
            and "site-packages" in parts
        )
        if not is_bundled:
            return False
        new_root = bundled_pipeline_root().resolve()
        if old_root.resolve() == new_root:
            return False
        profile["pipeline_root"] = str(new_root)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(profile, indent=2) + "\n")
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
        return True
    except (OSError, TypeError, ValueError) as exc:
        raise PlanError(f"Cannot refresh site profile {path}: {exc}") from exc
