from datetime import datetime
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from canedge_uploader.models import RecordingContext, Segment
from canedge_uploader.naming import duration_suffix, make_artifact_plans, make_raw_artifact_plan


def context(duration_seconds=0):
    return RecordingContext(
        source_path=Path("Logs/00000489/00000001.MF4"),
        source_root=Path("Logs"),
        source_digest="a" * 64,
        dbc_digest="b" * 64,
        start_time=datetime(2026, 8, 3, 23, 59, 58, tzinfo=ZoneInfo("America/New_York")),
        device_id="00000489",
        duration_seconds=duration_seconds,
    )


class NamingTests(unittest.TestCase):
    def test_name_is_deterministic_and_designators_are_sanitized(self):
        first = make_artifact_plans(context(duration_seconds=222 * 60), [Segment(designators=("STATIC", "Pit / Run"))])
        second = make_artifact_plans(context(duration_seconds=222 * 60), [Segment(designators=("STATIC", "Pit / Run"))])
        self.assertEqual(first, second)
        self.assertEqual(first[0].filename, "STATIC_Pit-Run_2026-08-03_23-59-58_3h42m.mf4")

    def test_split_crossing_midnight_uses_each_segment_calendar_day(self):
        plans = make_artifact_plans(context(duration_seconds=4), [Segment(start=0, stop=3), Segment(start=3)])
        self.assertEqual([plan.calendar_date for plan in plans], ["2026-08-03", "2026-08-04"])
        self.assertEqual(plans[1].filename, "part-02_2026-08-04_00-00-01_1s.mf4")

    def test_invalid_segment_rejected(self):
        with self.assertRaises(ValueError):
            make_artifact_plans(context(), [Segment(start=5, stop=5)])

    def test_raw_artifact_name_uses_recording_start_with_suffix(self):
        plan = make_raw_artifact_plan(context(duration_seconds=222 * 60))
        self.assertEqual(plan.calendar_date, "2026-08-03")
        self.assertEqual(plan.filename, "2026-08-03_23-59-58_3h42m_RAW.mf4")

    def test_segment_suffixes_are_appended_after_duration(self):
        plan = make_artifact_plans(context(duration_seconds=222 * 60), [Segment(suffixes=("STATIC",))])[0]
        self.assertEqual(plan.filename, "2026-08-03_23-59-58_3h42m_STATIC.mf4")

    def test_duration_suffix_formats_long_hours_minutes_and_seconds(self):
        self.assertEqual(duration_suffix((234 * 60 + 23) * 60), "234h23m")
        self.assertEqual(duration_suffix(24), "24s")
        self.assertEqual(duration_suffix(59.9), "59s")
        self.assertEqual(duration_suffix(60), "1m")
