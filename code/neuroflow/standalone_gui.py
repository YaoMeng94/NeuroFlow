"""Standalone cross-platform desktop DICOM sorter.

This deliberately imports only the shared sorter core, so the desktop build does
not need NeuroFlow site configuration, processing containers, or a browser.
"""

from __future__ import annotations

import argparse
import os
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from importlib.resources import files
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any
import re

from . import __version__
from .dicom_sort import inspect_dicom, load_rules, sort_dicom
from .bids_finalize import finalize_bids


APP_NAME = "NeuroFlow DICOM Sorter"
PRESET_NAME = "dicom_sort_rules_20261016.json"
BIDS_CONFIG_NAME = "dcm2bids_config_prisma.json"
ALL_SUBJECTS_OPTION = "All subjects"
TABLE_COLUMNS = ("subject", "series", "description", "protocol", "files", "category", "status")


def _user_config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "neuroflow-dicom-sorter"


def _bundled_preset() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidate = Path(frozen_root) / "neuroflow" / "data" / PRESET_NAME
        if candidate.is_file():
            return candidate
    return Path(str(files("neuroflow").joinpath("data", PRESET_NAME)))


def _install_user_preset() -> Path:
    destination = _user_config_dir() / PRESET_NAME
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_bundled_preset(), destination)
    return destination


def _install_user_bids_config() -> Path:
    destination = _user_config_dir() / BIDS_CONFIG_NAME
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_bundled_preset().with_name(BIDS_CONFIG_NAME), destination)
    return destination


class SorterWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.minsize(980, 680)
        self.root.geometry("1120x860")
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.rows: list[dict[str, Any]] = []
        self.busy = False
        self.advanced_visible = False

        try:
            default_rules = _install_user_preset()
        except (OSError, ModuleNotFoundError) as exc:
            default_rules = Path.home() / PRESET_NAME
            messagebox.showwarning(
                APP_NAME,
                f"The bundled rule preset could not be copied. Choose a rule JSON file to continue.\n\n{exc}",
                parent=root,
            )

        try:
            default_bids_config = _install_user_bids_config()
        except (OSError, ModuleNotFoundError) as exc:
            default_bids_config = Path.home() / BIDS_CONFIG_NAME
            messagebox.showwarning(
                APP_NAME,
                f"The default BIDS config could not be copied to the user settings folder.\n\n{exc}",
                parent=root,
            )

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.bids_output_var = tk.StringVar()
        self.bids_config_var = tk.StringVar(value=str(default_bids_config))
        self.session_var = tk.StringVar()
        self.bids_subject_var = tk.StringVar()
        self.rules_var = tk.StringVar(value=str(default_rules))
        self.status_var = tk.StringVar(value="Choose a scanner export folder and preview its DICOM series.")
        self.review_var = tk.BooleanVar(value=False)

        self._build()
        self.source_var.trace_add("write", self._source_changed)
        self.rules_var.trace_add("write", self._rules_changed)
        self.output_var.trace_add("write", self._output_changed)
        self.root.after(100, self._poll_events)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="DICOM Sorter", font=("TkDefaultFont", 20, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="1. Choose the scanner folder.  2. Preview the sequences.  3. Copy them into sorted folders. Your original files stay untouched.",
            wraplength=980,
        ).pack(anchor="w", pady=(3, 14))

        paths = ttk.LabelFrame(outer, text="1 · Choose folders", padding=12)
        paths.pack(fill="x")
        self._path_row(paths, "Scanner DICOM folder", self.source_var, self._choose_source, 0)
        self._path_row(paths, "Sorted output folder", self.output_var, self._choose_output, 1)
        ttk.Label(paths, text="Subject IDs are read from each DICOM PatientID; the app does not generate sequential IDs.").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(3, 0)
        )

        self.advanced_button = ttk.Button(
            outer,
            text="Advanced: change sequence rules ▸",
            command=self._toggle_advanced,
        )
        self.advanced_button.pack(anchor="w", pady=(8, 2))
        self.advanced_frame = ttk.LabelFrame(outer, text="Sequence rule preset", padding=10)
        ttk.Label(
            self.advanced_frame,
            text="The included preset is tuned for the observed Prisma protocol. Choose another JSON rule file for a different scanner or protocol.",
            wraplength=900,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._path_row(self.advanced_frame, "Rule preset", self.rules_var, self._choose_rules, 1)
        ttk.Button(self.advanced_frame, text="Open rule file", command=self._open_rules).grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(
            self.advanced_frame,
            text="Rule files can be edited in a text editor. Save changes, then preview again.",
            wraplength=700,
        ).grid(row=2, column=2, sticky="w", padx=(10, 0), pady=(6, 0))
        self.advanced_frame.columnconfigure(1, weight=1)
        self.advanced_frame.columnconfigure(2, weight=1)
        paths.columnconfigure(1, weight=1)

        bids = ttk.LabelFrame(outer, text="Optional · Convert DICOM to BIDS", padding=10)
        bids.pack(fill="x", pady=(8, 0))
        ttk.Label(
            bids,
            text="The included Prisma config maps tested anatomy, multi-echo REST, diffusion, and fieldmap series. Review it before conversion. dcm2bids and dcm2niix must be installed on this computer.",
            wraplength=980,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._path_row(bids, "BIDS output folder", self.bids_output_var, self._choose_bids_output, 1)
        self._path_row(bids, "dcm2bids config", self.bids_config_var, self._choose_bids_config, 2)
        ttk.Button(bids, text="Open config", command=self._open_bids_config).grid(row=2, column=3, padx=(8, 0), pady=4)
        ttk.Label(bids, text="Subject to convert").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=4)
        self.bids_subject_combo = ttk.Combobox(bids, textvariable=self.bids_subject_var, state="readonly", width=24)
        self.bids_subject_combo.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(bids, text="Choose ‘All subjects’ to convert every participant found in the preview.", wraplength=430).grid(
            row=3, column=2, columnspan=2, sticky="w", padx=(10, 0), pady=4
        )
        ttk.Label(bids, text="Session (optional)").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(bids, textvariable=self.session_var, width=24).grid(row=4, column=1, sticky="w", pady=4)
        bids.columnconfigure(1, weight=1)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 8))
        self.preview_button = ttk.Button(actions, text="2 · Preview sequences", command=self._preview)
        self.preview_button.pack(side="left")
        self.copy_button = ttk.Button(actions, text="3 · Copy to sorted folder", command=self._copy, state="disabled")
        self.copy_button.pack(side="left", padx=(8, 0))
        self.open_output_button = ttk.Button(actions, text="Open sorted folder", command=self._open_output, state="disabled")
        self.open_output_button.pack(side="left", padx=(8, 0))
        self.bids_button = ttk.Button(actions, text="Convert to BIDS…", command=self._convert_bids)
        self.bids_button.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=150)
        self.progress.pack(side="right")

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=TABLE_COLUMNS, show="headings", selectmode="browse")
        headings = {
            "subject": ("Subject", 130), "series": ("Series", 70),
            "description": ("Series description", 240), "protocol": ("Protocol name", 220),
            "files": ("Files", 65), "category": ("Category", 125), "status": ("Status", 105),
        }
        for key, (label, width) in headings.items():
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=55, stretch=key in {"description", "protocol"})
        self.table.tag_configure("unmatched", background="#fff2cf")
        self.table.tag_configure("ambiguous", background="#fbdcda")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        xscroll = ttk.Scrollbar(outer, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        xscroll.pack(fill="x")

        self.review_check = ttk.Checkbutton(
            outer,
            text="I reviewed the unmatched or ambiguous series; copy them into their review folders.",
            variable=self.review_var,
            command=self._update_copy_state,
        )
        self.review_check.pack_forget()
        self.status_label = ttk.Label(outer, textvariable=self.status_var, wraplength=1000)
        self.status_label.pack(anchor="w")

    def _path_row(self, parent: ttk.LabelFrame, label: str, variable: tk.StringVar, command: Any, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(parent, text="Browse…", command=command).grid(row=row, column=2, padx=(8, 0), pady=4)

    def _choose_source(self) -> None:
        selected = filedialog.askdirectory(title="Choose the scanner export folder", parent=self.root)
        if selected:
            self.source_var.set(selected)
            source = Path(selected)
            self.output_var.set(str(source.with_name(source.name + "_sorted")))
            self.bids_output_var.set(str(source.with_name(source.name + "_BIDS")))
            self.open_output_button.configure(state="disabled")
            self._invalidate_preview()

    def _toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.pack(fill="x", pady=(2, 6), after=self.advanced_button)
            self.advanced_button.configure(text="Advanced: change sequence rules ▾")
        else:
            self.advanced_frame.pack_forget()
            self.advanced_button.configure(text="Advanced: change sequence rules ▸")

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="Choose an empty output folder", parent=self.root, mustexist=False)
        if selected:
            self.output_var.set(selected)
            self.open_output_button.configure(state="disabled")

    def _open_output(self) -> None:
        output = Path(self.output_var.get().strip()).expanduser()
        if not output.is_dir():
            messagebox.showerror(APP_NAME, "The sorted output folder is not available.", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(output)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(output)])
            else:
                subprocess.Popen(["xdg-open", str(output)])
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open the output folder: {exc}", parent=self.root)

    def _choose_rules(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose a DICOM sorting rule preset",
            filetypes=(("JSON rule files", "*.json"), ("All files", "*.*")),
            parent=self.root,
        )
        if selected:
            self.rules_var.set(selected)
            self._invalidate_preview()

    def _open_rules(self) -> None:
        path = Path(self.rules_var.get()).expanduser()
        if not path.is_file():
            messagebox.showerror(APP_NAME, "Choose an existing rule JSON file first.", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open the rule file: {exc}", parent=self.root)

    def _invalidate_preview(self) -> None:
        self.rows = []
        self.review_var.set(False)
        self.copy_button.configure(state="disabled")
        for item in self.table.get_children():
            self.table.delete(item)

    def _source_changed(self, *_: object) -> None:
        if hasattr(self, "table"):
            self._invalidate_preview()

    def _rules_changed(self, *_: object) -> None:
        if hasattr(self, "table"):
            self._invalidate_preview()

    def _output_changed(self, *_: object) -> None:
        if hasattr(self, "open_output_button"):
            self.open_output_button.configure(state="disabled")

    def _set_busy(self, busy: bool, message: str) -> None:
        self.busy = busy
        self.status_var.set(message)
        self.preview_button.configure(state="disabled" if busy else "normal")
        self.bids_button.configure(state="disabled" if busy else "normal")
        self.progress.start(12) if busy else self.progress.stop()
        if not busy:
            self._update_copy_state()

    def _worker(self, event_name: str, callback: Any) -> None:
        def run() -> None:
            try:
                self.events.put((event_name, callback()))
            except Exception as exc:  # surface filesystem and malformed DICOM errors in the UI
                self.events.put(("error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def _preview(self) -> None:
        source = Path(self.source_var.get().strip()).expanduser()
        rule_path = Path(self.rules_var.get().strip()).expanduser()
        if not self.source_var.get().strip() or not self.rules_var.get().strip():
            messagebox.showerror(APP_NAME, "Choose the scanner export folder and a rule preset.", parent=self.root)
            return
        self._invalidate_preview()
        self._set_busy(True, "Reading DICOM headers and matching series…")

        def inspect() -> list[dict[str, Any]]:
            return inspect_dicom(
                source,
                load_rules(rule_path),
                progress_callback=lambda message: self.events.put(("progress", message)),
            )
        self._worker("preview_done", inspect)

    def _copy(self) -> None:
        if not self.rows:
            return
        source = Path(self.source_var.get().strip()).expanduser()
        output = Path(self.output_var.get().strip()).expanduser()
        rules = Path(self.rules_var.get().strip()).expanduser()
        if not self.output_var.get().strip():
            messagebox.showerror(APP_NAME, "Choose an output folder.", parent=self.root)
            return
        try:
            source_resolved = source.resolve()
            output_resolved = output.resolve()
            if source_resolved == output_resolved or source_resolved in output_resolved.parents or output_resolved in source_resolved.parents:
                messagebox.showerror(APP_NAME, "Choose a separate output folder outside the scanner DICOM folder.", parent=self.root)
                return
            if output.exists() and (not output.is_dir() or any(output.iterdir())):
                messagebox.showerror(APP_NAME, "Choose a new or empty output folder. Existing files will not be overwritten.", parent=self.root)
                return
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not check the output folder: {exc}", parent=self.root)
            return
        needs_review = any(row["status"] != "matched" for row in self.rows)
        if needs_review and not self.review_var.get():
            messagebox.showwarning(APP_NAME, "Review the unmatched or ambiguous rows and check the review box first.", parent=self.root)
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Copy {len(self.rows)} series into:\n{output}\n\nThe scanner export will remain unchanged.",
            parent=self.root,
        ):
            return
        self.copy_button.configure(state="disabled")
        self._set_busy(True, "Copying DICOM files… Large exports can take several minutes.")

        def copy() -> list[dict[str, Any]]:
            return sort_dicom(source, output, load_rules(rules))
        self._worker("copy_done", copy)

    def _choose_bids_output(self) -> None:
        selected = filedialog.askdirectory(title="Choose a new BIDS dataset folder", parent=self.root, mustexist=False)
        if selected:
            self.bids_output_var.set(selected)

    def _choose_bids_config(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose a dcm2bids configuration file",
            filetypes=(("JSON config", "*.json"), ("All files", "*.*")),
            parent=self.root,
        )
        if selected:
            self.bids_config_var.set(selected)

    def _open_bids_config(self) -> None:
        path = Path(self.bids_config_var.get().strip()).expanduser()
        if not path.is_file():
            messagebox.showerror(APP_NAME, "The BIDS config file is not available. Choose a JSON config first.", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open the BIDS config: {exc}", parent=self.root)

    def _convert_bids(self) -> None:
        source_text = self.source_var.get().strip()
        output_text = self.bids_output_var.get().strip()
        config_text = self.bids_config_var.get().strip()
        subject_label = self.bids_subject_var.get().strip()
        available_subjects = sorted({str(row.get("study", "")) for row in self.rows if row.get("study")})
        convert_all = subject_label == ALL_SUBJECTS_OPTION
        selected_subjects = available_subjects if convert_all else [subject_label]
        participants = [
            (label, re.sub(r"^sub-", "", label, flags=re.IGNORECASE))
            for label in selected_subjects
        ]
        session = self.session_var.get().strip()
        if not source_text or not Path(source_text).expanduser().is_dir() or not self.rows:
            messagebox.showerror(APP_NAME, "Choose a scanner folder and preview its series first.", parent=self.root)
            return
        if not participants or any(
            label not in available_subjects or not re.fullmatch(r"[A-Za-z0-9]+", participant)
            for label, participant in participants
        ):
            messagebox.showerror(
                APP_NAME,
                "Choose a detected subject or ‘All subjects’ from the subject list.",
                parent=self.root,
            )
            return
        if session and not re.fullmatch(r"[A-Za-z0-9]+", session):
            messagebox.showerror(APP_NAME, "Session labels can contain letters and numbers only.", parent=self.root)
            return
        if not output_text:
            messagebox.showerror(APP_NAME, "Choose a BIDS output folder.", parent=self.root)
            return
        output = Path(output_text).expanduser()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            messagebox.showerror(APP_NAME, "Choose a new or empty BIDS output folder. Existing data will not be overwritten.", parent=self.root)
            return
        if not config_text or not Path(config_text).expanduser().is_file():
            messagebox.showerror(APP_NAME, "Choose a dcm2bids JSON config that maps your sequences to BIDS.", parent=self.root)
            return
        executable = shutil.which("dcm2bids")
        if executable is None:
            messagebox.showerror(
                APP_NAME,
                "dcm2bids was not found. Install dcm2bids and dcm2niix, then restart the app so they are available on PATH.",
                parent=self.root,
            )
            return
        sorted_root = Path(self.output_var.get().strip()).expanduser()
        missing_subjects = [label for label, _ in participants if not (sorted_root / label).is_dir()]
        if missing_subjects:
            messagebox.showerror(
                APP_NAME,
                "Sort the DICOM files first. Missing sorted subject folder(s): " + ", ".join(missing_subjects),
                parent=self.root,
            )
            return
        selection = f"all {len(participants)} detected subjects" if convert_all else subject_label
        bids_labels = [f"sub-{participant}" for _, participant in participants]
        session_suffix = f"_ses-{session}" if session else ""
        if not messagebox.askyesno(
            APP_NAME,
            f"Convert {selection} to BIDS{session_suffix}?\n\n"
            f"Participants: {', '.join(bids_labels) if len(bids_labels) <= 5 else f'{len(bids_labels)} detected subjects'}\n\n"
            "The DICOM source will remain unchanged.",
            parent=self.root,
        ):
            return
        self._set_busy(True, f"Converting {selection} to BIDS… This may take several minutes.")

        def convert() -> str:
            config = json.loads(Path(config_text).expanduser().read_text(encoding="utf-8"))
            output.parent.mkdir(parents=True, exist_ok=True)
            for index, (label, participant) in enumerate(participants, 1):
                self.events.put(("progress", f"Converting {label} to BIDS ({index} of {len(participants)})…"))
                subject_source = sorted_root / label
                with tempfile.TemporaryDirectory(prefix="neuroflow-dcm2bids-", dir=output.parent) as temp_dir:
                    staged = Path(temp_dir) / "dicom"
                    staged.mkdir()
                    count = 0
                    for path in subject_source.rglob("*"):
                        if path.is_file():
                            count += 1
                            shutil.copyfile(path, staged / f"{count:08d}{path.suffix or '.dcm'}")
                    if not count:
                        raise ValueError(f"The sorted folder for {label} contains no files")
                    command = [executable, "-d", str(staged), "-p", participant,
                               "-c", str(Path(config_text).expanduser()), "-o", str(output)]
                    if session:
                        command.extend(["-s", session])
                    result = subprocess.run(command, capture_output=True, text=True)
                    if result.returncode:
                        details = (result.stderr or result.stdout or "No converter details were returned").strip()
                        raise RuntimeError(
                            f"dcm2bids failed for {label} ({index - 1} of {len(participants)} subjects completed; "
                            f"exit {result.returncode}). Partial output may exist at {output}.\n{details[-3000:]}"
                        )
                self.events.put(("progress", f"Finalizing {label} ({index} of {len(participants)})…"))
                finalize_bids(output, participant, session, config)
            return f"{len(participants)} subject(s) converted to BIDS: {output}"
        self._worker("bids_done", convert)

    def _update_copy_state(self) -> None:
        if self.busy:
            return
        allowed = bool(self.rows) and (
            self.review_var.get() or not any(row.get("status", "") != "matched" for row in self.rows)
        )
        self.copy_button.configure(state="normal" if allowed else "disabled")

    def _populate(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        subject_labels = sorted({str(row.get("study", "")) for row in rows if row.get("study")})
        choices = (ALL_SUBJECTS_OPTION, *subject_labels) if subject_labels else ()
        self.bids_subject_combo.configure(values=choices)
        self.bids_subject_var.set(subject_labels[0] if subject_labels else "")
        for item in self.table.get_children():
            self.table.delete(item)
        for row in rows:
            values = (
                row.get("study", ""), row.get("series_number", ""), row.get("description", ""),
                row.get("protocol_name", ""), row.get("file_count", ""), row.get("category", ""),
                row.get("status", ""),
            )
            status = row.get("status", "")
            self.table.insert("", "end", values=values, tags=(status,) if status in {"unmatched", "ambiguous"} else ())
        review_needed = sum(row.get("status") != "matched" for row in rows)
        self.review_var.set(False)
        if review_needed:
            self.review_check.pack(anchor="w", pady=(8, 4), before=self.status_label)
        else:
            self.review_check.pack_forget()
        self.copy_button.configure(state="disabled")
        self.status_var.set(
            f"{len(rows)} series found. "
            + (f"Review {review_needed} unmatched or ambiguous series before copying." if review_needed else "All series have one rule match.")
        )

    def _poll_events(self) -> None:
        try:
            while True:
                name, value = self.events.get_nowait()
                if name == "preview_done":
                    self._set_busy(False, "")
                    self._populate(value)
                    if not value:
                        messagebox.showwarning(APP_NAME, "No DICOM series with Study and Series Instance UIDs were found.", parent=self.root)
                elif name == "copy_done":
                    self._set_busy(False, "")
                    self._populate(value)
                    self.copy_button.configure(state="disabled")
                    self.open_output_button.configure(state="normal")
                    self.status_var.set(f"Copied {sum(row['file_count'] for row in value):,} files across {len(value)} series. Source files were left unchanged.")
                    messagebox.showinfo(APP_NAME, f"Sorting completed.\n\nOutput folder:\n{self.output_var.get()}", parent=self.root)
                elif name == "bids_done":
                    self._set_busy(False, f"BIDS conversion completed: {value}")
                    messagebox.showinfo(APP_NAME, f"BIDS conversion completed.\n\n{value}", parent=self.root)
                elif name == "progress":
                    self.status_var.set(str(value))
                elif name == "error":
                    self._set_busy(False, "The operation could not be completed.")
                    messagebox.showerror(APP_NAME, str(value), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)


def self_test() -> None:
    rules = load_rules(_bundled_preset())
    config_path = _bundled_preset().with_name(BIDS_CONFIG_NAME)
    with config_path.open(encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise ValueError(f"Bundled BIDS config is not a JSON object: {config_path}")
    print(f"{APP_NAME} {__version__}: resources and DICOM sorter imports are ready ({len(rules['rules'])} rules)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="neuroflow-dicom-sorter")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--self-test", action="store_true", help="verify bundled rules, BIDS config, and DICOM imports")
    actions.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0

    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("clam")
    except tk.TclError:
        pass
    SorterWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
