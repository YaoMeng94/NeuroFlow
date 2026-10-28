import json
import tempfile
import unittest
from pathlib import Path

from neuroflow.installation import (
    bundled_atlas_root, bundled_pipeline_root, initialize_site_profile, refresh_site_profile,
)


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

    def test_atlas_bundle_contains_surface_references_and_curated_parcellations(self):
        root = bundled_atlas_root()
        for relative in (
            "Atlas_ROIs.2.nii.gz",
            "L.atlasroi.32k_fs_LR.shape.gii",
            "R.atlasroi.32k_fs_LR.shape.gii",
            "fs_L/fsaverage.L.sphere.164k_fs_L.surf.gii",
            "fs_L/fs_L-to-fs_LR_fsaverage.L_LR.spherical_std.164k_fs_L.surf.gii",
            "fs_R/fsaverage.R.sphere.164k_fs_R.surf.gii",
            "fs_R/fs_R-to-fs_LR_fsaverage.R_LR.spherical_std.164k_fs_R.surf.gii",
            "fsaverage.L_LR.spherical_std.164k_fs_LR.surf.gii",
            "fsaverage.R_LR.spherical_std.164k_fs_LR.surf.gii",
            "L.sphere.32k_fs_LR.surf.gii",
            "R.sphere.32k_fs_LR.surf.gii",
            "L.refsulc.164k_fs_LR.shape.gii",
            "R.refsulc.164k_fs_LR.shape.gii",
        ):
            with self.subTest(relative=relative):
                self.assertTrue((root / relative).is_file())
        package_atlases = root.parent
        self.assertTrue((package_atlases / "Glasser360" / "dataset_description.json").is_file())
        schaefer = package_atlases / "Schaefer2018_MNI152_2mm"
        for count in (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000):
            for networks in (7, 17):
                stem = f"Schaefer2018_{count}Parcels_{networks}Networks_order"
                self.assertTrue((schaefer / f"{stem}_FSLMNI152_2mm.nii.gz").is_file())
                self.assertTrue((schaefer / f"{stem}.txt").is_file())

    def test_init_site_uses_the_available_bundle_and_blank_host_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "nested" / "site.json"
            self.assertEqual(initialize_site_profile(site), site)
            profile = json.loads(site.read_text())
            self.assertEqual(Path(profile["pipeline_root"]), bundled_pipeline_root())
            self.assertEqual(Path(profile["atlas_root"]), bundled_atlas_root())
            for key in ("freesurfer_home", "fs_license_file",
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

    def test_site_profile_load_fills_bundled_atlas_root_when_legacy_value_is_blank(self):
        from neuroflow.core import SiteProfile

        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "site.json"
            site.write_text(json.dumps({"pipeline_root": str(bundled_pipeline_root()), "atlas_root": ""}))
            loaded = SiteProfile.load(site)
            self.assertEqual(Path(loaded.atlas_root), bundled_atlas_root())

    def test_refresh_site_moves_only_an_old_bundled_pipeline_root(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "site.json"
            old_root = Path(temp) / ".venvs" / "neuroflow-0.4.11" / "lib" / "python3.11" \
                / "site-packages" / "neuroflow" / "pipelines"
            site.write_text(json.dumps({
                "pipeline_root": str(old_root),
                "atlas_root": "",
                "fmriprep_image": "/shared/fmriprep.sif",
                "freesurfer_home": "/shared/freesurfer",
            }))

            self.assertTrue(refresh_site_profile(site))
            profile = json.loads(site.read_text())
            self.assertEqual(Path(profile["pipeline_root"]), bundled_pipeline_root().resolve())
            self.assertEqual(Path(profile["atlas_root"]), bundled_atlas_root())
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
