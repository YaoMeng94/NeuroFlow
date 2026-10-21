nextflow.enable.dsl=2

params.launcher_bundle_b64 = null
params.input_dir = null
params.output_dir = null
params.expected_derivatives = null
params.nprocs = 16
params.mem_mb = 32768
params.publish_mode = 'symlink'

def bidsMetadataFingerprint(String inputDir) {
    def root = java.nio.file.Paths.get(inputDir).toRealPath()
    def relativePaths = []
    def stream = java.nio.file.Files.walk(root, java.nio.file.FileVisitOption.FOLLOW_LINKS)
    stream.filter { path -> java.nio.file.Files.isRegularFile(path) }
        .map { path -> root.relativize(path).toString() }
        .forEach { relative -> relativePaths.add(relative) }
    stream.close()
    java.util.Collections.sort(relativePaths)

    def digest = java.security.MessageDigest.getInstance('SHA-256')
    relativePaths.each { relative ->
        def path = root.resolve(relative)
        def attrs = java.nio.file.Files.readAttributes(path, java.nio.file.attribute.BasicFileAttributes.class)
        def row = "${relative}\t${attrs.size()}\t${attrs.lastModifiedTime()}\n"
        digest.update(row.getBytes(java.nio.charset.StandardCharsets.UTF_8))
    }
    digest.digest().encodeHex().toString()
}

process RUN_PIPELINE {
    tag "${params.workflow} participant workflow"
    cpus { params.nprocs as int }
    memory { (params.mem_mb as int).MB }
    maxForks 1
    publishDir params.output_dir, mode: params.publish_mode, overwrite: true,
        saveAs: { filename -> filename.tokenize('/').last() }

    input:
    path bids_source
    val bids_fingerprint
    val launcher_bundle_b64

    output:
    path 'nfout/*', emit: derivatives

    script:
    """
    python3 '${projectDir}/run_pipeline_task.py' \
      --launcher-bundle-b64 '${launcher_bundle_b64}' \
      --bids-dir "${bids_source}" \
      --bids-fingerprint '${bids_fingerprint}' \
      --output-root nfout \
      --expected-derivatives '${params.expected_derivatives}'
    """
}

workflow {
    if (!params.launcher_bundle_b64 || !params.input_dir || !params.output_dir || !params.expected_derivatives) {
        error 'launcher_bundle_b64, input_dir, output_dir, and expected_derivatives are required'
    }
    if (!(params.workflow in ['anat', 'bold', 'dwi'])) {
        error 'workflow must be anat, bold, or dwi'
    }
    if (!(params.publish_mode in ['symlink', 'copy'])) {
        error 'publish_mode must be symlink or copy'
    }
    def bids_fingerprint = bidsMetadataFingerprint(params.input_dir)
    RUN_PIPELINE(Channel.value(file(params.input_dir, checkIfExists: true)), bids_fingerprint, params.launcher_bundle_b64)
}
