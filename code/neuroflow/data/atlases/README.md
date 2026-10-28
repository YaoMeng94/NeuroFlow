# NeuroFlow bundled atlases

This directory contains two kinds of assets:

* `standard_mesh_atlases/` contains the HCP standard-mesh references used by
  NeuroFlow's fsLR32k ANAT export and CIFTI creation. The installer points the
  site profile's `atlas_root` here automatically. The original HCP explanatory
  README and repository license are kept with the files.
* `Glasser360/` contains the existing BIDS-Atlas dataset distributed with
  NeuroFlow.
* `Schaefer2018_MNI152_2mm/` contains official Schaefer2018 volumetric maps at
  2 mm, for 100, 200, 300, 400, 500, 600, 700, 800, 900, and 1000 cortical
  parcels. Both 7-network and 17-network labelings are present. Label tables,
  the upstream README, and CBIG license are included.

The Schaefer maps use the upstream `FSLMNI152` 2 mm grid. They are reference
files; NeuroFlow does not silently resample them or register them to another
template. Before using one as a custom QSIRecon atlas, validate the expected
template, orientation, affine/header conventions, and label preservation for
the installed QSIRecon version. QSIRecon's own built-in atlas collection
provides an immediately supported choice, including Brainnetome, AAL, AICHA,
Gordon, and 4S atlases.

## Attribution

Schaefer parcellations are from A. Schaefer et al. (2018), “Local-Global
Parcellation of the Human Cerebral Cortex from Intrinsic Functional
Connectivity MRI,” *Cerebral Cortex*, 29(7), 3095–3114,
doi:10.1093/cercor/bhx179. The files were obtained from the CBIG repository at
the pinned `v0.14.3-Update_Yeo2011_Schaefer2018_labelname` ref. See
`Schaefer2018_MNI152_2mm/CBIG_LICENSE.md` and its upstream README.

HCP standard-mesh files are derived from the Washington University HCP
pipelines' `global/templates/standard_mesh_atlases` resources. Their original
description and license are included under `standard_mesh_atlases/`.

The included atlas files are independent upstream data; their inclusion does
not change the license of the NeuroFlow software itself.
