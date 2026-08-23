from __future__ import annotations

import re
from datetime import timedelta

from .models import ArtifactPlan, RecordingContext, Segment


_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def safe_component(value: str, fallback: str = "UNKNOWN") -> str:
    cleaned = _UNSAFE.sub("-", value.strip()).strip("-_")
    return cleaned or fallback


def duration_suffix(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    if whole_seconds < 60:
        return f"{whole_seconds}s"
    total_minutes = whole_seconds // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def make_artifact_plans(context: RecordingContext, segments: list[Segment]) -> list[ArtifactPlan]:
    if not segments:
        raise ValueError("Rule logic returned no output segments")

    plans: list[ArtifactPlan] = []
    for index, segment in enumerate(segments, start=1):
        offset = float(segment.start or 0.0)
        if offset < 0 or (segment.stop is not None and segment.stop <= offset):
            raise ValueError(f"Invalid segment bounds: start={segment.start}, stop={segment.stop}")
        start = context.start_time + timedelta(seconds=offset)
        parts = [safe_component(item) for item in segment.designators if item.strip()]
        if len(segments) > 1:
            parts.append(f"part-{index:02d}")
        parts.append(start.strftime("%Y-%m-%d_%H-%M-%S"))
        stop = context.duration_seconds if segment.stop is None else segment.stop
        parts.append(duration_suffix(stop - offset))
        parts.extend(safe_component(item) for item in segment.suffixes if item.strip())
        filename = "_".join(parts) + ".mf4"
        plans.append(ArtifactPlan(segment, filename, start.date().isoformat(), start))
    return plans


def make_raw_artifact_plan(context: RecordingContext) -> ArtifactPlan:
    start = context.start_time
    filename = f"{start.strftime('%Y-%m-%d_%H-%M-%S')}_{duration_suffix(context.duration_seconds)}_RAW.mf4"
    return ArtifactPlan(Segment(), filename, start.date().isoformat(), start)
