"""Standalone cross-platform desktop window for MRI film sheet PDFs."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import numpy as np
from PIL import ImageTk

from .film_sheet import (
    DEFAULT_SLICES,
    DicomSeries,
    FilmSheetOptions,
    ImageVolume,
    _display_range,
    _plane_coordinate_range,
    _slice_bounds,
    _suggest_axial_range_mm,
    discover_dicom_series,
    format_series_choice,
    load_image_volume,
    render_film_sheet_page_preview,
    write_film_sheet,
)


APP_NAME = "NeuroFlow MRI Film Sheet"


def _nifti_browser_entries(directory: str | Path) -> list[Path]:
    """List folders and NIfTI files for the in-app browser."""
    root = Path(directory).expanduser()
    try:
        entries = [path for path in root.iterdir() if path.is_dir() or path.name.casefold().endswith((".nii", ".nii.gz"))]
    except OSError:
        return []
    return sorted(entries, key=lambda path: (not path.is_dir(), path.name.casefold()))


class FilmSheetWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1100x820")
        self.root.minsize(800, 560)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.volume: ImageVolume | None = None
        self.axial_bounds: tuple[int, int] | None = None
        self.preview_range: tuple[float, float] | None = None
        self.source_path: Path | None = None
        self.series: list[DicomSeries] = []
        self.busy = False
        self._preview_image: ImageTk.PhotoImage | None = None
        self._preview_after_id: str | None = None
        self.plane_ranges: dict[str, tuple[str, str]] = {}
        self._range_editor_plane = "axial"

        self.patient_var = tk.StringVar()
        self.date_var = tk.StringVar()
        self.sequence_var = tk.StringVar(value="No image loaded")
        self.status_var = tk.StringVar(value="Open a DICOM folder or a BIDS/NIfTI image to begin.")
        self.slice_var = tk.IntVar(value=DEFAULT_SLICES)
        self.columns_var = tk.IntVar(value=5)
        self.axial_mode_var = tk.StringVar(value="Fit count to start/end")
        self.axial_gap_var = tk.StringVar()
        self.contrast_var = tk.DoubleVar(value=1.0)
        self.axial_start_var = tk.StringVar()
        self.axial_end_var = tk.StringVar()
        self.axial_var = tk.BooleanVar(value=True)
        self.coronal_var = tk.BooleanVar(value=False)
        self.sagittal_var = tk.BooleanVar(value=False)
        self.preview_page_var = tk.StringVar(value="axial")
        self.last_pdf: Path | None = None

        self._build()
        self.slice_var.trace_add("write", self._sampling_values_changed)
        for variable in (
            self.columns_var,
            self.patient_var,
            self.date_var,
            self.axial_mode_var,
            self.axial_gap_var,
            self.axial_start_var,
            self.axial_end_var,
            self.contrast_var,
            self.axial_var,
            self.coronal_var,
            self.sagittal_var,
        ):
            variable.trace_add("write", self._preview_values_changed)
        self.preview_page_var.trace_add("write", self._preview_plane_changed)
        self.root.after(100, self._poll_events)

    def _build(self) -> None:
        scroll_container = ttk.Frame(self.root)
        scroll_container.pack(fill="both", expand=True)
        self.main_canvas = tk.Canvas(scroll_container, highlightthickness=0, borderwidth=0)
        main_scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=self.main_canvas.yview)
        self.main_canvas.configure(yscrollcommand=main_scrollbar.set)
        self.main_canvas.pack(side="left", fill="both", expand=True)
        main_scrollbar.pack(side="right", fill="y")
        outer = ttk.Frame(self.main_canvas, padding=20)
        self._main_window = self.main_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", self._update_main_scroll_region)
        self.main_canvas.bind("<Configure>", self._resize_main_content)
        self.root.bind_all("<MouseWheel>", self._mousewheel_scroll)
        self.root.bind_all("<Button-4>", lambda _event: self.main_canvas.yview_scroll(-1, "units"))
        self.root.bind_all("<Button-5>", lambda _event: self.main_canvas.yview_scroll(1, "units"))
        ttk.Label(outer, text="MRI Film Sheet", font=("TkDefaultFont", 23, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Create a grayscale, printable image sheet from a DICOM series or 3D NIfTI. Images are processed locally; no clinical summary is added.",
            wraplength=940,
        ).pack(anchor="w", pady=(4, 16))

        source_box = ttk.LabelFrame(outer, text="1 · Open an image", padding=12)
        source_box.pack(fill="x")
        buttons = ttk.Frame(source_box)
        buttons.pack(anchor="w")
        ttk.Button(buttons, text="Open DICOM folder…", command=self._choose_dicom).pack(side="left")
        ttk.Button(buttons, text="Open NIfTI image…", command=self._choose_nifti).pack(side="left", padx=(8, 0))
        self.source_label = ttk.Label(source_box, text="No image selected", foreground="#5a6472")
        self.source_label.pack(anchor="w", pady=(8, 0))

        content = ttk.Frame(outer)
        content.pack(fill="both", expand=True, pady=(14, 10))
        settings = ttk.Frame(content)
        settings.pack(side="left", fill="y", padx=(0, 18))
        preview = ttk.LabelFrame(content, text="Film sheet page preview", padding=10)
        preview.pack(side="right", fill="both", expand=True)

        patient_box = ttk.LabelFrame(settings, text="2 · Patient and study", padding=10)
        patient_box.pack(fill="x")
        ttk.Label(patient_box, text="Patient name").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(patient_box, textvariable=self.patient_var, width=30).grid(row=1, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(patient_box, text="Study date (YYYY-MM-DD)").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(patient_box, textvariable=self.date_var, width=30).grid(row=3, column=0, sticky="ew", pady=(0, 5))
        patient_box.columnconfigure(0, weight=1)

        layout_box = ttk.LabelFrame(settings, text="3 · Film layout", padding=10)
        layout_box.pack(fill="x", pady=(10, 0))
        ttk.Label(layout_box, text="Images on each page").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(layout_box, from_=1, to=60, increment=1, textvariable=self.slice_var, width=8).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Label(layout_box, text="Grid columns").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(layout_box, from_=2, to=8, increment=1, textvariable=self.columns_var, width=8).grid(row=1, column=1, sticky="e", padx=(8, 0), pady=(8, 0))
        ttk.Label(layout_box, text="14 × 17 in portrait").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Separator(layout_box).grid(row=3, column=0, columnspan=2, sticky="ew", pady=9)
        ttk.Label(layout_box, text="Views").grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(layout_box, text="Axial", variable=self.axial_var).grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(layout_box, text="Coronal", variable=self.coronal_var).grid(row=6, column=0, sticky="w")
        ttk.Checkbutton(layout_box, text="Sagittal", variable=self.sagittal_var).grid(row=7, column=0, sticky="w")
        ttk.Label(layout_box, text="Plane sampling and coverage").grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.axial_mode = ttk.Combobox(
            layout_box,
            textvariable=self.axial_mode_var,
            values=("Fit count to start/end", "Fixed gap from start"),
            state="readonly",
            width=25,
        )
        self.axial_mode.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(2, 5))
        self.axial_mode.bind("<<ComboboxSelected>>", self._sampling_mode_changed)
        self.axial_start_label = ttk.Label(layout_box, text="Start plane (mm) · Axial")
        self.axial_start_label.grid(row=10, column=0, sticky="w")
        axial_start = ttk.Entry(layout_box, textvariable=self.axial_start_var, width=8)
        axial_start.grid(row=11, column=0, sticky="ew", padx=(0, 4))
        axial_start.bind("<KeyRelease>", self._axial_range_changed)
        self.axial_end_entry = ttk.Entry(layout_box, textvariable=self.axial_end_var, width=8)
        self.axial_end_entry.grid(row=11, column=1, sticky="ew", padx=(4, 0))
        self.axial_end_entry.bind("<KeyRelease>", self._axial_range_changed)
        self.axial_end_label = ttk.Label(layout_box, text="End plane (mm) · Axial")
        self.axial_end_label.grid(row=10, column=1, sticky="w", padx=(4, 0))
        ttk.Label(layout_box, text="Gap (mm)").grid(row=12, column=0, sticky="w", pady=(5, 0))
        self.axial_gap_entry = ttk.Entry(layout_box, textvariable=self.axial_gap_var, width=8)
        self.axial_gap_entry.grid(row=13, column=0, sticky="ew", padx=(0, 4))
        self.axial_gap_entry.bind("<KeyRelease>", self._axial_range_changed)
        ttk.Label(
            layout_box,
            text="Targets use the nearest source slices; duplicate panels are skipped. Fit mode spreads count across start/end; fixed-gap mode uses start, gap, and count.",
            wraplength=265,
        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Label(layout_box, text="Contrast").grid(row=15, column=0, sticky="w", pady=(8, 0))
        ttk.Scale(layout_box, from_=0.5, to=2.5, variable=self.contrast_var, orient="horizontal", length=150, command=self._preview_contrast_changed).grid(row=16, column=0, columnspan=2, sticky="ew")
        layout_box.columnconfigure(0, weight=1)

        preview_header = ttk.Frame(preview)
        preview_header.pack(fill="x")
        self.sequence_label = ttk.Label(preview_header, textvariable=self.sequence_var, font=("TkDefaultFont", 12, "bold"))
        self.sequence_label.pack(anchor="w", side="left", fill="x", expand=True)
        ttk.Label(preview_header, text="Page").pack(side="left", padx=(8, 4))
        self.preview_page_choice = ttk.Combobox(
            preview_header,
            textvariable=self.preview_page_var,
            values=("axial",),
            state="disabled",
            width=10,
        )
        self.preview_page_choice.pack(side="right")
        self.preview_page_choice.bind("<<ComboboxSelected>>", self._preview_plane_changed)
        preview_view = ttk.Frame(preview)
        preview_view.pack(fill="both", expand=True, pady=(8, 0))
        self.preview_canvas = tk.Canvas(preview_view, background="#454b54", highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", lambda _event: self._schedule_preview())
        ttk.Label(
            preview,
            text="Shows the selected PDF page with all chosen slices. The PDF keeps native source planes and voxel spacing.",
            foreground="#5a6472",
            wraplength=520,
        ).pack(anchor="w", pady=(5, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.generate_button = ttk.Button(actions, text="4 · Create PDF film sheet…", command=self._generate_pdf, state="disabled")
        self.generate_button.pack(side="left")
        self.open_pdf_button = ttk.Button(actions, text="Open last PDF", command=self._open_last_pdf, state="disabled")
        self.open_pdf_button.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=150)
        self.progress.pack(side="right")
        ttk.Label(outer, textvariable=self.status_var, wraplength=940).pack(anchor="w", pady=(9, 0))

    def _update_main_scroll_region(self, _event: tk.Event | None = None) -> None:
        self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

    def _resize_main_content(self, event: tk.Event) -> None:
        self.main_canvas.itemconfigure(self._main_window, width=event.width)

    def _mousewheel_scroll(self, event: tk.Event) -> None:
        delta = getattr(event, "delta", 0)
        if delta:
            self.main_canvas.yview_scroll(-1 if delta > 0 else 1, "units")

    def _choose_nifti(self) -> None:
        initial = self.source_path.parent if self.source_path else Path.home()
        self._show_nifti_browser(initial)

    def _show_nifti_browser(self, initial_directory: str | Path) -> None:
        """Use Tk widgets instead of macOS NSOpenPanel, which crashes on .nii.gz filters."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Choose a NIfTI image")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("760x520")
        dialog.minsize(560, 360)
        path_var = tk.StringVar(value=str(Path(initial_directory).expanduser()))
        ttk.Label(dialog, text="Select a .nii or .nii.gz image. Double-click a folder to open it.", padding=(12, 10)).pack(anchor="w")
        navigation = ttk.Frame(dialog, padding=(12, 0, 12, 8))
        navigation.pack(fill="x")
        path_entry = ttk.Entry(navigation, textvariable=path_var)
        path_entry.pack(side="left", fill="x", expand=True)
        tree = ttk.Treeview(dialog, columns=("kind",), show="tree headings", selectmode="browse")
        tree.heading("#0", text="Name")
        tree.heading("kind", text="Type")
        tree.column("#0", stretch=True)
        tree.column("kind", width=110, stretch=False)
        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill="both", expand=True, padx=12)
        browser_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=browser_scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        browser_scrollbar.pack(side="right", fill="y")
        selected_path: list[Path] = []

        def populate(directory: Path) -> None:
            directory = directory.expanduser()
            try:
                directory = directory.resolve()
            except OSError:
                pass
            if not directory.is_dir():
                messagebox.showerror(APP_NAME, f"Folder not found:\n{directory}", parent=dialog)
                return
            path_var.set(str(directory))
            tree.delete(*tree.get_children())
            for index, entry in enumerate(_nifti_browser_entries(directory)):
                kind = "Folder" if entry.is_dir() else "NIfTI image"
                tree.insert("", "end", iid=str(index), text=entry.name, values=(kind,))

        def enter_selected(_event: tk.Event | None = None) -> None:
            selection = tree.selection()
            if not selection:
                return
            entry = _nifti_browser_entries(path_var.get())[int(selection[0])]
            if entry.is_dir():
                populate(entry)
            else:
                choose()

        def choose() -> None:
            selection = tree.selection()
            if not selection:
                messagebox.showwarning(APP_NAME, "Select a NIfTI image to continue.", parent=dialog)
                return
            entry = _nifti_browser_entries(path_var.get())[int(selection[0])]
            if entry.is_dir():
                populate(entry)
                return
            selected_path.append(entry)
            dialog.destroy()
            self._open_nifti_path(selected_path[0])

        ttk.Button(navigation, text="Go", command=lambda: populate(Path(path_var.get()))).pack(side="left", padx=(8, 0))
        ttk.Button(navigation, text="Up", command=lambda: populate(Path(path_var.get()).parent)).pack(side="left", padx=(6, 0))
        tree.bind("<Double-1>", enter_selected)
        buttons = ttk.Frame(dialog, padding=12)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Open image", command=choose).pack(side="right", padx=(0, 8))
        populate(Path(initial_directory))

    def _open_nifti_path(self, path: str | Path) -> None:
        path = Path(path)
        self.source_path = path
        self._begin_input_change()
        self.source_label.configure(text=str(path))
        self._load_async(path)

    def _choose_dicom(self) -> None:
        selected = filedialog.askdirectory(title="Choose a DICOM folder", parent=self.root)
        if selected:
            self.source_path = Path(selected)
            self._begin_input_change()
            self.source_label.configure(text=f"{self.source_path}  ·  scanning DICOM headers")
            self._set_busy(True, "Scanning DICOM headers for image series…")
            self._worker("series", lambda: discover_dicom_series(selected))

    def _load_async(self, source: Path, series_uid: str | None = None) -> None:
        self._set_busy(True, "Reading image data and preparing source-plane views…")
        self._worker("loaded", lambda: load_image_volume(source, series_uid=series_uid))

    def _begin_input_change(self) -> None:
        self.volume = None
        self.axial_bounds = None
        self.preview_range = None
        self.patient_var.set("")
        self.date_var.set("")
        self.axial_start_var.set("")
        self.axial_end_var.set("")
        self.sequence_var.set("Loading image…")
        self.generate_button.configure(state="disabled")
        self.open_pdf_button.configure(state="disabled")
        self.preview_canvas.delete("all")
        self.preview_page_choice.configure(values=(), state="disabled")
        self.preview_page_var.set("")

    def _worker(self, event_name: str, callback: Any) -> None:
        def run() -> None:
            try:
                self.events.put((event_name, callback()))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                try:
                    name, payload = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    if name == "series":
                        self._series_ready(payload)
                    elif name == "loaded":
                        self._volume_ready(payload)
                    elif name == "pdf":
                        self.last_pdf = payload
                        self._set_busy(False, f"Created PDF: {payload}")
                    elif name == "error":
                        self._set_busy(False, "The operation could not be completed.")
                        messagebox.showerror(APP_NAME, str(payload), parent=self.root)
                except Exception as exc:
                    self._set_busy(False, f"The operation could not be completed: {exc}")
                    messagebox.showerror(APP_NAME, str(exc), parent=self.root)
        finally:
            # Keep draining worker results even if a UI callback fails. Otherwise
            # the progress bar can run forever and leave controls disabled after
            # an image or PDF has already been loaded/written.
            try:
                self.root.after(100, self._poll_events)
            except tk.TclError:
                pass

    def _series_ready(self, series: list[DicomSeries]) -> None:
        self._set_busy(False, f"Found {len(series)} DICOM series.")
        if not series:
            messagebox.showerror(APP_NAME, "No readable DICOM series were found in that folder.", parent=self.root)
            return
        self.series = series
        if len(series) == 1:
            self._choose_series(series[0])
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Choose a DICOM series")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("780x340")
        ttk.Label(dialog, text="Choose the anatomical series to place on the film sheet. Select the complete multi-slice sequence.", padding=12).pack(anchor="w")
        tree = ttk.Treeview(dialog, columns=("description", "protocol", "images", "date"), show="headings", selectmode="browse")
        for key, label, width in (("description", "Series description", 270), ("protocol", "Protocol", 250), ("images", "Images", 80), ("date", "Study date", 120)):
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=key in {"description", "protocol"})
        for i, item in enumerate(series):
            tree.insert("", "end", iid=str(i), values=(item.description, item.protocol, item.instance_count, item.study_date))
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        buttons = ttk.Frame(dialog, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")

        def select() -> None:
            chosen = tree.selection()
            if not chosen:
                messagebox.showwarning(APP_NAME, "Select one series to continue.", parent=dialog)
                return
            dialog.destroy()
            self._choose_series(series[int(chosen[0])])

        ttk.Button(buttons, text="Load selected series", command=select).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=(0, 8))
        tree.bind("<Double-1>", lambda _event: select())

    def _choose_series(self, item: DicomSeries) -> None:
        if self.source_path is None:
            return
        self.patient_var.set(item.patient_name)
        self.date_var.set(item.study_date)
        self.source_label.configure(text=f"{self.source_path}  ·  {format_series_choice(item)}")
        self._load_async(self.source_path, item.uid)

    def _volume_ready(self, volume: ImageVolume) -> None:
        self.volume = volume
        self.axial_bounds = _slice_bounds(volume.data, 2)
        self.preview_range = volume.display_range or _display_range(volume.data)
        if volume.patient_name:
            self.patient_var.set(volume.patient_name)
        if volume.study_date:
            self.date_var.set(volume.study_date)
        axial_start, axial_end = _suggest_axial_range_mm(volume, bounds=self.axial_bounds)
        self.plane_ranges = {
            "axial": (f"{axial_start:.0f}", f"{axial_end:.0f}"),
            "coronal": tuple(f"{value:.0f}" for value in _plane_coordinate_range(volume, "coronal")),
            "sagittal": tuple(f"{value:.0f}" for value in _plane_coordinate_range(volume, "sagittal")),
        }
        self._range_editor_plane = "axial"
        self.axial_start_var.set(f"{axial_start:.0f}")
        self.axial_end_var.set(f"{axial_end:.0f}")
        self._refresh_sampling_values()
        self.sequence_var.set(f"{volume.sequence}  ·  {volume.data.shape[0]} × {volume.data.shape[1]} × {volume.data.shape[2]} voxels")
        if self.source_path and self.source_path.is_file():
            self.source_label.configure(text=str(self.source_path))
        self.generate_button.configure(state="normal")
        self._set_busy(False, "Image loaded. Review the layout settings, then create the PDF.")
        self._update_preview_pages()
        self._schedule_preview()

    def _draw_preview(self) -> None:
        if self.volume is None:
            return
        self._preview_after_id = None
        width = max(1, self.preview_canvas.winfo_width())
        height = max(1, self.preview_canvas.winfo_height())
        try:
            if width < 100 or height < 100:
                return
            options = self._film_options()
            plane = self.preview_page_var.get()
            if plane not in options.planes:
                plane = options.planes[0]
                self.preview_page_var.set(plane)
            page_width = max(200, min(width - 24, int((height - 24) * 14 / 17)))
            page = render_film_sheet_page_preview(
                self.volume,
                options,
                plane,  # type: ignore[arg-type]
                patient_name=self.patient_var.get(),
                study_date=self.date_var.get(),
                width=page_width,
            )
        except (ValueError, tk.TclError):
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(
                width // 2,
                height // 2,
                text="Enter valid film layout settings to preview the PDF page.",
                fill="#d5dbe4",
                width=max(160, width - 40),
                justify="center",
            )
            return
        self._preview_image = ImageTk.PhotoImage(page)
        self.preview_canvas.delete("all")
        x = max(0, (width - page.width) // 2)
        y = max(0, (height - page.height) // 2)
        self.preview_canvas.create_image(x, y, image=self._preview_image, anchor="nw")

    def _schedule_preview(self) -> None:
        if self.volume is None:
            return
        if self._preview_after_id is not None:
            try:
                self.root.after_cancel(self._preview_after_id)
            except tk.TclError:
                pass
        try:
            self._preview_after_id = self.root.after(90, self._draw_preview)
        except tk.TclError:
            self._preview_after_id = None

    def _preview_values_changed(self, *_args: object) -> None:
        if self.volume is not None:
            self._cache_active_range()
            self._update_preview_pages()
            self._schedule_preview()

    def _cache_active_range(self) -> None:
        if self.volume is None:
            return
        try:
            start = float(self.axial_start_var.get())
            end = float(self.axial_end_var.get())
            if np.isfinite(start) and np.isfinite(end):
                self.plane_ranges[self._range_editor_plane] = (str(self.axial_start_var.get()), str(self.axial_end_var.get()))
        except (TypeError, ValueError):
            pass

    def _preview_plane_changed(self, *_args: object) -> None:
        if self.volume is None:
            return
        self._cache_active_range()
        plane = self.preview_page_var.get()
        if plane not in {"axial", "coronal", "sagittal"}:
            return
        self._range_editor_plane = plane
        self.axial_start_label.configure(text=f"Start plane (mm) · {plane.title()}")
        self.axial_mode.configure(state="readonly" if plane == "axial" else "disabled")
        values = self.plane_ranges.get(plane)
        if values:
            self.axial_start_var.set(values[0])
            self.axial_end_var.set(values[1])
        self._refresh_sampling_values()
        self._schedule_preview()

    def _update_preview_pages(self) -> None:
        pages = [
            name
            for enabled, name in (
                (self.axial_var.get(), "axial"),
                (self.coronal_var.get(), "coronal"),
                (self.sagittal_var.get(), "sagittal"),
            )
            if enabled
        ]
        self.preview_page_choice.configure(values=pages, state="readonly" if pages and self.volume is not None else "disabled")
        if pages and self.preview_page_var.get() not in pages:
            self.preview_page_var.set(pages[0])

    def _film_options(self) -> FilmSheetOptions:
        planes = tuple(
            name
            for enabled, name in (
                (self.axial_var.get(), "axial"),
                (self.coronal_var.get(), "coronal"),
                (self.sagittal_var.get(), "sagittal"),
            )
            if enabled
        )
        if not planes:
            raise ValueError("Select at least one view plane.")
        axial_range = None
        coronal_range = None
        sagittal_range = None
        axial_start = None
        axial_gap = None
        ranges: dict[str, tuple[float, float]] = {}
        for plane in planes:
            raw_range = self.plane_ranges.get(plane)
            if plane == self._range_editor_plane:
                raw_range = (self.axial_start_var.get(), self.axial_end_var.get())
            elif raw_range is None:
                raise ValueError(f"No coverage range is set for {plane} view.")
            ranges[plane] = (float(raw_range[0]), float(raw_range[1]))
        if "axial" in planes:
            if self.axial_mode_var.get() == "Fixed gap from start" and self._range_editor_plane == "axial":
                axial_start = float(self.axial_start_var.get())
                axial_gap = float(self.axial_gap_var.get())
                if axial_gap <= 0:
                    raise ValueError("Axial gap must be greater than zero.")
            else:
                axial_range = ranges["axial"]
                if axial_range[1] < axial_range[0] or (
                    axial_range[1] == axial_range[0] and int(self.slice_var.get()) > 1
                ):
                    raise ValueError("Axial end plane must be greater than the start plane when using multiple images.")
        if "coronal" in planes:
            coronal_range = ranges["coronal"]
        if "sagittal" in planes:
            sagittal_range = ranges["sagittal"]
        return FilmSheetOptions(
            slices_per_plane=int(self.slice_var.get()),
            columns=int(self.columns_var.get()),
            planes=planes,  # type: ignore[arg-type]
            contrast=float(self.contrast_var.get()),
            axial_range_mm=axial_range,
            axial_start_mm=axial_start,
            axial_gap_mm=axial_gap,
            coronal_range_mm=coronal_range,
            sagittal_range_mm=sagittal_range,
        )

    def _preview_contrast_changed(self, _value: str = "") -> None:
        if self.volume is not None:
            self._schedule_preview()

    def _axial_range_changed(self, _event: tk.Event | None = None) -> None:
        if self.volume is not None:
            self._refresh_sampling_values()
            self._schedule_preview()

    def _sampling_values_changed(self, *_args: object) -> None:
        if self.volume is not None:
            self._refresh_sampling_values()
            self._schedule_preview()

    def _sampling_mode_changed(self, _event: tk.Event | None = None) -> None:
        self._refresh_sampling_values()
        self._update_preview_pages()
        self._schedule_preview()

    def _refresh_sampling_values(self) -> None:
        plane = self._range_editor_plane
        self.axial_mode.configure(state="readonly" if plane == "axial" else "disabled")
        fixed_gap = plane == "axial" and self.axial_mode_var.get() == "Fixed gap from start"
        self.axial_end_entry.configure(state="disabled" if fixed_gap else "normal")
        self.axial_gap_entry.configure(state="normal" if fixed_gap else "disabled")
        self.axial_end_label.configure(text=f"End (calculated, mm) · {plane.title()}" if fixed_gap else f"End plane (mm) · {plane.title()}")
        try:
            start = float(self.axial_start_var.get())
            count = int(self.slice_var.get())
            if fixed_gap:
                gap = float(self.axial_gap_var.get())
                if gap <= 0 or count < 1:
                    return
                end = start + gap * (count - 1)
                self.axial_end_var.set(f"{end:.1f}")
            else:
                end = float(self.axial_end_var.get())
                if end <= start or count < 1:
                    return
                gap = 0.0 if count == 1 else (end - start) / (count - 1)
                self.axial_gap_var.set(f"{gap:.1f}")
        except (ValueError, tk.TclError):
            return

    def _generate_pdf(self) -> None:
        if self.volume is None:
            return
        try:
            options = self._film_options()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror(APP_NAME, f"Check the film layout values: {exc}", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            title="Save MRI film sheet",
            defaultextension=".pdf",
            initialfile="mri_film_sheet.pdf",
            filetypes=(("PDF document", "*.pdf"),),
            parent=self.root,
        )
        if not path:
            return
        volume = self.volume
        patient_name = self.patient_var.get()
        study_date = self.date_var.get()
        self._set_busy(True, "Rendering the selected views to PDF…")
        self._worker("pdf", lambda: write_film_sheet(
            volume,
            path,
            patient_name=patient_name,
            study_date=study_date,
            options=options,
        ))

    def _open_last_pdf(self) -> None:
        if self.last_pdf is None or not self.last_pdf.is_file():
            messagebox.showerror(APP_NAME, "The saved PDF is not available.", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(self.last_pdf)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.last_pdf)])
            else:
                subprocess.Popen(["xdg-open", str(self.last_pdf)])
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open the PDF: {exc}", parent=self.root)

    def _set_busy(self, busy: bool, message: str) -> None:
        self.busy = busy
        self.status_var.set(message)
        state = "disabled" if busy else "normal"
        for widget in self.root.winfo_children():
            self._set_widget_state(widget, state)
        if not busy:
            self.generate_button.configure(state="normal" if self.volume is not None else "disabled")
            pdf_available = self.last_pdf is not None and self.last_pdf.is_file()
            self.open_pdf_button.configure(state="normal" if pdf_available else "disabled")
            self._refresh_sampling_values()
        self.progress.start(12) if busy else self.progress.stop()

    def _set_widget_state(self, widget: tk.Widget, state: str) -> None:
        if isinstance(widget, ttk.Combobox):
            try:
                widget.configure(state="disabled" if state == "disabled" else "readonly")
            except tk.TclError:
                pass
        elif isinstance(widget, (ttk.Button, ttk.Entry, ttk.Spinbox, ttk.Checkbutton, ttk.Scale)):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            self._set_widget_state(child, state)


