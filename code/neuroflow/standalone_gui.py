"""Standalone cross-platform desktop DICOM sorter.

This deliberately imports only the shared sorter core, so the desktop build does
not need NeuroFlow site configuration, processing containers, or a browser.
"""

from __future__ import annotations

import argparse
import os
import json
import contextlib
import io
import logging
import traceback
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
PRESET_NAME = "dicom_sort_rules_general.json"
PRISMA_PRESET_NAME = "dicom_sort_rules_20261016.json"
BIDS_CONFIG_NAME = "dcm2bids_config_prisma.json"
ALL_SUBJECTS_OPTION = "All subjects"
TABLE_COLUMNS = ("subject", "series", "description", "protocol", "files", "category", "status")


def _user_config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "neuroflow-dicom-sorter"


def _bundled_preset(name: str = PRESET_NAME) -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidate = Path(frozen_root) / "neuroflow" / "data" / name
        if candidate.is_file():
            return candidate
    return Path(str(files("neuroflow").joinpath("data", name)))


def _bundled_converter(name: str) -> Path | None:
    """Return a converter shipped in the standalone bundle, if present."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if not frozen_root:
        return None
    suffix = ".exe" if sys.platform == "win32" else ""
    candidate = Path(frozen_root) / "tools" / f"{name}{suffix}"
    return candidate if candidate.is_file() else None


def _install_user_preset() -> Path:
    destination = _user_config_dir() / PRESET_NAME
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_bundled_preset(), destination)
    return destination


def _install_prisma_preset() -> Path:
    destination = _user_config_dir() / PRISMA_PRESET_NAME
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_bundled_preset(PRISMA_PRESET_NAME), destination)
    return destination


def _install_user_bids_config() -> Path:
    destination = _user_config_dir() / BIDS_CONFIG_NAME
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_bundled_preset().with_name(BIDS_CONFIG_NAME), destination)
    else:
        # Remove the old default T1-dark-fluid-as-T1w rule on upgrade while
        # preserving the user's other edits. Keep a one-time backup first.
        try:
            config = json.loads(destination.read_text(encoding="utf-8"))
            descriptions = config.get("descriptions", [])
            filtered = [
                description for description in descriptions
                if not (
                    description.get("datatype") == "anat"
                    and description.get("suffix") == "T1w"
                    and any(
                        str(description.get("criteria", {}).get(key, "")).casefold()
                        == "t1_tse_dark-fluid_tra"
                        for key in ("SeriesDescription", "ProtocolName")
                    )
                )
            ]
            if len(filtered) != len(descriptions):
                backup = destination.with_suffix(destination.suffix + ".backup")
                if not backup.exists():
                    shutil.copy2(destination, backup)
                config["descriptions"] = filtered
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
                temporary.replace(destination)
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            # Leave malformed or unfamiliar user configs untouched; dcm2bids
            # will report their validation error when conversion is requested.
            pass
    return destination


class SorterWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{__version__}")
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
        self.prisma_rules_path = _install_prisma_preset()
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

        ttk.Label(outer, text=f"DICOM Sorter · v{__version__}", font=("TkDefaultFont", 20, "bold")).pack(anchor="w")
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
            text="The general preset recognizes common MRI sequence families across vendors. Matches still need review when names are ambiguous. The observed Prisma protocol preset is also available.",
            wraplength=900,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._path_row(self.advanced_frame, "Rule preset", self.rules_var, self._choose_rules, 1)
        ttk.Button(self.advanced_frame, text="Open rule file", command=self._open_rules).grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Button(self.advanced_frame, text="Use Prisma preset", command=lambda: self._select_rules(self.prisma_rules_path)).grid(row=3, column=1, sticky="w", pady=(6, 0))
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
            text="The included Prisma config maps tested anatomy, multi-echo REST, diffusion, and fieldmap series. Review it before conversion. The standalone app includes dcm2bids and dcm2niix.",
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

    def _select_rules(self, path: Path) -> None:
        self.rules_var.set(str(path))

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
        dcm2niix = _bundled_converter("dcm2niix") or shutil.which("dcm2niix")
        if not _has_dcm2bids():
            messagebox.showerror(
                APP_NAME,
                "The bundled dcm2bids engine is missing. Reinstall the app before converting to BIDS.",
                parent=self.root,
            )
            return
        if dcm2niix is None:
            messagebox.showerror(
                APP_NAME,
                "The bundled dcm2niix tool is missing. Reinstall the app, or install dcm2niix on PATH.",
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
            env = _converter_environment(Path(dcm2niix))
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
                    arguments = ["-d", str(staged), "-p", participant,
                                 "-c", str(Path(config_text).expanduser()), "-o", str(output)]
                    if session:
                        arguments.extend(["-s", session])
                    _prepare_external_process()
                    result = _invoke_dcm2bids(arguments, env)
                    if result.returncode:
                        details = (result.stderr or result.stdout or "No converter details were returned").strip()
                        raise RuntimeError(
                            f"dcm2bids failed for {label} ({index - 1} of {len(participants)} subjects completed; "
                            f"exit {result.returncode}). App v{__version__}; bundled dcm2bids 3.3.1. "
                            f"Command: {result.args!r}. Partial output may exist at {output}.\n{details[-3000:]}"
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


def _write_converter_smoke_dicom(path: Path, *, study_uid: str, series_uid: str, instance: int) -> None:
    from pydicom import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

    sop_uid = generate_uid()
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = MRImageStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = MRImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.PatientName = "NeuroFlow^Synthetic"
    ds.PatientID = "NFSMOKE"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.StudyDate = "20260101"
    ds.StudyTime = "101500.25"
    ds.SeriesNumber = 1
    ds.SeriesDescription = "NeuroFlow Synthetic T1"
    ds.ProtocolName = "NeuroFlow Synthetic T1"
    ds.AcquisitionTime = "101500.25"
    ds.InstanceNumber = instance
    ds.Modality = "MR"
    ds.Manufacturer = "Siemens"
    ds.MagneticFieldStrength = 3.0
    ds.MRAcquisitionType = "2D"
    ds.PatientPosition = "HFS"
    ds.RepetitionTime = 10.0
    ds.Rows = 4
    ds.Columns = 4
    ds.PixelSpacing = [1.0, 1.0]
    ds.SliceThickness = 1.0
    ds.SpacingBetweenSlices = 1.0
    ds.ImagePositionPatient = [0.0, 0.0, float(instance - 1)]
    ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = bytes([instance] * (ds.Rows * ds.Columns * 2))
    ds.save_as(path, enforce_file_format=True)


def _converter_command(executable: Path, arguments: list[str]) -> list[str]:
    """Build a child command that preserves Windows executable identity."""
    argv0 = executable.name if sys.platform == "win32" else str(executable)
    return [argv0, *arguments]


def _converter_launch_kwargs(executable: Path) -> dict[str, str]:
    # Supply the full path separately to CreateProcess while keeping argv[0]
    # free of spaces. dcm2bids 3.3.x includes the maintained Windows build.
    return {"executable": str(executable)} if sys.platform == "win32" else {}


def _prepare_external_process() -> None:
    if sys.platform == "win32":
        # PyInstaller sets the parent app directory as the process DLL search
        # directory. Windows propagates it to children unless it is reset.
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel32.SetDllDirectoryW.restype = ctypes.c_bool
        if not kernel32.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())


def _has_dcm2bids() -> bool:
    try:
        import dcm2bids.cli.dcm2bids  # noqa: F401
    except ImportError:
        return False
    return True


def _invoke_dcm2bids(
    arguments: list[str],
    env: dict[str, str],
    *,
    entrypoint: Any = None,
) -> subprocess.CompletedProcess[str]:
    """Run dcm2bids in-process so Windows paths and schema data stay internal."""
    if entrypoint is None:
        from dcm2bids.cli.dcm2bids import main as entrypoint

    command = ["dcm2bids", *arguments]
    captured = io.StringIO()
    previous_argv = sys.argv
    previous_path = os.environ.get("PATH")
    root_logger = logging.getLogger()
    previous_handlers = root_logger.handlers[:]
    previous_level = root_logger.level
    try:
        root_logger.handlers = []
        root_logger.setLevel(logging.WARNING)
        os.environ["PATH"] = env.get("PATH", os.environ.get("PATH", ""))
        sys.argv = command[:]
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            entrypoint()
        return subprocess.CompletedProcess(command, 0, captured.getvalue(), "")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return subprocess.CompletedProcess(command, code, captured.getvalue(), "")
    except Exception:
        return subprocess.CompletedProcess(command, 1, captured.getvalue(), traceback.format_exc())
    finally:
        for handler in root_logger.handlers:
            if handler not in previous_handlers:
                handler.close()
        root_logger.handlers = previous_handlers
        root_logger.setLevel(previous_level)
        sys.argv = previous_argv
        if previous_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous_path


def _converter_environment(*executables: Path) -> dict[str, str]:
    env = os.environ.copy()
    tool_dirs = []
    for executable in executables:
        directory = str(executable.parent)
        if directory not in tool_dirs:
            tool_dirs.append(directory)
    env["PATH"] = os.pathsep.join([*tool_dirs, env.get("PATH", "")])
    return env


def self_test(*, conversion: bool = False) -> None:
    rules = load_rules(_bundled_preset())
    config_path = _bundled_preset().with_name(BIDS_CONFIG_NAME)
    with config_path.open(encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise ValueError(f"Bundled BIDS config is not a JSON object: {config_path}")
    # The Linux/HPC controller package imports this module for its installer
    # smoke test, but intentionally does not install standalone conversion
    # dependencies. The desktop bundles must always include the engine; any
    # explicit conversion smoke test must verify it on every platform.
    if (conversion or sys.platform in {"win32", "darwin"}) and not _has_dcm2bids():
        raise RuntimeError("Standalone package is missing the dcm2bids Python engine")
    dcm2niix = _bundled_converter("dcm2niix") or shutil.which("dcm2niix")
    if conversion and not dcm2niix:
        raise RuntimeError("Conversion smoke test needs dcm2niix bundled or available on PATH")
    if dcm2niix:
        dcm2niix_path = Path(dcm2niix)
        env = _converter_environment(dcm2niix_path)
        _prepare_external_process()
        result = subprocess.run(
            _converter_command(dcm2niix_path, ["-h"]), capture_output=True, text=True,
            env=env, check=True, timeout=30, **_converter_launch_kwargs(dcm2niix_path),
        )
        identity = (result.stdout + "\n" + result.stderr).lower()
        if "dcm2niix" not in identity:
            raise RuntimeError(
                f"Unexpected executable launched for dcm2niix: {dcm2niix_path}; "
                f"reported output: {identity.strip()[:1000]}"
            )
        if conversion:
            from pydicom.uid import generate_uid

            with tempfile.TemporaryDirectory(prefix="neuroflow-sorter-smoke-") as temporary:
                root = Path(temporary)
                source = root / "dicom"
                source.mkdir()
                study_uid, series_uid = generate_uid(), generate_uid()
                for instance in (1, 2):
                    _write_converter_smoke_dicom(
                        source / f"image-{instance}.dcm", study_uid=study_uid,
                        series_uid=series_uid, instance=instance,
                    )
                smoke_config = root / "config.json"
                smoke_config.write_text(json.dumps({
                    "search_method": "fnmatch",
                    "descriptions": [{
                        "datatype": "anat", "suffix": "T1w",
                        "criteria": {"SeriesDescription": "NeuroFlow Synthetic T1"},
                    }],
                }), encoding="utf-8")
                output = root / "bids output with spaces"
                arguments = ["-d", str(source), "-p", "NFSMOKE", "-c", str(smoke_config), "-o", str(output)]
                result = _invoke_dcm2bids(arguments, env)
                if result.returncode:
                    details = (result.stderr or result.stdout or "No converter details were returned").strip()
                    raise RuntimeError(
                        f"Bundled dcm2bids conversion smoke test failed (exit {result.returncode}); "
                        f"app v{__version__}; bundled dcm2niix: {dcm2niix_path}\n{details[-5000:]}"
                    )
                nifti = output / "sub-NFSMOKE" / "anat" / "sub-NFSMOKE_T1w.nii.gz"
                if not nifti.is_file():
                    found = [str(path.relative_to(output)) for path in output.rglob("*.nii*")]
                    raise RuntimeError(f"dcm2bids smoke test made no expected T1w NIfTI; found: {found}")
            print("Bundled dcm2bids + dcm2niix end-to-end conversion smoke test passed")
    print(f"{APP_NAME} {__version__}: resources and DICOM sorter imports are ready ({len(rules['rules'])} rules)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="neuroflow-dicom-sorter")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--self-test", action="store_true", help="verify bundled rules, BIDS config, and converter executables")
    actions.add_argument("--self-test-conversion", action="store_true", help="run an end-to-end conversion on synthetic DICOM files")
    actions.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.self_test or args.self_test_conversion:
        self_test(conversion=args.self_test_conversion)
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
