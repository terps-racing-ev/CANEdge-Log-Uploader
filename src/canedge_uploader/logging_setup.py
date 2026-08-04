from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def log_directory() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "CANedgeUploader" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def configure_logging(verbose: bool = False) -> Path:
    log_file = log_directory() / "canedge-uploader.log"
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    return log_file

