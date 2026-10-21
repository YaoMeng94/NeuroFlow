nextflow.enable.dsl=2

params.launcher_bundle_b64 = null
params.output_dir = null
params.nprocs = 16
params.mem_mb = 32768
params.publish_mode = 'symlink'

process RUN_BOLD {
    tag 'BOLD participant workflow'
    cpus { params.nprocs as int }
    memory { (params.mem_mb as int).MB }
    maxForks 1
    publishDir params.output_dir, mode: params.publish_mode, overwrite: true,
        saveAs: { filename -> filename.tokenize('/').last() }

    input:
    val launcher_bundle_b64

    output:
    path 'nfout/fmriprep', emit: fmriprep
    path 'nfout/tedana', emit: tedana
    path 'nfout/mefmriprep', emit: mefmriprep

    script:
    """
    python3 '${projectDir}/run_bold_task.py' \
      --launcher-bundle-b64 '${launcher_bundle_b64}' \
      --output-root nfout
    """
}

workflow {
    if (!params.launcher_bundle_b64 || !params.output_dir) {
        error 'launcher_bundle_b64 and output_dir are required'
    }
    if (!(params.publish_mode in ['symlink', 'copy'])) {
        error 'publish_mode must be symlink or copy'
    }
    RUN_BOLD(params.launcher_bundle_b64)
}
