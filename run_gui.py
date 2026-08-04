"""Convenient launcher for users who do not want to use the CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from canedge_uploader.gui import main  # noqa: E402

main()

