"""Standalone cross-platform desktop window for MRI film sheet PDFs."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

from .film_sheet import (
    DEFAULT_SLICES,
    DicomSeries,
    FilmSheetOptions,
    ImageVolume,
    _display_range,
    _extract_plane,
    _normalized_image,
    _plane_aspect_ratio,
    _slice_bounds,
    _suggest_axial_range_mm,
    discover_dicom_series,
    format_series_choice,
    load_image_volume,
    write_film_sheet,
)


APP_NAME = "NeuroFlow MRI Film Sheet"


class FilmSheetWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1020x760")
        self.root.minsize(850, 650)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.volume: ImageVolume | None = None
        self.axial_bounds: tuple[int, int] | None = None
        self.preview_range: tuple[float, float] | None = None
        self.source_path: Path | None = None
        self.series: list[DicomSeries] = []
        self.busy = False
        self._preview_image: ImageTk.PhotoImage | None = None

        self.patient_var = tk.StringVar()
        self.date_var = tk.StringVar()
        self.sequence_var = tk.StringVar(value="No image loaded")
        self.status_var = tk.StringVar(value="Open a DICOM folder or a BIDS/NIfTI image to begin.")
        self.slice_var = tk.IntVar(value=DEFAULT_SLICES)
        self.columns_var = tk.IntVar(value=5)
        self.contrast_var = tk.DoubleVar(value=1.0)
        self.axial_start_var = tk.StringVar()
        self.axial_end_var = tk.StringVar()
        self.axial_var = tk.BooleanVar(value=True)
        self.coronal_var = tk.BooleanVar(value=False)
        self.sagittal_var = tk.BooleanVar(value=False)
        self.last_pdf: Path | None = None

        self._build()
        self.root.after(100, self._poll_events)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
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
        preview = ttk.LabelFrame(content, text="Axial center-slice preview", padding=10)
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
        ttk.Label(layout_box, text="Slices on each page").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(layout_box, from_=6, to=60, increment=1, textvariable=self.slice_var, width=8).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Label(layout_box, text="Grid columns").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(layout_box, from_=2, to=8, increment=1, textvariable=self.columns_var, width=8).grid(row=1, column=1, sticky="e", padx=(8, 0), pady=(8, 0))
        ttk.Label(layout_box, text="14 × 17 in portrait").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Separator(layout_box).grid(row=3, column=0, columnspan=2, sticky="ew", pady=9)
        ttk.Label(layout_box, text="Views").grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(layout_box, text="Axial", variable=self.axial_var).grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(layout_box, text="Coronal", variable=self.coronal_var).grid(row=6, column=0, sticky="w")
        ttk.Checkbutton(layout_box, text="Sagittal", variable=self.sagittal_var).grid(row=7, column=0, sticky="w")
        ttk.Label(layout_box, text="Axial coverage (mm, low to high)").grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        axial_start = ttk.Entry(layout_box, textvariable=self.axial_start_var, width=8)
        axial_start.grid(row=9, column=0, sticky="ew", padx=(0, 4))
        axial_start.bind("<KeyRelease>", self._axial_range_changed)
        axial_end = ttk.Entry(layout_box, textvariable=self.axial_end_var, width=8)
        axial_end.grid(row=9, column=1, sticky="ew", padx=(4, 0))
        axial_end.bind("<KeyRelease>", self._axial_range_changed)
        ttk.Label(layout_box, text="Contrast").grid(row=10, column=0, sticky="w", pady=(8, 0))
        ttk.Scale(layout_box, from_=0.5, to=2.5, variable=self.contrast_var, orient="horizontal", length=150, command=self._preview_contrast_changed).grid(row=11, column=0, columnspan=2, sticky="ew")
        layout_box.columnconfigure(0, weight=1)

        self.sequence_label = ttk.Label(preview, textvariable=self.sequence_var, font=("TkDefaultFont", 12, "bold"))
        self.sequence_label.pack(anchor="w")
        self.preview_canvas = tk.Canvas(preview, background="#111820", highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True, pady=(8, 0))
        self.preview_canvas.bind("<Configure>", lambda _event: self._draw_preview())

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.generate_button = ttk.Button(actions, text="4 · Create PDF film sheet…", command=self._generate_pdf, state="disabled")
        self.generate_button.pack(side="left")
        self.open_pdf_button = ttk.Button(actions, text="Open last PDF", command=self._open_last_pdf, state="disabled")
        self.open_pdf_button.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=150)
        self.progress.pack(side="right")
        ttk.Label(outer, textvariable=self.status_var, wraplength=940).pack(anchor="w", pady=(9, 0))

    def _choose_nifti(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose a 3D NIfTI image",
            filetypes=(("NIfTI images", "*.nii *.nii.gz"), ("All files", "*.*")),
            parent=self.root,
        )
        if selected:
            path = Path(selected)
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
        self._set_busy(True, "Reading image data and preparing orthogonal views…")
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
                name, payload = self.events.get_nowait()
                if name == "series":
                    self._series_ready(payload)
                elif name == "loaded":
                    self._volume_ready(payload)
                elif name == "pdf":
                    self.last_pdf = payload
                    self.open_pdf_button.configure(state="normal")
                    self._set_busy(False, f"Created PDF: {payload}")
                    messagebox.showinfo(APP_NAME, f"Film sheet saved to:\n{payload}", parent=self.root)
                else:
                    self._set_busy(False, "The operation could not be completed.")
                    messagebox.showerror(APP_NAME, str(payload), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

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
        self.axial_start_var.set(f"{axial_start:.0f}")
        self.axial_end_var.set(f"{axial_end:.0f}")
        self.sequence_var.set(f"{volume.sequence}  ·  {volume.data.shape[0]} × {volume.data.shape[1]} × {volume.data.shape[2]} voxels")
        if self.source_path and self.source_path.is_file():
            self.source_label.configure(text=str(self.source_path))
        self.generate_button.configure(state="normal")
        self._set_busy(False, "Image loaded. Review the layout settings, then create the PDF.")
        self._draw_preview()

    def _draw_preview(self) -> None:
        if self.volume is None:
            return
        width = max(1, self.preview_canvas.winfo_width())
        height = max(1, self.preview_canvas.winfo_height())
        try:
            start_mm = float(self.axial_start_var.get())
            end_mm = float(self.axial_end_var.get())
            if end_mm <= start_mm:
                raise ValueError("Invalid axial range")
            step = float(self.volume.affine[2, 2])
            if abs(step) < 1e-8:
                step = self.volume.voxel_sizes[2]
            center_mm = (start_mm + end_mm) / 2
            axial_index = round((center_mm - float(self.volume.affine[2, 3])) / step)
            low, high = self.axial_bounds or (0, self.volume.data.shape[2] - 1)
            axial_index = min(max(axial_index, low), high)
        except (ValueError, IndexError):
            axial_index = self.volume.data.shape[2] // 2
        plane = _extract_plane(self.volume.data, "axial", axial_index)
        image = _normalized_image(
            plane,
            float(self.contrast_var.get()),
            self.preview_range,
            self.volume.invert_grayscale,
        )
        max_width, max_height = max(1, width - 24), max(1, height - 24)
        aspect = _plane_aspect_ratio(plane, "axial", self.volume.voxel_sizes)
        if aspect >= max_width / max_height:
            preview_width, preview_height = max_width, max(1, round(max_width / aspect))
        else:
            preview_width, preview_height = max(1, round(max_height * aspect)), max_height
        image = image.resize((preview_width, preview_height), Image.Resampling.LANCZOS)
        self._preview_image = ImageTk.PhotoImage(image)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(width // 2, height // 2, image=self._preview_image, anchor="center")
        marker_color = "#b6c0cc"
        self.preview_canvas.create_text(width // 2, 7, text="A", fill=marker_color, anchor="n", font=("TkDefaultFont", 8))
        self.preview_canvas.create_text(width // 2, height - 7, text="P", fill=marker_color, anchor="s", font=("TkDefaultFont", 8))
        self.preview_canvas.create_text(7, height // 2, text="R", fill=marker_color, anchor="w", font=("TkDefaultFont", 8))
        self.preview_canvas.create_text(width - 7, height // 2, text="L", fill=marker_color, anchor="e", font=("TkDefaultFont", 8))

    def _preview_contrast_changed(self, _value: str = "") -> None:
        if self.volume is not None:
            self._draw_preview()

    def _axial_range_changed(self, _event: tk.Event | None = None) -> None:
        if self.volume is not None:
            self._draw_preview()

    def _generate_pdf(self) -> None:
        if self.volume is None:
            return
        planes = tuple(
            name for enabled, name in (
                (self.axial_var.get(), "axial"),
                (self.coronal_var.get(), "coronal"),
                (self.sagittal_var.get(), "sagittal"),
            ) if enabled
        )
        if not planes:
            messagebox.showerror(APP_NAME, "Select at least one view plane.", parent=self.root)
            return
        try:
            axial_range = None
            if "axial" in planes:
                axial_range = (float(self.axial_start_var.get()), float(self.axial_end_var.get()))
                if axial_range[1] <= axial_range[0]:
                    raise ValueError("Axial coverage end must be greater than its start.")
            options = FilmSheetOptions(
                slices_per_plane=int(self.slice_var.get()),
                columns=int(self.columns_var.get()),
                planes=planes,  # type: ignore[arg-type]
                contrast=float(self.contrast_var.get()),
                axial_range_mm=axial_range,
            )
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
        self.progress.start(12) if busy else self.progress.stop()

    def _set_widget_state(self, widget: tk.Widget, state: str) -> None:
        if isinstance(widget, (ttk.Button, ttk.Entry, ttk.Spinbox, ttk.Checkbutton)):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            self._set_widget_state(child, state)


def main() -> int:
    root = tk.Tk()
    FilmSheetWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

