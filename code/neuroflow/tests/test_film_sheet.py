from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import nibabel as nib
import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore[assignment,misc]

from neuroflow.film_sheet import (
    FilmSheetOptions,
    ImageVolume,
    _extract_plane,
    _orthogonal_ras,
    _plane_aspect_ratio,
    _slice_indices,
    _sequence_label,
    _suggest_axial_range_mm,
    discover_dicom_series,
    load_image_volume,
    write_film_sheet,
)

REPORTLAB_AVAILABLE = importlib.util.find_spec("reportlab") is not None
PDF_RENDERING_AVAILABLE = PdfReader is not None and REPORTLAB_AVAILABLE


def _write_dicom_series(directory: Path, count: int = 32) -> None:
    study_uid = generate_uid()
    series_uid = generate_uid()
    for index in range(count):
        instance_uid = generate_uid()
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = MRImageStorage
        file_meta.MediaStorageSOPInstanceUID = instance_uid
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        file_meta.ImplementationClassUID = generate_uid()
        path = directory / f"slice-{count - index:03d}.dcm"
        ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
        ds.SOPClassUID = MRImageStorage
        ds.SOPInstanceUID = instance_uid
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.Modality = "MR"
        ds.PatientName = "Sample^Person"
        ds.PatientID = "SYNTHETIC-ONLY"
        ds.StudyDate = "20261019"
        ds.SeriesDescription = "t1_mprage_sag_p2_iso"
        ds.ProtocolName = "tfl3d1_16"
        ds.InstanceNumber = index + 1
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [0, 0, float(index * 5)]
        ds.PixelSpacing = [1.0, 1.0]
        ds.SliceThickness = 5.0
        ds.WindowCenter = 215
        ds.WindowWidth = 430
        ds.Rows = 24
        ds.Columns = 32
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME1"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        yy, xx = np.mgrid[:24, :32]
        image = ((xx - 16) ** 2 + (yy - 12) ** 2 < 95).astype(np.int16) * (200 + index)
        ds.PixelData = image.tobytes()
        ds.save_as(path, enforce_file_format=True)


