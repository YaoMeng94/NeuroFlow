import json
import sys
import tempfile
import unittest
from pathlib import Path

from neuroflow.core import JobSpec, PlanError, SiteProfile, plan_job
from neuroflow.mapping import build_mapping


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bids = self.root / "input BIDS"
        (self.bids / "sub-01").mkdir(parents=True)
        (self.bids / "dataset_description.json").write_text('{"Name":"test","BIDSVersion":"1.8.0"}')
        self.output = self.root / "derivatives"
        self.pipeline = self.root / "pipelines"
        for name in ("anat", "bold", "dwi"):
            folder = self.pipeline / name
            folder.mkdir(parents=True)
            (folder / f"run_{name}_bidsapp.sh").write_text("#!/bin/sh\n")
        (self.pipeline / "nextflow").mkdir(parents=True)
        (self.pipeline / "nextflow" / "main.nf").write_text("// test\n")
        self.fs_home = self.root / "freesurfer"
        self.fs_home.mkdir()
        self.image = self.root / "image.sif"
        self.image.write_bytes(b"test")
        self.newmsm = self.root / "newmsm"
        self.newmsm.write_bytes(b"test")
        self.atlas_root = self.root / "atlas"
        self.atlas_root.mkdir()
        self.wb_command = self.root / "wb_command"
        self.wb_command.write_text("#!/bin/sh\n")
        self.wb_command.chmod(0o755)
        self.ants_apply = self.root / "antsApplyTransforms"
        self.ants_apply.write_text("#!/bin/sh\n")
        self.ants_apply.chmod(0o755)
        self.nextflow = self.root / "nextflow"
        self.nextflow.write_text("#!/bin/sh\n")
        self.nextflow.chmod(0o755)
        self.java_home = self.root / "java"
        self.java_home.mkdir()
        self.site = SiteProfile(
            pipeline_root=str(self.pipeline), freesurfer_home=str(self.fs_home),
            newmsm=str(self.newmsm), atlas_root=str(self.atlas_root),
            wb_command=str(self.wb_command), qc_python=sys.executable,
            ants_apply_transforms=str(self.ants_apply), fmriprep_image=str(self.image),
            tedana_image=str(self.image), qsiprep_image=str(self.image),
            qsirecon_image=str(self.image), nextflow=str(self.nextflow),
            nextflow_java_home=str(self.java_home),
        )

    def spec(self, workflow="dwi", **changes):
        values = dict(workflow=workflow, stage="all", input_dir=str(self.bids),
                      output_dir=str(self.output), participant="01")
        values.update(changes)
        return JobSpec.from_mapping(values)

    def test_dwi_plan_preserves_path_with_spaces_and_limits(self):
        plan = plan_job(self.spec(atlases="Brainnetome246Ext,Glasser360"), self.site)
        self.assertIn(str(self.bids.resolve()), plan.command)
        self.assertEqual(plan.command.count("--atlases"), 1)
        self.assertEqual(plan.command[plan.command.index("--atlases") + 1], "Brainnetome246Ext")
        self.assertFalse(self.output.exists(), "Planning must not create derivatives")

    def test_dwi_defaults_to_brainnetome_and_glasser_atlases(self):
        spec = JobSpec.from_mapping({
            "workflow": "dwi", "stage": "connectome", "input_dir": str(self.bids),
            "output_dir": str(self.output), "participant": "01",
        })
        self.assertEqual(spec.atlases, ("Brainnetome246Ext", "Glasser360"))

    def test_existing_bids_input_skips_conversion(self):
        plan = plan_job(self.spec("bold"), self.site)
        self.assertTrue(plan.command[1].endswith("run_bold_bidsapp.sh"))
        self.assertNotIn("dcm2bids", plan.command)

    def test_multi_stage_plan_uses_pipeline_order(self):
        patch = self.root / "merge_patch.py"
        patch.write_text("# test\n")
        site = SiteProfile(**{**self.site.__dict__, "qsiprep_merge_patch": str(patch)})
        plan = plan_job(self.spec(stages=("connectome", "qsiprep", "qsirecon")), site)
        self.assertEqual(plan.spec.stages, ("qsiprep", "qsirecon", "connectome"))
        self.assertEqual([command[command.index("--stage") + 1] for command in plan.commands],
                         ["qsiprep", "qsirecon", "connectome"])
        self.assertEqual(plan.environment["PRISMA_QSIPREP_DWI_MERGE_PATCH"], str(patch.resolve()))
        self.assertEqual(len(plan.as_dict()["commands"]), 3)
        gap = plan_job(self.spec(stages=("preflight", "verify")), self.site)
        self.assertTrue(any("Skipped stages" in warning for warning in gap.warnings))
        contiguous = plan_job(self.spec(stages=("qsiprep", "qsirecon")), self.site)
        self.assertFalse(any("Skipped stages" in warning for warning in contiguous.warnings))

    def test_all_is_exclusive_and_dicom_requires_separate_jobs(self):
        with self.assertRaisesRegex(PlanError, "cannot be combined"):
            plan_job(self.spec("bold", stages=("all", "qc")), self.site)
        with self.assertRaisesRegex(PlanError, "separate reviewed jobs"):
            plan_job(self.spec("dicom", stages=("inventory", "convert")), self.site)

    def test_anatomy_uses_nprocs_and_msm_threads_without_omp_override(self):
        plan = plan_job(self.spec("anat", nprocs=16, omp_nthreads=8, msm_threads=5), self.site)
        self.assertEqual(plan.command[plan.command.index("--nprocs") + 1], "16")
        self.assertEqual(plan.command[plan.command.index("--msm-threads") + 1], "5")
        self.assertNotIn("--omp-nthreads", plan.command)
        self.assertNotIn("--mem-mb", plan.command)
        recon = plan_job(self.spec("anat", stage="recon", nprocs=2), self.site)
        self.assertNotIn("--msm-threads", recon.command)

    def test_anatomy_forwards_user_configured_surface_tools(self):
        plan = plan_job(self.spec("anat"), self.site)
        for option, expected in (("--atlas-root", self.atlas_root),
                                 ("--wb-command", self.wb_command),
                                 ("--qc-python", Path(sys.executable))):
            self.assertEqual(plan.command[plan.command.index(option) + 1], str(expected.resolve()))

    def test_anatomy_requires_an_explicit_atlas_root(self):
        site = SiteProfile(**{**self.site.__dict__, "atlas_root": ""})
        with self.assertRaisesRegex(PlanError, "atlas_root"):
            plan_job(self.spec("anat"), site)

    def test_anatomy_preflight_does_not_require_freesurfer(self):
        site = SiteProfile(pipeline_root=str(self.pipeline))
        plan = plan_job(self.spec("anat", stage="preflight"), site)
        self.assertEqual(plan.command[plan.command.index("--stage") + 1], "preflight")
        self.assertNotIn("--freesurfer-home", plan.command)

    def test_bold_forwards_user_configured_cifti_tools(self):
        plan = plan_job(self.spec("bold"), self.site)
        for option, expected in (("--atlas-root", self.atlas_root),
                                 ("--wb-command", self.wb_command),
                                 ("--ants-apply", self.ants_apply)):
            self.assertEqual(plan.command[plan.command.index(option) + 1], str(expected.resolve()))

    def test_bold_and_dwi_parameter_forwarding(self):
        bold = plan_job(self.spec("bold", tedana_fittype="curvefit", ica_method="robustica",
                                  comparison_echo=2, comparison_te_ms=28.0, fmriprep_cifti=True,
                                  output_spaces="T1w MNI152NLin6Asym", fd_threshold=0.3), self.site)
        self.assertIn("--fmriprep-cifti", bold.command)
        self.assertNotIn("--fs-subjects-dir", bold.command)
        self.assertIn("--anat-fslr-dir", bold.command)
        self.assertNotIn("--anat-fs-subjects-dir", bold.command)
        self.assertEqual(bold.command[bold.command.index("--comparison-echo") + 1], "2")
        self.assertEqual(bold.command[bold.command.index("--tedana-fittype") + 1], "curvefit")
        dwi = plan_job(self.spec(output_resolution=2.0, denoise_method="patch2self",
                                 b0_policy="exclude", distortion_group_merge="average",
                                 separate_all_dwis=True), self.site)
        self.assertEqual(dwi.command[dwi.command.index("--output-resolution") + 1], "2.0")
        self.assertIn("--separate-all-dwis", dwi.command)
        self.assertEqual(dwi.command[dwi.command.index("--denoise-method") + 1], "patch2self")

    def test_bold_cifti_requires_postprocess_inputs_or_selected_stage(self):
        with self.assertRaisesRegex(PlanError, "select postprocess and CIFTI together"):
            plan_job(self.spec("bold", stage="cifti"), self.site)

        post_dir = self.root / "bold-work" / "post_tedana" / "sub-01" / "func"
        post_dir.mkdir(parents=True)
        (post_dir / "sub-01_task-rest_desc-tedanaGSR_bold.nii.gz").touch()
        (post_dir / "sub-01_task-rest_desc-tedanaNoGSR_bold.nii.gz").touch()
        plan = plan_job(self.spec("bold", stage="cifti", work_dir=str(self.root / "bold-work")), self.site)
        self.assertEqual(plan.command[plan.command.index("--stage") + 1], "cifti")

        staged = plan_job(self.spec("bold", stages=("cifti", "postprocess")), self.site)
        self.assertEqual(staged.spec.stages, ("postprocess", "cifti"))

    def test_bold_only_reuses_explicit_fmriprep_subjects(self):
        subjects = self.root / "fs-subjects"
        subjects.mkdir()
        plan = plan_job(self.spec("bold", fs_subjects_dir=str(subjects)), self.site)
        self.assertEqual(plan.command[plan.command.index("--fs-subjects-dir") + 1], str(subjects.resolve()))

    def test_bold_forwards_anatomy_surfaces_without_fmriprep_reuse(self):
        subjects = self.root / "anat-fs-subjects"
        subjects.mkdir()
        plan = plan_job(self.spec("bold", anat_fs_subjects_dir=str(subjects)), self.site)
        self.assertEqual(plan.command[plan.command.index("--anat-fs-subjects-dir") + 1], str(subjects.resolve()))
        self.assertNotIn("--fs-subjects-dir", plan.command)

    def test_bold_accepts_native_tedana(self):
        tedana = self.root / "tedana"
        tedana.write_text("#!/bin/sh\n")
        site = SiteProfile(pipeline_root=str(self.pipeline), fmriprep_image=str(self.image),
                           tedana_command=str(tedana), atlas_root=str(self.atlas_root),
                           wb_command=str(self.wb_command), ants_apply_transforms=str(self.ants_apply))
        plan = plan_job(self.spec("bold"), site)
        self.assertEqual(plan.command[plan.command.index("--tedana-command") + 1], str(tedana.resolve()))
        self.assertNotIn("--tedana-image", plan.command)

    def test_nextflow_bold_plan_is_bounded_and_resumable(self):
        plan = plan_job(self.spec("bold", execution_engine="nextflow", nextflow_resume=True), self.site)
        self.assertEqual(plan.command[0], str(self.nextflow.resolve()))
        self.assertIn("-resume", plan.command)
        self.assertIn("--launcher_bundle_b64", plan.command)
        self.assertEqual(plan.command[plan.command.index("--input_dir") + 1], str(Path(self.spec("bold").input_dir).resolve()))
        self.assertEqual(plan.command[plan.command.index("--workflow") + 1], "bold")
        self.assertEqual(plan.command[plan.command.index("--expected_derivatives") + 1], "fmriprep,tedana,mefmriprep")
        self.assertEqual(plan.environment["JAVA_CMD"], str((self.java_home / "bin" / "java").resolve()))
        self.assertTrue(any("one tracked task" in warning for warning in plan.warnings))
        self.assertTrue(any("path, size, and modification time" in warning for warning in plan.warnings))

    def test_nextflow_wraps_anatomy_and_dwi_apps(self):
        anat = plan_job(self.spec("anat", execution_engine="nextflow"), self.site)
        dwi = plan_job(self.spec("dwi", execution_engine="nextflow"), self.site)
        self.assertEqual(anat.command[anat.command.index("--expected_derivatives") + 1], "freesurfer,anatprep")
        self.assertEqual(dwi.command[dwi.command.index("--expected_derivatives") + 1], "qsiprep,qsirecon,dwiprep")
        self.assertIn("--launcher_bundle_b64", anat.command)
        self.assertIn("--launcher_bundle_b64", dwi.command)

    def test_nextflow_is_not_offered_for_dicom_conversion(self):
        source = self.root / "dicom"
        source.mkdir()
        with self.assertRaisesRegex(PlanError, "only"):
            plan_job(self.spec("dicom", stage="inventory", input_dir=str(source),
                               execution_engine="nextflow"), self.site)

    def test_nextflow_bold_rejects_partial_stages_and_output_overrides(self):
        with self.assertRaisesRegex(PlanError, "complete modality workflows only"):
            plan_job(self.spec("bold", stage="fmriprep", execution_engine="nextflow"), self.site)
        with self.assertRaisesRegex(PlanError, "output paths"):
            plan_job(self.spec("bold", execution_engine="nextflow", fmriprep_dir=str(self.output / "alt")), self.site)

    def test_nextflow_cannot_replace_existing_derivative_directory(self):
        existing = self.output / "fmriprep" / "sub-01"
        existing.mkdir(parents=True)
        (existing / "preexisting.txt").write_text("preserve")
        with self.assertRaisesRegex(PlanError, "will not replace"):
            plan_job(self.spec("bold", execution_engine="nextflow"), self.site)

    def test_nextflow_allows_empty_derivative_placeholders(self):
        for derivative in ("qsiprep", "qsirecon", "dwiprep"):
            (self.output / derivative).mkdir(parents=True)
        plan = plan_job(self.spec("dwi", execution_engine="nextflow"), self.site)
        self.assertEqual(plan.command[plan.command.index("--expected_derivatives") + 1],
                         "qsiprep,qsirecon,dwiprep")

    def test_nextflow_rejects_nonempty_derivative_placeholders(self):
        target = self.output / "qsiprep" / "sub-01"
        target.mkdir(parents=True)
        (target / "existing.txt").write_text("preserve")
        with self.assertRaisesRegex(PlanError, "will not replace"):
            plan_job(self.spec("dwi", execution_engine="nextflow"), self.site)

    def test_anatomy_session_is_blocked(self):
        (self.bids / "sub-01" / "ses-02").mkdir()
        with self.assertRaisesRegex(PlanError, "session-scoped"):
            plan_job(self.spec("anat", session="02"), self.site)

    def test_resource_and_label_validation(self):
        with self.assertRaisesRegex(PlanError, "OpenMP"):
            plan_job(self.spec(nprocs=4, omp_nthreads=8), self.site)
        with self.assertRaisesRegex(PlanError, "labels"):
            plan_job(self.spec(participant="01;whoami"), self.site)

    def test_dicom_conversion_requires_reviewed_config_and_no_overwrite(self):
        source = self.root / "dicom"
        source.mkdir()
        tool = self.root / "dcm2bids"
        tool.write_text("#!/bin/sh\n")
        converter = self.root / "dcm2niix"
        converter.write_text("#!/bin/sh\n")
        site = SiteProfile(pipeline_root=str(self.pipeline), dcm2bids=str(tool), dcm2niix=str(converter), bids_validator=str(tool))
        config = self.root / "mapping.json"
        config.write_text(json.dumps({
            "descriptions": [{"datatype": "dwi", "suffix": "dwi", "criteria": {"SeriesNumber": 14}}],
            "NeuroFlowStudyId": "study-01", "NeuroFlowManifest": str(self.root / "study_manifest.json"),
        }))
        spec = self.spec("dicom", stage="convert", input_dir=str(source), dicom_config=str(config))
        plan = plan_job(spec, site)
        self.assertNotIn("--bids_validate", plan.command)
        self.assertEqual(plan.post_command[-1], str(self.output.resolve()))
        self.assertTrue(plan.warnings)
        (self.output / "sub-01").mkdir(parents=True)
        with self.assertRaisesRegex(PlanError, "overwrite"):
            plan_job(spec, site)

    def test_dicom_inventory_cannot_write_inside_source(self):
        source = self.root / "dicom"
        source.mkdir()
        spec = self.spec("dicom", stage="inventory", input_dir=str(source), output_dir=str(source / "helper"))
        with self.assertRaisesRegex(PlanError, "inside"):
            plan_job(spec, self.site)

    def test_manual_mapping_requires_valid_fieldmap_target(self):
        rows = [{"SeriesNumber": 10}, {"SeriesNumber": 11}]
        fields = {
            "row_0_datatype": "func", "row_0_suffix": "bold",
            "row_0_entities": "task-rest", "row_0_id": "bold1",
            "row_1_datatype": "fmap", "row_1_suffix": "epi",
            "row_1_id": "fmap1", "row_1_intendedfor": "bold1",
        }
        config = build_mapping(rows, fields)
        self.assertEqual(config["descriptions"][0]["id"], "bold1")
        self.assertEqual(config["descriptions"][1]["sidecar_changes"]["IntendedFor"], ["bold1"])
        fields["row_1_intendedfor"] = "missing"
        with self.assertRaisesRegex(PlanError, "unknown"):
            build_mapping(rows, fields)

    def test_manual_mapping_uses_acquisition_time_to_disambiguate_repeated_series(self):
        rows = [
            {"SeriesNumber": 10, "AcquisitionTime": "09:00:00", "EchoNumber": 1},
            {"SeriesNumber": 10, "AcquisitionTime": "10:30:00", "EchoNumber": 1},
        ]
        fields = {
            "row_0_datatype": "anat", "row_0_suffix": "T1w",
            "row_1_datatype": "anat", "row_1_suffix": "T1w",
        }
        config = build_mapping(rows, fields)
        self.assertEqual(
            [item["criteria"]["AcquisitionTime"] for item in config["descriptions"]],
            ["09:00:00", "10:30:00"],
        )
        with self.assertRaisesRegex(PlanError, "lacks AcquisitionTime"):
            build_mapping([{"SeriesNumber": 10, "source_group": "20261012085647"}], {
                "row_0_datatype": "anat", "row_0_suffix": "T1w",
            })


if __name__ == "__main__":
    unittest.main()
