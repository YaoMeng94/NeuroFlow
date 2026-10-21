import json
import tempfile
import unittest
from pathlib import Path

from neuroflow.installation import bundled_pipeline_root, initialize_site_profile, refresh_site_profile


class InstallTests(unittest.TestCase):
    def test_pipeline_bundle_resolves_to_complete_launcher_tree(self):
        root = bundled_pipeline_root()
        for relative in (
            "anat/run_anat_bidsapp.sh",
            "bold/run_bold_bidsapp.sh",
            "dwi/run_dwi_bidsapp.sh",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((root / relative).is_file())

    def test_init_site_uses_the_available_bundle_and_blank_host_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "nested" / "site.json"
            self.assertEqual(initialize_site_profile(site), site)
            profile = json.loads(site.read_text())
            self.assertEqual(Path(profile["pipeline_root"]), bundled_pipeline_root())
            for key in ("freesurfer_home", "fs_license_file", "atlas_root",
                        "wb_command", "ants_apply_transforms", "fmriprep_image",
                        "tedana_image", "qsiprep_image", "qsirecon_image"):
                self.assertEqual(profile[key], "")

    def test_init_site_refuses_to_overwrite_existing_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "site.json"
            site.write_text("existing")
            with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                initialize_site_profile(site)
            self.assertEqual(site.read_text(), "existing")

    def test_refresh_site_moves_only_an_old_bundled_pipeline_root(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "site.json"
            old_root = Path(temp) / ".venvs" / "neuroflow-0.4.11" / "lib" / "python3.11" \
                / "site-packages" / "neuroflow" / "pipelines"
            site.write_text(json.dumps({
                "pipeline_root": str(old_root),
                "fmriprep_image": "/shared/fmriprep.sif",
                "freesurfer_home": "/shared/freesurfer",
            }))

            self.assertTrue(refresh_site_profile(site))
            profile = json.loads(site.read_text())
            self.assertEqual(Path(profile["pipeline_root"]), bundled_pipeline_root().resolve())
            self.assertEqual(profile["fmriprep_image"], "/shared/fmriprep.sif")
            self.assertEqual(profile["freesurfer_home"], "/shared/freesurfer")
            self.assertFalse(refresh_site_profile(site))

    def test_refresh_site_preserves_custom_pipeline_root(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "site.json"
            custom_root = Path(temp) / "custom-pipelines"
            site.write_text(json.dumps({"pipeline_root": str(custom_root)}))
            self.assertFalse(refresh_site_profile(site))
            self.assertEqual(json.loads(site.read_text())["pipeline_root"], str(custom_root))


if __name__ == "__main__":
    unittest.main()