class FilmSheetTests(unittest.TestCase):
    def test_common_sequence_names(self) -> None:
        self.assertEqual(_sequence_label("t1_mprage_sag_p2_iso"), "3D T1w MPRAGE")
        self.assertEqual(_sequence_label("sub-01_acq-mp2rage_T1w"), "3D T1w MP2RAGE")
        self.assertEqual(_sequence_label("sub-01_T1w"), "3D T1w")
        self.assertEqual(_sequence_label("t1 dark-fluid"), "T1-FLAIR")
        self.assertEqual(_sequence_label("T1_DarkFluid"), "T1-FLAIR")
        self.assertEqual(_sequence_label("t1_flair_ax"), "T1-FLAIR")
        self.assertEqual(_sequence_label("t2_space"), "T2 SPACE")
        self.assertEqual(_sequence_label("t2_spc_sag_p2_iso"), "T2 SPACE")
        self.assertEqual(_sequence_label("sub-01_acq-t2w_SPACE"), "T2 SPACE")
        self.assertEqual(_sequence_label("3D SPACE"), "SPACE")
        self.assertEqual(_sequence_label("sub-01_acq-sag_T2w"), "3D T2w")
        self.assertEqual(_sequence_label("t2_flair"), "T2-FLAIR")

    @unittest.skipUnless(PDF_RENDERING_AVAILABLE, "Install ReportLab and pypdf to run PDF serialization checks.")
    def test_nifti_load_and_three_page_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            x, y, z = np.mgrid[-20:20, -18:18, -16:16]
            volume = np.asarray((x * x + y * y + z * z < 250).astype(np.float32) * 900, dtype=np.float32)
            nifti = root / "sub-synthetic_T1w.nii.gz"
            nib.save(nib.Nifti1Image(volume, np.diag([1.0, 1.0, 1.0, 1.0])), nifti)
            nifti.with_name("sub-synthetic_T1w.json").write_text(
                json.dumps({
                    "PatientName": "Synthetic Person",
                    "StudyDate": "20261019",
                    "SeriesDescription": "T1w_MPR",
                }),
                encoding="utf-8",
            )

            loaded = load_image_volume(nifti)
            self.assertEqual(loaded.data.ndim, 3)
            self.assertEqual(loaded.sequence, "3D T1w MPRAGE")
            self.assertNotIn("sub-synthetic", loaded.source_label)
            self.assertEqual(loaded.source_label, "T1w_MPR")
            self.assertEqual(loaded.patient_name, "Synthetic Person")
            self.assertEqual(loaded.study_date, "2026-10-19")
            self.assertGreater(loaded.data.shape[2], 20)
            default_pdf = write_film_sheet(loaded, root / "default-film.pdf")
            default_page = PdfReader(str(default_pdf)).pages[0]
            self.assertIn("30 slices", default_page.extract_text())
            self.assertIn("Axial", default_page.extract_text())
            self.assertNotIn("Coronal", default_page.extract_text())
            output = write_film_sheet(
                loaded,
                root / "film",
                patient_name="张三 Synthetic Person",
                study_date="2026-10-19",
                options=FilmSheetOptions(planes=("axial", "coronal", "sagittal")),
            )
            self.assertEqual(output.suffix, ".pdf")
            reader = PdfReader(str(output))
            self.assertEqual(len(reader.pages), 3)
            self.assertIn("张三 Synthetic Person", reader.pages[0].extract_text())
            self.assertIn("Axial", reader.pages[0].extract_text())
            self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 14 * 72)
            self.assertAlmostEqual(float(reader.pages[0].mediabox.height), 17 * 72)

    def test_nifti_sidecar_metadata_without_pdf_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nifti = root / "sub-synthetic_T1w.nii.gz"
            data = np.arange(8 * 8 * 8, dtype=np.float32).reshape((8, 8, 8))
            nib.save(nib.Nifti1Image(data, np.eye(4)), nifti)
            nifti.with_name("sub-synthetic_T1w.json").write_text(json.dumps({
                "PatientName": "Synthetic Person",
                "StudyDate": "20261019",
                "SeriesDescription": "T1w_MPR",
            }), encoding="utf-8")
            loaded = load_image_volume(nifti)
            self.assertEqual(loaded.patient_name, "Synthetic Person")
            self.assertEqual(loaded.study_date, "2026-10-19")
            self.assertEqual(loaded.source_label, "T1w_MPR")
            self.assertNotIn("sub-synthetic", loaded.source_label)

    @unittest.skipIf(REPORTLAB_AVAILABLE, "This check is only for environments without ReportLab.")
    def test_pdf_writer_explains_missing_reportlab(self) -> None:
        volume = ImageVolume(np.ones((2, 2, 2), dtype=np.float32), np.eye(4), "demo")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, r"pip install \.\[film-sheet\]"):
                write_film_sheet(volume, Path(tmp) / "missing-runtime")

    def test_dicom_series_discovery_load_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_dicom_series(root)
            series = discover_dicom_series(root)
            self.assertEqual(len(series), 1)
            self.assertEqual(series[0].instance_count, 32)
            self.assertEqual(series[0].patient_name, "Sample Person")
            self.assertEqual(series[0].study_date, "2026-10-19")

            loaded = load_image_volume(root, series_uid=series[0].uid)
            self.assertEqual(loaded.patient_name, "Sample Person")
            self.assertEqual(loaded.study_date, "2026-10-19")
            self.assertEqual(loaded.sequence, "3D T1w MPRAGE")
            self.assertEqual(loaded.data.ndim, 3)
            self.assertEqual(loaded.data.shape[2], 32)
            self.assertAlmostEqual(loaded.voxel_sizes[2], 5.0)
            self.assertTrue(np.isfinite(loaded.data).all())
            self.assertEqual(loaded.display_range, (0.0, 430.0))
            self.assertTrue(loaded.invert_grayscale)
            slice_means = loaded.data.mean(axis=(0, 1))
            self.assertTrue(np.all(np.diff(slice_means) > 0))

    @unittest.skipUnless(PDF_RENDERING_AVAILABLE, "Install ReportLab and pypdf to run PDF serialization checks.")
    def test_dicom_pdf_reports_available_slice_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_dicom_series(root)
            series = discover_dicom_series(root)
            loaded = load_image_volume(root, series_uid=series[0].uid)
            output = write_film_sheet(
                loaded,
                root / "dicom-film.pdf",
                options=FilmSheetOptions(slices_per_plane=40),
            )
            text = PdfReader(str(output)).pages[0].extract_text()
            self.assertIn("32 slices", text)

    def test_axial_plane_uses_radiological_left_right_order(self) -> None:
        data = np.zeros((2, 2, 2), dtype=np.int16)
        data[1, 1, 0] = 9
        plane = _extract_plane(data, "axial", 0)
        self.assertEqual(plane[0, 0], 9)

    def test_sagittal_plane_places_anterior_on_image_left(self) -> None:
        data = np.zeros((2, 2, 2), dtype=np.int16)
        data[0, 1, 1] = 9
        plane = _extract_plane(data, "sagittal", 0)
        self.assertEqual(plane[0, 0], 9)

    def test_plane_aspect_ratio_uses_voxel_spacing(self) -> None:
        plane = np.zeros((300, 200))
        self.assertAlmostEqual(_plane_aspect_ratio(plane, "coronal", (1, 1, 5)), 200 / 1500)

    def test_long_axial_coverage_trims_neck_and_accepts_user_range(self) -> None:
        data = np.zeros((32, 32, 211), dtype=np.float32)
        data[5:27, 5:27, :] = 100
        affine = np.eye(4)
        affine[2, 3] = -110
        volume = ImageVolume(data=data, affine=affine, source_label="demo_T1w.nii.gz")

        suggested = _suggest_axial_range_mm(volume)
        self.assertGreater(suggested[0], -60)
        self.assertLess(suggested[0], -45)
        self.assertAlmostEqual(suggested[1], 89.5)
        automatic_indices = _slice_indices(volume, "axial", 30)
        self.assertEqual(len(automatic_indices), 30)
        self.assertEqual(len(set(automatic_indices)), 30)

        custom_indices = _slice_indices(volume, "axial", 30, axial_range_mm=(-80, 60))
        self.assertAlmostEqual(custom_indices[0] - 110, -80, delta=1)
        self.assertAlmostEqual(custom_indices[-1] - 110, 60, delta=1)
        with self.assertRaises(ValueError):
            _slice_indices(volume, "axial", 30, axial_range_mm=(20, -20))

    def test_oblique_input_is_resampled_to_orthogonal_ras(self) -> None:
        try:
            import scipy.ndimage  # noqa: F401
        except Exception as exc:
            self.skipTest(f"SciPy resampling runtime is unavailable: {exc}")
        angle = np.deg2rad(8)
        affine = np.eye(4)
        affine[:3, :3] = ((np.cos(angle), -np.sin(angle), 0), (np.sin(angle), np.cos(angle), 0), (0, 0, 1))
        image = nib.Nifti1Image(np.ones((24, 24, 24), dtype=np.float32), affine)
        result = _orthogonal_ras(nib.as_closest_canonical(image))
        direction = result.affine[:3, :3] / np.linalg.norm(result.affine[:3, :3], axis=0)
        self.assertTrue(np.allclose(direction, np.eye(3), atol=1e-5))
        self.assertTrue(np.allclose(np.linalg.norm(result.affine[:3, :3], axis=0), 1.0))


if __name__ == "__main__":
    unittest.main()

