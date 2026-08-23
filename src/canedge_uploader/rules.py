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
    return True


def build_segments(decoded: "MDF | None", context: RecordingContext) -> list[Segment]:
    """Return every output segment and its filename designators.

    The default keeps the complete recording and adds no designator. Designators
    are normalized for filenames and may be combined, e.g. ("STATIC", "CHARGING").
    """
    if decoded is None:
        raise ValueError("Decoded data is required to apply VCU_Speed_MPH rules")

    _timestamps, speed_samples = signal_samples(decoded, "VCU_Speed_MPH")
    suffixes = () if any(float(speed) > 1.0 for speed in speed_samples) else ("STATIC",)
    return [Segment(suffixes=suffixes)]


def signal_samples(decoded: "MDF", signal_name: str) -> tuple[Any, Any]:
    """Convenience helper returning (timestamps, samples) for a decoded signal."""
    signal = decoded.get(signal_name)
    return signal.timestamps, signal.samples

