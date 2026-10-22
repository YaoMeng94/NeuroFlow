"""Load an anatomical volume and render a clinical-style MRI film sheet.

The public seam is intentionally small: :func:`load_image_volume` converts a
DICOM series or NIfTI image into an RAS-oriented volume, and
:func:`write_film_sheet` renders that volume as a multi-page PDF.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import nibabel as nib
import numpy as np
import pydicom
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from pydicom.multival import MultiValue


Plane = Literal["axial", "coronal", "sagittal"]
PAGE_INCHES = (14.0, 17.0)
DEFAULT_COLUMNS = 5
DEFAULT_ROWS = 6
DEFAULT_SLICES = DEFAULT_COLUMNS * DEFAULT_ROWS


@dataclass(frozen=True)
class ImageVolume:
    """Three-dimensional MRI data in RAS orientation with millimetre affine."""

    data: np.ndarray
    affine: np.ndarray
    source_label: str
    sequence: str = "MRI"
    patient_name: str = ""
    study_date: str = ""
    display_range: tuple[float, float] | None = None
    invert_grayscale: bool = False

    @property
    def voxel_sizes(self) -> tuple[float, float, float]:
        return tuple(float(np.linalg.norm(self.affine[:3, axis])) for axis in range(3))  # type: ignore[return-value]


@dataclass(frozen=True)
class FilmSheetOptions:
    slices_per_plane: int = DEFAULT_SLICES
    columns: int = DEFAULT_COLUMNS
    planes: tuple[Plane, ...] = ("axial",)
    contrast: float = 1.0
    page_inches: tuple[float, float] = PAGE_INCHES
    axial_range_mm: tuple[float, float] | None = None
    axial_start_mm: float | None = None
    axial_gap_mm: float | None = None
    coronal_range_mm: tuple[float, float] | None = None
    sagittal_range_mm: tuple[float, float] | None = None


@dataclass(frozen=True)
class DicomSeries:
    uid: str
    description: str
    protocol: str
    instance_count: int
    patient_name: str
    study_date: str
    files: tuple[Path, ...]


@dataclass(frozen=True)
class _DicomFrame:
    pixels: np.ndarray
    position: np.ndarray
    orientation: np.ndarray
    pixel_spacing: tuple[float, float]
    slope: float
    intercept: float


def _date_display(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _person_name(value: object) -> str:
    text = str(value or "").strip().replace("^", " ")
    return " ".join(text.split())


def _first_float(value: object, default: float | None = None) -> float | None:
    if isinstance(value, (MultiValue, list, tuple)):
        value = value[0] if value else None
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _description(ds: pydicom.Dataset, key: str) -> str:
    return str(getattr(ds, key, "") or "").strip()


def discover_dicom_series(source: str | Path) -> list[DicomSeries]:
    """Group readable DICOM instances by study and series without reading pixels."""
    root = Path(source).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"DICOM input must be a folder: {root}")

    grouped: dict[tuple[str, str], list[tuple[Path, pydicom.Dataset]]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True, force=False)
        except Exception:
            continue
        if not getattr(ds, "SeriesInstanceUID", None):
            continue
        modality = str(getattr(ds, "Modality", "MR") or "MR").upper()
        if modality != "MR":
            continue
        key = (str(getattr(ds, "StudyInstanceUID", "")), str(ds.SeriesInstanceUID))
        grouped.setdefault(key, []).append((path, ds))

    results: list[DicomSeries] = []
    for (_, uid), entries in grouped.items():
        ds = entries[0][1]
        results.append(DicomSeries(
            uid=uid,
            description=_description(ds, "SeriesDescription") or "Unnamed series",
            protocol=_description(ds, "ProtocolName"),
            instance_count=sum(int(getattr(item, "NumberOfFrames", 1) or 1) for _, item in entries),
            patient_name=_person_name(getattr(ds, "PatientName", "")),
            study_date=_date_display(getattr(ds, "StudyDate", "")),
            files=tuple(path for path, _ in entries),
        ))
    return sorted(results, key=lambda row: (row.description.casefold(), row.uid))


def _functional_value(ds: pydicom.Dataset, frame_index: int, sequence_name: str, value_name: str, default: object = None) -> object:
    per_frame = getattr(ds, "PerFrameFunctionalGroupsSequence", None)
    if per_frame and frame_index < len(per_frame):
        seq = getattr(per_frame[frame_index], sequence_name, None)
        if seq:
            value = getattr(seq[0], value_name, None)
            if value is not None:
                return value
    shared = getattr(ds, "SharedFunctionalGroupsSequence", None)
    if shared:
        seq = getattr(shared[0], sequence_name, None)
        if seq:
            value = getattr(seq[0], value_name, None)
            if value is not None:
                return value
    direct = getattr(ds, value_name, None)
    return direct if direct is not None else default


def _read_dicom_frames(series: DicomSeries) -> list[_DicomFrame]:
    frames: list[_DicomFrame] = []
    for path in series.files:
        try:
            ds = pydicom.dcmread(path, force=False)
            pixels = np.asarray(ds.pixel_array)
        except Exception as exc:
            raise ValueError(f"Cannot decode DICOM pixel data in {path.name}: {exc}") from exc
        is_multiframe = pixels.ndim == 3
        if pixels.ndim == 2:
            pixels = pixels[np.newaxis, ...]
        elif pixels.ndim != 3:
            raise ValueError(f"Expected a single-channel MR image in {path.name}; found shape {pixels.shape}.")
        count = pixels.shape[0]
        for frame_index in range(count):
            orientation = _functional_value(ds, frame_index, "PlaneOrientationSequence", "ImageOrientationPatient")
            position = _functional_value(ds, frame_index, "PlanePositionSequence", "ImagePositionPatient")
            pixel_measures = _functional_value(ds, frame_index, "PixelMeasuresSequence", "PixelSpacing")
            # Enhanced multi-frame tags are nested; read them explicitly where the shared accessor's
            # direct-value fallback is insufficient.
            fg = getattr(ds, "PerFrameFunctionalGroupsSequence", None)
            if is_multiframe and fg and frame_index < len(fg):
                current = fg[frame_index]
                orientation = getattr(getattr(current, "PlaneOrientationSequence", [None])[0], "ImageOrientationPatient", orientation)
                position = getattr(getattr(current, "PlanePositionSequence", [None])[0], "ImagePositionPatient", position)
                pixel_measures = getattr(getattr(current, "PixelMeasuresSequence", [None])[0], "PixelSpacing", pixel_measures)
            if orientation is None or position is None or pixel_measures is None:
                raise ValueError(
                    f"DICOM geometry is missing in {path.name}. The series must include image position, orientation, and pixel spacing."
                )
            slope = float(_functional_value(ds, frame_index, "PixelValueTransformationSequence", "RescaleSlope", getattr(ds, "RescaleSlope", 1)) or 1)
            intercept = float(_functional_value(ds, frame_index, "PixelValueTransformationSequence", "RescaleIntercept", getattr(ds, "RescaleIntercept", 0)) or 0)
            frames.append(_DicomFrame(
                pixels=np.asarray(pixels[frame_index], dtype=np.float32),
                position=np.asarray(position, dtype=float),
                orientation=np.asarray(orientation, dtype=float),
                pixel_spacing=(float(pixel_measures[0]), float(pixel_measures[1])),
                slope=slope,
                intercept=intercept,
            ))

    if len(frames) < 2:
        raise ValueError("Select a complete multi-slice DICOM series with at least two image planes.")
    first = frames[0]
    if any(frame.pixels.shape != first.pixels.shape for frame in frames):
        raise ValueError("DICOM series contains inconsistent image dimensions.")
    if any(not np.allclose(frame.orientation, first.orientation, atol=1e-4) for frame in frames):
        raise ValueError("DICOM series changes slice orientation within the selected volume.")
    normal = np.cross(first.orientation[:3], first.orientation[3:])
    normal /= np.linalg.norm(normal)
    positions = np.asarray([float(np.dot(frame.position, normal)) for frame in frames])
    order = np.argsort(positions)
    positions = positions[order]
    frames = [frames[index] for index in order]
    gaps = np.diff(positions)
    if np.any(gaps <= 1e-5):
        raise ValueError("DICOM series contains duplicate slice positions.")
    slice_spacing = float(np.median(gaps))
    if len(gaps) > 1 and np.max(np.abs(gaps - slice_spacing)) > max(0.25, slice_spacing * 0.10):
        raise ValueError("DICOM slice spacing is irregular; this series does not have a regular source grid.")

    pixels = np.stack([frame.pixels * frame.slope + frame.intercept for frame in frames], axis=2)
    # DICOM pixel array axes are [row, column, slice]. Convert patient LPS to RAS.
    lps_affine = np.eye(4, dtype=float)
    lps_affine[:3, 0] = first.orientation[3:] * first.pixel_spacing[0]
    lps_affine[:3, 1] = first.orientation[:3] * first.pixel_spacing[1]
    lps_affine[:3, 2] = normal * slice_spacing
    lps_affine[:3, 3] = frames[0].position
    lps_to_ras = np.diag([-1.0, -1.0, 1.0, 1.0])
    affine = lps_to_ras @ lps_affine
    image = nib.Nifti1Image(pixels, affine)
    image = nib.as_closest_canonical(image)
    # Preserve acquired samples. Canonical orientation only permutes/flips axes;
    # it does not interpolate or resample voxel data.
    data = np.asarray(image.dataobj, dtype=np.float32)
    first_header = pydicom.dcmread(series.files[0], stop_before_pixels=True, force=False)
    center = _first_float(getattr(first_header, "WindowCenter", None))
    width = _first_float(getattr(first_header, "WindowWidth", None))
    dicom_range = None
    if center is not None and width is not None and width > 1:
        center = center * first.slope + first.intercept
        width = width * abs(first.slope)
        dicom_range = (center - width / 2, center + width / 2)
    return ImageVolume(
        data=data,
        affine=image.affine,
        source_label=series.description,
        sequence=_sequence_label(series.description, series.protocol),
        patient_name=series.patient_name,
        study_date=series.study_date,
        display_range=dicom_range,
        invert_grayscale=str(getattr(first_header, "PhotometricInterpretation", "MONOCHROME2")) == "MONOCHROME1",
    )


def _sequence_label(*values: str) -> str:
    text = " ".join(values).casefold()
    has_t1 = bool(re.search(r"(?<![a-z0-9])t1(?![a-z0-9])", text))
    has_t2 = bool(re.search(r"(?<![a-z0-9])t2(?![a-z0-9])", text))
    if re.search(r"dark[\W_]*fluid", text):
        return "T1-FLAIR"
    if "flair" in text:
        return "T1-FLAIR" if has_t1 else "T2-FLAIR" if has_t2 else "FLAIR"
    if "mp2rage" in text:
        return "3D T1w MP2RAGE"
    has_spc = bool(re.search(r"(?<![a-z0-9])spc(?![a-z0-9])", text))
    if "space" in text or has_spc:
        if "t1w" in text or has_t1:
            return "T1w SPACE"
        if "t2w" in text or has_t2:
            return "T2 SPACE"
        return "SPACE"
    if "mprage" in text or re.search(r"\bt1w[\W_]*mpr\b", text) or re.search(r"\bmpr\b", text):
        return "3D T1w MPRAGE"
    if "t1w" in text or has_t1:
        return "3D T1w"
    if "t2w" in text or has_t2:
        return "3D T2w"
    return "MRI"


def load_image_volume(path: str | Path, *, series_uid: str | None = None) -> ImageVolume:
    """Read a NIfTI file or selected DICOM series without resampling voxel data."""
    source = Path(path).expanduser().resolve()
    if source.is_dir():
        series = discover_dicom_series(source)
        if not series:
            raise ValueError(f"No DICOM image series were found in {source}.")
        if series_uid is None:
            if len(series) != 1:
                raise ValueError("The DICOM folder contains multiple series; select one before loading pixels.")
            selected = series[0]
        else:
            selected = next((row for row in series if row.uid == series_uid), None)
            if selected is None:
                raise ValueError("The selected DICOM series is no longer available. Scan the folder again.")
        return _read_dicom_frames(selected)

    if not source.is_file() or not (source.name.endswith(".nii") or source.name.endswith(".nii.gz")):
        raise ValueError("Choose a DICOM folder or a .nii/.nii.gz image.")
    try:
        image = nib.load(str(source))
    except Exception as exc:
        raise ValueError(f"Cannot read NIfTI image {source.name}: {exc}") from exc
    if len(image.shape) != 3:
        raise ValueError(f"Expected a 3D anatomical image; found dimensions {image.shape}.")
    image = nib.as_closest_canonical(image)
    data = np.asarray(image.dataobj, dtype=np.float32)
    if not np.isfinite(data).any() or float(np.nanmax(data)) <= float(np.nanmin(data)):
        raise ValueError("NIfTI image contains no usable intensity range.")
    sidecar = source.with_name(source.name[:-7] + ".json") if source.name.endswith(".nii.gz") else source.with_suffix(".json")
    metadata: dict[str, object] = {}
    if sidecar.is_file():
        try:
            import json
            candidate = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                metadata = candidate
        except (OSError, ValueError):
            metadata = {}
    sequence = _sequence_label(
        source.name,
        str(metadata.get("SeriesDescription", "")),
        str(metadata.get("ProtocolName", "")),
        str(metadata.get("SequenceName", "")),
    )
    display_source = str(metadata.get("SeriesDescription", "") or metadata.get("ProtocolName", "") or sequence)
    return ImageVolume(
        data=data,
        affine=image.affine,
        # Do not copy a BIDS filename into the PDF header; subject labels in
        # filenames are pseudonymous identifiers and are not needed on the film.
        source_label=display_source,
        sequence=sequence,
        patient_name=_person_name(metadata.get("PatientName", "")),
        study_date=_date_display(metadata.get("StudyDate", metadata.get("AcquisitionDate", ""))),
    )


def _slice_bounds(data: np.ndarray, axis: int) -> tuple[int, int]:
    finite = np.isfinite(data)
    if not finite.any():
        raise ValueError("Image contains no finite voxel values.")
    maximum = float(np.nanpercentile(data[finite], 99.9))
    minimum = float(np.nanpercentile(data[finite], 10))
    if maximum <= minimum:
        raise ValueError("Image contains no usable intensity range.")
    threshold = minimum + (maximum - minimum) * 0.04
    foreground = finite & (data > threshold)
    reduce_axes = tuple(item for item in range(3) if item != axis)
    occupancy = np.sum(foreground, axis=reduce_axes)
    peak = int(occupancy.max(initial=0))
    valid = np.flatnonzero(occupancy >= max(1, int(peak * 0.035)))
    if len(valid) < 2:
        size = data.shape[axis]
        return (0, size - 1)
    return int(valid[0]), int(valid[-1])


def _axial_normal(volume: ImageVolume) -> np.ndarray:
    normal = np.cross(volume.affine[:3, 0], volume.affine[:3, 1])
    length = float(np.linalg.norm(normal))
    if length <= 0:
        raise ValueError("Image affine has invalid axial plane geometry.")
    normal /= length
    if float(np.dot(normal, volume.affine[:3, 2])) < 0:
        normal *= -1
    return normal


def _axial_position_mm(volume: ImageVolume, index: float) -> float:
    """Return the source slice position along its own normal in millimetres."""
    center = np.array(
        ((volume.data.shape[0] - 1) / 2, (volume.data.shape[1] - 1) / 2, index, 1.0),
        dtype=float,
    )
    world = volume.affine @ center
    return float(np.dot(world[:3], _axial_normal(volume)))


def _axial_index_at_position(volume: ImageVolume, position_mm: float) -> float:
    step = float(np.dot(volume.affine[:3, 2], _axial_normal(volume)))
    return (float(position_mm) - _axial_position_mm(volume, 0)) / step


def _suggest_axial_range_mm(
    volume: ImageVolume,
    *,
    bounds: tuple[int, int] | None = None,
) -> tuple[float, float]:
    """Suggest a brain-focused axial slab, trimming long acquisitions' margins.

    Shorter head-only volumes use their full detected coverage. If occupied
    axial coverage exceeds 80 mm, the excess above an 80 mm brain-focused
    slab is trimmed mostly from the inferior end. This avoids spending several
    panels on face and upper-neck anatomy while retaining the lower posterior
    fossa and vertex. The GUI lets users edit these coordinates for local
    acquisition coverage.
    """
    low, high = bounds or _slice_bounds(volume.data, 2)
    step = float(np.dot(volume.affine[:3, 2], _axial_normal(volume)))
    first = _axial_position_mm(volume, low)
    last = _axial_position_mm(volume, high)
    extent = abs(last - first)
    if extent > 80.0:
        excess = extent - 80.0
        trim_inferior = excess * 0.85
        trim_superior = excess * 0.15
        if step > 0:
            first += trim_inferior
            last -= trim_superior
        else:
            first -= trim_superior
            last += trim_inferior
    return min(first, last), max(first, last)


def _extract_plane(data: np.ndarray, plane: Plane, index: int) -> np.ndarray:
    if plane == "axial":
        # RAS input, displayed radiologically: patient right on image left.
        return data[::-1, ::-1, index].T
    if plane == "coronal":
        return data[::-1, index, ::-1].T
    return data[index, ::-1, ::-1].T


def _plane_position_mm(volume: ImageVolume, plane: Plane, index: int) -> float:
    axis = {"sagittal": 0, "coronal": 1, "axial": 2}[plane]
    if plane == "axial":
        return _axial_position_mm(volume, index)
    normal = volume.affine[:3, axis] / np.linalg.norm(volume.affine[:3, axis])
    return float(np.dot(volume.affine[:3, 3] + index * volume.affine[:3, axis], normal))


def _slice_indices(
    volume: ImageVolume,
    plane: Plane,
    count: int,
    *,
    axial_range_mm: tuple[float, float] | None = None,
    axial_start_mm: float | None = None,
    axial_gap_mm: float | None = None,
    coronal_range_mm: tuple[float, float] | None = None,
    sagittal_range_mm: tuple[float, float] | None = None,
) -> list[int]:
    axis = {"sagittal": 0, "coronal": 1, "axial": 2}[plane]
    low, high = _slice_bounds(volume.data, axis)
    origin = _plane_position_mm(volume, plane, 0)
    step = _plane_position_mm(volume, plane, 1) - origin
    first_mm = origin + low * step
    last_mm = origin + high * step
    minimum, maximum = min(first_mm, last_mm), max(first_mm, last_mm)
    if plane == "axial" and axial_gap_mm is not None:
        start_mm = float(axial_start_mm) if axial_start_mm is not None else _suggest_axial_range_mm(volume, bounds=(low, high))[0]
        gap_mm = float(axial_gap_mm)
        if not math.isfinite(start_mm) or not math.isfinite(gap_mm) or gap_mm <= 0:
            raise ValueError("Axial start must be finite and the fixed gap must be greater than zero.")
        if start_mm < minimum or start_mm > maximum:
            raise ValueError("Axial start is outside the image coverage.")
        coordinates = start_mm + np.arange(count, dtype=float) * gap_mm
        if coordinates[-1] > maximum + abs(step) * 0.5:
            raise ValueError(
                f"Only {int(np.floor((maximum - start_mm) / gap_mm)) + 1} slices fit in the image at this gap. "
                "Reduce the image count or gap, or move the start plane."
            )
    else:
        selected_range = {
            "axial": axial_range_mm,
            "coronal": coronal_range_mm,
            "sagittal": sagittal_range_mm,
        }[plane]
        if selected_range is None:
            selected_range = _suggest_axial_range_mm(volume, bounds=(low, high)) if plane == "axial" else (minimum, maximum)
        start_mm, end_mm = (float(value) for value in selected_range)
        if not math.isfinite(start_mm) or not math.isfinite(end_mm) or end_mm < start_mm:
            raise ValueError(f"{plane.title()} coverage must have finite coordinates with the start at or below the end.")
        if end_mm == start_mm and count > 1:
            raise ValueError(f"{plane.title()} end must be greater than the start when selecting multiple images.")
        start_mm = min(max(start_mm, minimum), maximum)
        end_mm = min(max(end_mm, minimum), maximum)
        if end_mm < start_mm or (end_mm == start_mm and count > 1):
            raise ValueError(f"{plane.title()} coverage must contain more than one plane when requesting multiple images.")
        coordinates = np.linspace(start_mm, end_mm, count)
    # Map requested millimetre positions to the nearest acquired plane.
    # Several nearby targets can land on the same source slice; keep one
    # copy so the film never prints duplicate panels.
    indices = np.floor((coordinates - origin) / step + 0.5).astype(int)
    indices = np.clip(indices, low, high)
    return list(dict.fromkeys(indices.tolist()))


def _plane_coordinate_range(volume: ImageVolume, plane: Plane) -> tuple[float, float]:
    """Physical coverage along the native acquired-slice axis for a view."""
    axis = {"sagittal": 0, "coronal": 1, "axial": 2}[plane]
    low, high = _slice_bounds(volume.data, axis)
    origin = _plane_position_mm(volume, plane, 0)
    step = _plane_position_mm(volume, plane, 1) - origin
    a, b = origin + low * step, origin + high * step
    return min(a, b), max(a, b)


def _plane_aspect_ratio(plane: np.ndarray, view: Plane, voxel_sizes: tuple[float, float, float]) -> float:
    """Return physical display width / height for a plane in canonical RAS data."""
    width_spacing, height_spacing = {
        "axial": (voxel_sizes[0], voxel_sizes[1]),
        "coronal": (voxel_sizes[0], voxel_sizes[2]),
        "sagittal": (voxel_sizes[1], voxel_sizes[2]),
    }[view]
    return (plane.shape[1] * width_spacing) / (plane.shape[0] * height_spacing)


def _display_range(data: np.ndarray) -> tuple[float, float]:
    # One stable range for the full volume keeps every slice on the same grayscale window.
    sample = data[::3, ::3, ::3]
    finite = sample[np.isfinite(sample)]
    if not finite.size:
        raise ValueError("Image contains no finite voxel values.")
    low, high = np.percentile(finite, (0.5, 99.5))
    if high <= low:
        high = low + 1
    return float(low), float(high)


def _normalized_image(
    plane: np.ndarray,
    contrast: float,
    intensity_range: tuple[float, float] | None = None,
    invert: bool = False,
) -> Image.Image:
    finite = plane[np.isfinite(plane)]
    if not finite.size:
        raise ValueError("An image plane contains no usable values.")
    low, high = intensity_range or tuple(float(value) for value in np.percentile(finite, (0.5, 99.5)))
    if high <= low:
        high = low + 1
    finite_plane = np.nan_to_num(plane, nan=low, posinf=high, neginf=low)
    scaled = np.clip((finite_plane - low) / (high - low), 0, 1)
    if invert:
        scaled = 1 - scaled
    image = Image.fromarray(np.asarray(np.round(scaled * 255), dtype=np.uint8))
    if contrast != 1.0:
        image = ImageEnhance.Contrast(image).enhance(contrast)
    return image


def render_film_sheet_page_preview(
    volume: ImageVolume,
    options: FilmSheetOptions,
    plane: Plane,
    *,
    patient_name: str = "",
    study_date: str = "",
    width: int = 840,
) -> Image.Image:
    """Render a scaled, full-page preview using the PDF film-sheet layout.

    This image is only a screen preview. The PDF writer continues to draw each
    acquired source plane directly with its physical voxel aspect ratio.
    """
    if plane not in options.planes:
        raise ValueError(f"The {plane} view is not selected for this film sheet.")
    if width < 200:
        raise ValueError("Preview width must be at least 200 pixels.")

    indices = _slice_indices(
        volume,
        plane,
        options.slices_per_plane,
        axial_range_mm=options.axial_range_mm,
        axial_start_mm=options.axial_start_mm,
        axial_gap_mm=options.axial_gap_mm,
        coronal_range_mm=options.coronal_range_mm,
        sagittal_range_mm=options.sagittal_range_mm,
    )
    page_width = options.page_inches[0] * 72
    page_height = options.page_inches[1] * 72
    scale = width / page_width
    height = round(page_height * scale)
    page = Image.new("RGB", (width, height), (5, 8, 13))
    draw = ImageDraw.Draw(page)

    def point(value: float) -> int:
        return int(round(value * scale))

    def screen_y(value: float) -> int:
        return height - point(value)

    def font(points: float, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        size = max(7, point(points))
        font_paths = (
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("C:/Windows/Fonts/msyh.ttc", 0),
            ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf", 0),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
        )
        for font_path, index in font_paths:
            try:
                return ImageFont.truetype(font_path, size=size, index=index)
            except OSError:
                continue
        return ImageFont.load_default(size=size)

    margin_x = 34
    header_top = page_height - 32
    header_height = 76
    grid_top = header_top - header_height
    footer = 30
    gap_x = 9
    gap_y = 9
    label_height = 13
    columns = min(options.columns, len(indices))
    rows = math.ceil(len(indices) / columns)
    cell_width = (page_width - 2 * margin_x - gap_x * (columns - 1)) / columns
    cell_height = (grid_top - footer - gap_y * (rows - 1)) / rows
    image_height = cell_height - label_height

    foreground = (235, 240, 247)
    muted = (179, 191, 207)
    draw.text((point(margin_x), screen_y(header_top - 12)), f"{volume.sequence}  |  {plane.title()}", fill=foreground, font=font(18, bold=True), anchor="ls")
    draw.text((point(margin_x), screen_y(header_top - 32)), f"Patient: {' '.join(patient_name.split()) or 'Not entered'}", fill=muted, font=font(9), anchor="ls")
    draw.text((point(margin_x), screen_y(header_top - 47)), f"Study date: {' '.join(study_date.split()) or 'Not entered'}", fill=muted, font=font(9), anchor="ls")
    draw.text((point(page_width - margin_x), screen_y(header_top - 47)), f"{len(indices)} slices", fill=muted, font=font(9), anchor="rs")
    draw.line((point(margin_x), screen_y(grid_top + 3), point(page_width - margin_x), screen_y(grid_top + 3)), fill=(61, 74, 92), width=max(1, point(0.7)))

    display_range = volume.display_range or _display_range(volume.data)
    axis = {"sagittal": 0, "coronal": 1, "axial": 2}[plane]
    for position, index in enumerate(indices):
        row, column = divmod(position, columns)
        x = margin_x + column * (cell_width + gap_x)
        y_top = grid_top - row * (cell_height + gap_y)
        y = y_top - cell_height
        source_plane = _extract_plane(volume.data, plane, index)
        image = _normalized_image(source_plane, options.contrast, display_range, volume.invert_grayscale)
        aspect = _plane_aspect_ratio(source_plane, plane, volume.voxel_sizes)
        if aspect >= cell_width / image_height:
            draw_width = cell_width
            draw_height = cell_width / aspect
        else:
            draw_height = image_height
            draw_width = image_height * aspect
        image_x = x + (cell_width - draw_width) / 2
        image_y = y + label_height + (image_height - draw_height) / 2
        preview_size = (max(1, point(draw_width)), max(1, point(draw_height)))
        screen_image = image.resize(preview_size, Image.Resampling.LANCZOS).convert("RGB")
        page.paste(screen_image, (point(image_x), height - point(image_y + draw_height)))

        coordinate = _plane_position_mm(volume, plane, index)
        axis_label = "pos" if plane == "axial" and not np.allclose(
            volume.affine[:3, :3] / volume.voxel_sizes, np.eye(3), atol=1e-5
        ) else "xyz"[axis]
        draw.text(
            (point(x + cell_width / 2), screen_y(y + label_height)),
            f"{plane.title()}  {axis_label} {coordinate:+.0f} mm",
            fill=muted,
            font=font(8),
            anchor="mt",
        )
        markers = {
            "axial": (("R", "left"), ("L", "right"), ("A", "top"), ("P", "bottom")),
            "coronal": (("R", "left"), ("L", "right"), ("S", "top"), ("I", "bottom")),
            "sagittal": (("A", "left"), ("P", "right"), ("S", "top"), ("I", "bottom")),
        }[plane]
        marker_font = font(7, bold=True)
        draw.text((point(image_x + 5), screen_y(image_y + draw_height / 2)), markers[0][0], fill=foreground, font=marker_font, anchor="lm")
        draw.text((point(image_x + draw_width - 5), screen_y(image_y + draw_height / 2)), markers[1][0], fill=foreground, font=marker_font, anchor="rm")
        draw.text((point(image_x + draw_width / 2), screen_y(image_y + draw_height - 8)), markers[2][0], fill=foreground, font=marker_font, anchor="ms")
        draw.text((point(image_x + draw_width / 2), screen_y(image_y + 3)), markers[3][0], fill=foreground, font=marker_font, anchor="mt")

    draw.text((point(margin_x), screen_y(14)), "NeuroFlow MRI film sheet  |  grayscale image display", fill=(122, 138, 158), font=font(7), anchor="ls")
    draw.text((point(page_width - margin_x), screen_y(14)), f"{options.planes.index(plane) + 1} / {len(options.planes)}", fill=(122, 138, 158), font=font(7), anchor="rs")
    return page


def write_film_sheet(
    volume: ImageVolume,
    output: str | Path,
    *,
    patient_name: str = "",
    study_date: str = "",
    options: FilmSheetOptions | None = None,
) -> Path:
    """Write a local grayscale film sheet PDF and return its path."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError(
            "PDF output requires ReportLab. Install the film-sheet dependencies with `pip install .[film-sheet]`."
        ) from exc
    opts = options or FilmSheetOptions()
    if opts.slices_per_plane < 1 or opts.columns < 1:
        raise ValueError("Slice count and column count must be positive.")
    if not opts.planes or any(plane not in {"axial", "coronal", "sagittal"} for plane in opts.planes):
        raise ValueError("Choose at least one valid view plane.")
    plane_indices = {
        plane: _slice_indices(
            volume,
            plane,
            opts.slices_per_plane,
            axial_range_mm=opts.axial_range_mm,
            axial_start_mm=opts.axial_start_mm,
            axial_gap_mm=opts.axial_gap_mm,
            coronal_range_mm=opts.coronal_range_mm,
            sagittal_range_mm=opts.sagittal_range_mm,
        )
        for plane in opts.planes
    }
    destination = Path(output).expanduser().resolve()
    if destination.suffix.casefold() != ".pdf":
        destination = destination.with_suffix(".pdf")
    destination.parent.mkdir(parents=True, exist_ok=True)
    page_width = opts.page_inches[0] * 72
    page_height = opts.page_inches[1] * 72
    pdf = canvas.Canvas(str(destination), pagesize=(page_width, page_height), pageCompression=1)
    pdf.setTitle(f"MRI film sheet - {volume.sequence}")
    pdf.setSubject("MRI images")
    pdf.setAuthor("NeuroFlow")

    margin_x = 34
    header_top = page_height - 32
    header_height = 76
    grid_top = header_top - header_height
    footer = 30
    gap_x = 9
    gap_y = 9
    label_height = 13
    grid_left = margin_x
    name = " ".join(patient_name.split()) or "Not entered"
    date = " ".join(study_date.split()) or "Not entered"
    source_label = " ".join(volume.source_label.split())
    display_range = volume.display_range or _display_range(volume.data)
    cjk = any(ord(character) > 127 for character in name + source_label)
    metadata_font = "Helvetica"
    if cjk:
        metadata_font = "STSong-Light"
        if metadata_font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(metadata_font))

    for page_number, plane_name in enumerate(opts.planes, start=1):
        indices = plane_indices[plane_name]
        columns = min(opts.columns, len(indices))
        rows = math.ceil(len(indices) / columns)
        cell_width = (page_width - 2 * margin_x - gap_x * (columns - 1)) / columns
        cell_height = (grid_top - footer - gap_y * (rows - 1)) / rows
        image_height = cell_height - label_height
        pdf.setFillColorRGB(0.02, 0.03, 0.05)
        pdf.rect(0, 0, page_width, page_height, fill=1, stroke=0)
        pdf.setFillColorRGB(0.92, 0.94, 0.97)
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(margin_x, header_top - 12, f"{volume.sequence}  |  {plane_name.title()}")
        pdf.setFont(metadata_font, 9)
        pdf.setFillColorRGB(0.70, 0.75, 0.82)
        pdf.drawString(margin_x, header_top - 32, f"Patient: {name}")
        pdf.drawString(margin_x, header_top - 47, f"Study date: {date}")
        pdf.drawRightString(page_width - margin_x, header_top - 32, source_label[:58])
        pdf.drawRightString(page_width - margin_x, header_top - 47, f"{len(indices)} slices")
        pdf.setStrokeColorRGB(0.24, 0.29, 0.36)
        pdf.line(margin_x, grid_top + 3, page_width - margin_x, grid_top + 3)

        axis = {"sagittal": 0, "coronal": 1, "axial": 2}[plane_name]
        voxel_sizes = volume.voxel_sizes
        for position, index in enumerate(indices):
            row, column = divmod(position, columns)
            x = grid_left + column * (cell_width + gap_x)
            y_top = grid_top - row * (cell_height + gap_y)
            y = y_top - cell_height
            plane = _extract_plane(volume.data, plane_name, index)
            image = _normalized_image(plane, opts.contrast, display_range, volume.invert_grayscale)
            aspect = _plane_aspect_ratio(plane, plane_name, voxel_sizes)
            if aspect >= cell_width / image_height:
                draw_width = cell_width
                draw_height = cell_width / aspect
            else:
                draw_height = image_height
                draw_width = image_height * aspect
            image_x = x + (cell_width - draw_width) / 2
            image_y = y + label_height + (image_height - draw_height) / 2
            pdf.drawImage(ImageReader(image), image_x, image_y, width=draw_width, height=draw_height, mask="auto")
            coordinate = _plane_position_mm(volume, plane_name, index)
            axis_label = "pos" if plane_name == "axial" and not np.allclose(
                volume.affine[:3, :3] / voxel_sizes, np.eye(3), atol=1e-5
            ) else "xyz"[axis]
            coordinate_label = f"{axis_label} {coordinate:+.0f} mm"
            pdf.setFillColorRGB(0.73, 0.78, 0.85)
            pdf.setFont("Helvetica", 8)
            pdf.drawCentredString(x + cell_width / 2, y + 2, f"{plane_name.title()}  {coordinate_label}")
            # Radiological orientation markers for RAS-oriented axial/coronal/sagittal views.
            markers = {
                "axial": (("R", "left"), ("L", "right"), ("A", "top"), ("P", "bottom")),
                "coronal": (("R", "left"), ("L", "right"), ("S", "top"), ("I", "bottom")),
                "sagittal": (("A", "left"), ("P", "right"), ("S", "top"), ("I", "bottom")),
            }[plane_name]
            pdf.setFillColorRGB(0.90, 0.93, 0.97)
            pdf.setFont("Helvetica-Bold", 7)
            pdf.drawCentredString(image_x + 5, image_y + draw_height / 2, markers[0][0])
            pdf.drawCentredString(image_x + draw_width - 5, image_y + draw_height / 2, markers[1][0])
            pdf.drawCentredString(image_x + draw_width / 2, image_y + draw_height - 8, markers[2][0])
            pdf.drawCentredString(image_x + draw_width / 2, image_y + 3, markers[3][0])

        pdf.setFillColorRGB(0.48, 0.54, 0.62)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(margin_x, 14, "NeuroFlow MRI film sheet  |  grayscale image display")
        pdf.drawRightString(page_width - margin_x, 14, f"{page_number} / {len(opts.planes)}")
        pdf.showPage()
    pdf.save()
    return destination


def format_series_choice(series: DicomSeries) -> str:
    details = [series.description, f"{series.instance_count} images"]
    if series.protocol and series.protocol.casefold() != series.description.casefold():
        details.append(series.protocol)
    return "  |  ".join(details)

