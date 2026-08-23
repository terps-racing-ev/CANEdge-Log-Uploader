from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class RecordingContext:
    source_path: Path
    source_root: Path
    source_digest: str
    dbc_digest: str
    start_time: datetime
    device_id: str
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class Segment:
    """One desired output. Times are seconds relative to the recording start."""

    start: float | None = None
    stop: float | None = None
    designators: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactPlan:
    segment: Segment
    filename: str
    calendar_date: str
    start_time: datetime


@dataclass
class RunSummary:
    discovered: int = 0
    decoded: int = 0
    uploaded: int = 0
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    uploaded_urls: list[str] = field(default_factory=list)
    root_url: str = ""


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str
    current: int = 0
    total: int = 0
    file: Path | None = None
    file_index: int = 0
    file_total: int = 0


ProgressCallback = Callable[[ProgressEvent], None]
