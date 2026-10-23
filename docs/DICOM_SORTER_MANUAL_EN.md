# NeuroFlow DICOM Sorter: Example Workflow (English)

This guide walks through a typical scanner-export workflow. The screen image shows the app controls. The fictional `DEMO001` labels in the examples below are illustrative; no participant data are used.

## 1. Install and open the app

- **Windows:** run the downloaded `NeuroFlowDICOMSorter-Windows-Setup-0.4.16.exe` installer, then launch NeuroFlow DICOM Sorter from the Start menu or desktop shortcut.
- **macOS:** unzip the downloaded `NeuroFlowDICOMSorter-macOS-…-0.4.16.zip`, move the app to Applications, and open it. The internal prototype is not notarized; if macOS blocks the first launch, use Finder’s Control-click → **Open** flow.

No Python, dcm2bids, or dcm2niix installation is needed for the packaged app. DICOM sorting does not depend on BIDS conversion.

## 2. Choose the scanner export

In **Scanner DICOM folder**, select the folder containing the copied scanner DICOM files. The app reads DICOM metadata and groups instances by Study and Series Instance UID. It reads each subject label from DICOM `PatientID`; it does not generate sequential subject numbers.

The app suggests a separate `_sorted` folder beside the source. You can change it, but choose a new or empty folder. Sorting copies the files; the scanner export remains unchanged.

![Sorter main window before a scanner folder is selected](images/dicom-sorter-main.png)

## 3. Preview and review sequences

Click **2 · Preview sequences**. Check the subject, series description, protocol name, proposed category, and status before copying. The general preset recognizes common sequence families across vendors. For a known Prisma protocol, open **Advanced: change sequence rules** and select **Use Prisma preset**. You can open and edit the JSON rules or choose a custom rule file there.

A series with no rule match is marked **unmatched**. A series matching more than one rule is marked **ambiguous**. Both remain visible for review; the app requires the review checkbox before enabling copy. If a series is misclassified, edit the rules and preview again, or leave it in the review bucket.

## 4. Copy into sorted folders

After checking the preview, click **3 · Copy to sorted folder** and confirm. Each series is copied into its own folder under a category, for example:

```text
scanner-export_sorted/
└── sub-DEMO001/
    ├── anat/series-1_T1w_MPRAGE/
    ├── func/series-2_task-rest_bold/
    └── _unmatched/series-3_SequenceDelta/
```

The unmatched example is intentionally kept separate for manual classification. Click **Open sorted folder** when the copy completes.

## 5. Optional: convert sorted data to BIDS

BIDS conversion is a separate, optional step. It uses the bundled dcm2bids and dcm2niix tools.

1. Sort the DICOM data first.
2. Under **Subject to convert**, choose one detected subject or **All subjects**.
3. Choose a new or empty **BIDS output folder**.
4. Review the selected dcm2bids JSON config using **Open config**. The included config is tailored to the observed Prisma sequences; adapt it for other protocols.
5. Optionally enter a session label, then click **Convert to BIDS…** and confirm.

The app converts selected subjects one at a time and retains the DICOM source. Inspect the resulting NIfTI files and JSON sidecars, then run a BIDS validator before downstream analysis. Do not assume that series names alone establish phase-encoding direction, readout time, or diffusion metadata.

## Troubleshooting

- **Preview takes time:** the status line reports discovery and reading progress. Keep generated `_sorted` and `_BIDS` output folders outside the scanner source when practical.
- **Unexpected category:** use the preview status and descriptions to review the rule match. Update the JSON preset and preview again.
- **No BIDS files found for a mapping:** the config entry did not match any converted sidecar. Review the dcm2bids config and the dcm2niix JSON sidecars; correct the mapping before retrying in a new empty output folder.
- **Conversion stops partway through:** inspect the reported partial BIDS output before retrying. Use a new empty output folder to avoid mixing results.
