from __future__ import annotations

import unittest
import queue
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from neuroflow import film_sheet_gui
from neuroflow.film_sheet import FilmSheetOptions, ImageVolume, _display_range, render_film_sheet_page_preview


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Canvas:
    def __init__(self):
        self.images = []

    def winfo_width(self):
        return 500

    def winfo_height(self):
        return 600

    def delete(self, *_args):
        pass

    def configure(self, **_kwargs):
        pass

    def create_image(self, _x, _y, *, image, **_kwargs):
        self.images.append(image)

    def create_text(self, *_args, **_kwargs):
        pass


class _Root:
    def __init__(self):
        self.callbacks = []

    def after(self, delay, callback):
        self.callbacks.append((delay, callback))


class _Button:
    def __init__(self):
        self.states = []

    def configure(self, **kwargs):
        self.states.append(kwargs)


class FilmSheetGuiTests(unittest.TestCase):
    def test_preview_canvas_receives_full_film_page(self) -> None:
        data = np.arange(8 * 9 * 10, dtype=np.float32).reshape((8, 9, 10))
        app = object.__new__(film_sheet_gui.FilmSheetWindow)
        app.volume = ImageVolume(data, np.eye(4), "synthetic")
        app.axial_bounds = (0, 9)
        app.preview_range = _display_range(data)
        app.axial_start_var = _Value("2")
        app.axial_end_var = _Value("7")
        app.axial_mode_var = _Value("Fit count to start/end")
        app.axial_gap_var = _Value("")
        app.slice_var = _Value(4)
        app.columns_var = _Value(2)
        app.contrast_var = _Value(1.0)
        app.axial_var = _Value(True)
        app.coronal_var = _Value(False)
        app.sagittal_var = _Value(False)
        app.preview_page_var = _Value("axial")
        app.patient_var = _Value("Synthetic Person")
        app.date_var = _Value("2026-10-21")
        app.preview_page_choice = _Button()
        app.preview_canvas = _Canvas()
        app._preview_after_id = None

        class Photo:
            def __init__(self, image):
                self.image = image

            def width(self):
                return self.image.width

            def height(self):
                return self.image.height

        with patch.object(film_sheet_gui.ImageTk, "PhotoImage", Photo):
            app._draw_preview()

        self.assertEqual(app.preview_canvas.images, [app._preview_image])
        self.assertEqual(app._preview_image.width(), 474)
        self.assertEqual(app._preview_image.height(), round(474 * 17 / 14))

    def test_pdf_page_preview_supports_selected_views_and_keeps_page_aspect(self) -> None:
        volume = ImageVolume(np.arange(8 * 9 * 10, dtype=np.float32).reshape((8, 9, 10)), np.eye(4), "synthetic")
        options = FilmSheetOptions(slices_per_plane=4, columns=2, planes=("axial", "coronal"))
        page = render_film_sheet_page_preview(volume, options, "coronal", width=420)
        self.assertEqual(page.size, (420, 510))

    def test_event_poll_is_rescheduled_after_a_handler_failure(self) -> None:
        app = object.__new__(film_sheet_gui.FilmSheetWindow)
        app.events = queue.Queue()
        app.events.put(("loaded", None))
        app.root = _Root()
        app.last_pdf = None
        app.open_pdf_button = _Button()
        app.statuses = []
        app._volume_ready = lambda _payload: (_ for _ in ()).throw(RuntimeError("preview failed"))
        app._set_busy = lambda busy, message: app.statuses.append((busy, message))

        with patch.object(film_sheet_gui.messagebox, "showerror"):
            app._poll_events()

        self.assertEqual(len(app.root.callbacks), 1)
        self.assertIn("preview failed", app.statuses[-1][1])

    def test_pdf_completion_releases_busy_state_without_modal_dialog(self) -> None:
        app = object.__new__(film_sheet_gui.FilmSheetWindow)
        app.events = queue.Queue()
        app.root = _Root()
        app.last_pdf = None
        app.open_pdf_button = _Button()
        app.statuses = []
        app._set_busy = lambda busy, message: app.statuses.append((busy, message))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "film.pdf"
            app.events.put(("pdf", output))
            with patch.object(film_sheet_gui.messagebox, "showinfo") as showinfo:
                app._poll_events()

        self.assertFalse(app.statuses[-1][0])
        self.assertIn("Created PDF", app.statuses[-1][1])
        showinfo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
