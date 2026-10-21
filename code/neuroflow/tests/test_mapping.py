import unittest

from neuroflow.mapping import default_t1w_row


class DefaultT1wRowTests(unittest.TestCase):
    def test_selects_unique_siemens_norm_t1w(self):
        rows = [
            {"SeriesDescription": "3dT1w_mpr_sag_1mm", "ImageTypeText": ["ORIGINAL", "DIS2D"]},
            {"SeriesDescription": "3dT1w_mpr_sag_1mm", "ImageTypeText": ["ORIGINAL", "NORM", "DIS2D"]},
            {"SeriesDescription": "bold_mb", "ImageTypeText": ["ORIGINAL", "NORM"]},
        ]
        self.assertEqual(default_t1w_row(rows), 1)

    def test_accepts_mprage_label_and_image_type(self):
        rows = [{"ProtocolName": "MPRAGE", "ImageType": ["ORIGINAL", "NORM"]}]
        self.assertEqual(default_t1w_row(rows), 0)

    def test_leaves_multiple_candidates_unselected(self):
        rows = [
            {"SeriesDescription": "3dT1w_mpr_sag", "ImageTypeText": ["NORM"]},
            {"SeriesDescription": "T1w_repeat", "ImageTypeText": ["NORM"]},
        ]
        self.assertIsNone(default_t1w_row(rows))

    def test_does_not_select_non_t1_norm_series(self):
        rows = [{"SeriesDescription": "dwi_dir99", "ImageTypeText": ["NORM"]}]
        self.assertIsNone(default_t1w_row(rows))

    def test_does_not_select_t1_without_norm(self):
        rows = [{"SeriesDescription": "3dT1w_mpr_sag", "ImageTypeText": ["DIS3D"]}]
        self.assertIsNone(default_t1w_row(rows))

    def test_ignores_t1_tse_and_derived_mpr_scout(self):
        rows = [
            {"SeriesDescription": "t1_tse_dark-fluid_tra", "ImageTypeText": ["ORIGINAL", "NORM"]},
            {"SeriesDescription": "AAhead_scout_MPR_sag", "ImageTypeText": ["DERIVED", "MPR", "NORM"]},
            {"SeriesDescription": "3dT1w_mpr_sag_1mm", "ImageTypeText": ["ORIGINAL", "NORM", "DIS2D"]},
        ]
        self.assertEqual(default_t1w_row(rows), 2)


if __name__ == "__main__":
    unittest.main()
