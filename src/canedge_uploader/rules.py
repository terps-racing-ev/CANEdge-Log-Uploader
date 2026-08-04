"""Project-specific filename and splitting logic.

This is intentionally the only file most signal-logic changes need to touch.
See docs/ADDING_RULES.md for copy/paste examples.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import RecordingContext, Segment

if TYPE_CHECKING:
    from asammdf import MDF


def requires_decoded_data() -> bool:
    """Return True when ``build_segments`` reads decoded signals."""
    return False


def build_segments(decoded: "MDF | None", context: RecordingContext) -> list[Segment]:
    """Return every output segment and its filename designators.

    The default keeps the complete recording and adds no designator. Designators
    are normalized for filenames and may be combined, e.g. ("STATIC", "CHARGING").
    """
    return [Segment()]


def signal_samples(decoded: "MDF", signal_name: str) -> tuple[Any, Any]:
    """Convenience helper returning (timestamps, samples) for a decoded signal."""
    signal = decoded.get(signal_name)
    return signal.timestamps, signal.samples

