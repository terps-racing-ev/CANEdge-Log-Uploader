"""Project-specific filename and splitting logic.

This is intentionally the only file most signal-logic changes need to touch.
See docs/ADDING_RULES.md for copy/paste examples.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .models import RecordingContext, Segment

if TYPE_CHECKING:
    from asammdf import MDF

CHANNEL_NO_RE = re.compile(r'name="ChannelNo"[^>]*>\s*(\d+)\s*<')
CAN_BUS_RE = re.compile(r"\bCAN(\d+)\b", re.IGNORECASE)


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
    occurrences = decoded.whereis(signal_name) if hasattr(decoded, "whereis") else ()
    if occurrences:
        group, index = min(
            occurrences,
            key=lambda occurrence: (_bus_for_occurrence(decoded, *occurrence), occurrence),
        )
        signal = decoded.get(signal_name, group=group, index=index)
    else:
        signal = decoded.get(signal_name)
    return signal.timestamps, signal.samples


def _bus_for_occurrence(decoded: "MDF", group_index: int, channel_index: int) -> int:
    group = getattr(decoded, "groups", [])[group_index]
    channel = group.channels[channel_index]
    source = getattr(channel, "source", None)
    candidates = [
        getattr(source, "comment", ""),
        getattr(source, "path", ""),
        getattr(source, "name", ""),
        getattr(getattr(group, "channel_group", None), "acq_name", ""),
    ]

    for text in candidates:
        bus = _parse_bus_number(str(text or ""))
        if bus is not None:
            return bus

    return 2**31 - 1


def _parse_bus_number(text: str) -> int | None:
    channel_match = CHANNEL_NO_RE.search(text)
    if channel_match:
        return int(channel_match.group(1))

    can_match = CAN_BUS_RE.search(text)
    if can_match:
        return int(can_match.group(1))

    return None

