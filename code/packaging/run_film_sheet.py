import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    # Keep this source launcher runnable from the checkout as well as from
    # PyInstaller; Python otherwise places only the packaging/ directory on
    # sys.path when this file is executed directly.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuroflow.film_sheet_gui import main


if __name__ == "__main__":
    raise SystemExit(main())