def main() -> int:
    if sys.argv[1:] == ["--smoke-test"]:
        return _smoke_test()
    if sys.argv[1:3] == ["--smoke-test", "--result-file"] and len(sys.argv) == 4:
        return _smoke_test(Path(sys.argv[3]))
    if sys.argv[1:3] == ["--engine-smoke-test", "--result-file"] and len(sys.argv) == 4:
        return _engine_smoke_test(Path(sys.argv[3]))
    root = tk.Tk()
    FilmSheetWindow(root)
    root.mainloop()
    return 0


def _smoke_test(result_file: Path | None = None) -> int:
    """Exercise the desktop controls and PDF writer in a native build runner."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = FilmSheetWindow(root)
        volume = _smoke_volume()
        app._volume_ready(volume)
        preview_page = render_film_sheet_page_preview(
            volume,
            app._film_options(),
            "axial",
            patient_name="GUI smoke test",
            width=500,
        )
        if preview_page.width != 500 or preview_page.height != round(500 * 17 / 14):
            raise RuntimeError("The film-sheet page preview did not render at the expected page aspect ratio.")
        app.axial_mode_var.set("Fixed gap from start")
        app._sampling_mode_changed()
        app.slice_var.set(5)
        app.axial_start_var.set("-5")
        app.axial_gap_var.set("3")
        app._sampling_values_changed()
        if app.axial_end_var.get() != "7.0":
            raise RuntimeError("Fixed-gap mode did not calculate the end plane correctly.")
        _write_smoke_pdf(volume, "GUI smoke test")
        if result_file is not None:
            _write_smoke_result(result_file, "MRI film-sheet GUI and PDF smoke test passed.")
        print("MRI film-sheet GUI and PDF smoke test passed.")
        return 0
    finally:
        root.destroy()


def _engine_smoke_test(result_file: Path) -> int:
    import nibabel as nib

    volume = _smoke_volume()
    with tempfile.TemporaryDirectory(prefix="neuroflow-film-sheet-smoke-") as directory:
        nifti = Path(directory) / "synthetic_T1w.nii.gz"
        nib.save(nib.Nifti1Image(volume.data, volume.affine), nifti)
        loaded = load_image_volume(nifti)
        if loaded.data.shape != volume.data.shape:
            raise RuntimeError("The packaged NIfTI reader returned an unexpected volume shape.")
        _write_smoke_pdf(loaded, "Packaged NIfTI and PDF smoke test")
    _write_smoke_result(result_file, "MRI film-sheet packaged NIfTI loading and PDF smoke test passed.")
    return 0


def _smoke_volume() -> ImageVolume:
    x, y, z = np.ogrid[:48, :48, :16]
    data = np.zeros((48, 48, 16), dtype=np.float32)
    data[((x - 24) * 0.8) ** 2 + ((y - 24) * 0.8) ** 2 + ((z - 8) * 5.0) ** 2 < 18 ** 2] = 900
    affine = np.diag((0.8, 0.8, 5.0, 1.0))
    affine[2, 3] = -40
    return ImageVolume(data=data, affine=affine, source_label="MRI film-sheet smoke test")


def _write_smoke_pdf(volume: ImageVolume, label: str) -> None:
    options = FilmSheetOptions(
        slices_per_plane=5,
        planes=("axial",),
        axial_start_mm=-5,
        axial_gap_mm=3,
    )
    with tempfile.TemporaryDirectory(prefix="neuroflow-film-sheet-smoke-") as directory:
        output = write_film_sheet(
            volume,
            Path(directory) / "smoke-test.pdf",
            patient_name=label,
            options=options,
        )
        if not output.is_file() or output.stat().st_size < 1000 or output.read_bytes()[:5] != b"%PDF-":
            raise RuntimeError("The MRI film-sheet smoke test did not produce a valid PDF.")


def _write_smoke_result(result_file: Path, message: str) -> None:
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(f"{message}\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

