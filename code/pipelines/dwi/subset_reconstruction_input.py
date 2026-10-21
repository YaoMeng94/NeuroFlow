#!/usr/bin/env python3
"""Create a reconstruction-only QSIPrep subset using per-volume source provenance."""

import argparse
import csv
import json
from pathlib import Path
import shutil

import nibabel as nib
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dwi', type=Path, required=True)
    parser.add_argument('--keep-source', action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source = args.source_dwi.resolve()
    subject = next(p for p in source.parents if p.name.startswith('sub-'))
    root = subject.parent
    prefix = source.name.split('_space-')[0]
    stem = source.name.removesuffix('.nii.gz')
    confounds = source.with_name(prefix + '_desc-confounds_timeseries.tsv')
    with confounds.open() as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))
    image = nib.load(source)
    bval = np.loadtxt(source.with_name(stem + '.bval')).reshape(-1)
    bvec = np.loadtxt(source.with_name(stem + '.bvec'))
    if len(image.shape) != 4 or len(rows) != image.shape[3] or len(bval) != len(rows) or bvec.shape != (3, len(rows)):
        raise ValueError('NIfTI, confounds, and gradient lengths must match')
    sources = [Path(row['original_file']).name for row in rows]
    keep = set(args.keep_source)
    if not keep.issubset(sources):
        raise ValueError(f'Sources not found: {keep - set(sources)}')
    indices = np.array([i for i, name in enumerate(sources) if name in keep])
    if not np.allclose([float(row['bval']) for row in rows], bval, atol=1):
        raise ValueError('Confounds b-values disagree with output gradients')
    if not np.any(bval[indices] <= 50):
        raise ValueError('Selection lacks b0 volumes')
    if args.output_dir.exists():
        raise FileExistsError(f'Refusing to overwrite {args.output_dir}')
    output = args.output_dir / source.relative_to(root)
    output.parent.mkdir(parents=True)
    description = json.loads((root / 'dataset_description.json').read_text())
    description['Name'] = description.get('Name', 'QSIPrep') + ' reconstruction input subset'
    description.setdefault('GeneratedBy', []).append({'Name': 'PrismaDWI-ReconstructionSubset', 'Version': '1.0'})
    (args.output_dir / 'dataset_description.json').write_text(json.dumps(description, indent=2) + '\n')
    # Retain spatial transforms and anatomy without altering the parent derivatives.
    for anat in subject.glob('**/anat'):
        shutil.copytree(anat, args.output_dir / anat.relative_to(root))
    data = np.asarray(image.dataobj, dtype=np.float32)[..., indices]
    if not np.isfinite(data).all():
        raise ValueError('Selected image contains nonfinite values')
    nib.save(nib.Nifti1Image(data, image.affine, image.header), output)
    np.savetxt(output.with_name(stem + '.bval'), bval[indices][None, :], fmt='%.8g')
    np.savetxt(output.with_name(stem + '.bvec'), bvec[:, indices], fmt='%.10g')
    metadata = json.loads(source.with_name(stem + '.json').read_text())
    provenance = {'ParentDWI': str(source), 'RetainedSources': sorted(keep),
                  'ExcludedSources': sorted(set(sources) - keep),
                  'OriginalVolumeIndicesZeroBased': indices.tolist(),
                  'ParentVolumeCount': len(rows), 'SelectedVolumeCount': len(indices),
                  'Caveat': 'Preprocessing, motion corrections and mask were estimated jointly with excluded acquisitions. Only reconstruction inputs are subset.'}
    metadata['Sources'] = [name for name in dict.fromkeys(sources) if name in keep]
    if 'ScanGrouping' in metadata:
        metadata['ParentScanGrouping'] = metadata.pop('ScanGrouping')
    metadata['ReconstructionSubset'] = provenance
    output.with_name(stem + '.json').write_text(json.dumps(metadata, indent=2) + '\n')
    mask_name = source.name.replace('_desc-preproc_dwi.nii.gz', '_desc-brain_mask.nii.gz')
    masks = sorted(subject.rglob(mask_name))
    if len(masks) != 1:
        raise ValueError(f'Expected one QSIPrep DWI mask named {mask_name}, found {len(masks)}')
    shutil.copy2(masks[0], output.with_name(mask_name))
    reference = output.with_name(source.name.replace('_desc-preproc_dwi.nii.gz', '_dwiref.nii.gz'))
    nib.save(nib.Nifti1Image(data[..., bval[indices] <= 50].mean(axis=3), image.affine), reference)
    with output.with_name(confounds.name).open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows[i] for i in indices)
    (args.output_dir / 'reconstruction_subset.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps({'Output': str(output), 'Volumes': len(indices),
                      'ShellCounts': {str(v): int(np.sum(bval[indices] == v)) for v in np.unique(bval[indices])}}))


if __name__ == '__main__':
    main()
