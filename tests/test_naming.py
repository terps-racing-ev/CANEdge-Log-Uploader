from datetime import datetime
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from canedge_uploader.models import RecordingContext, Segment
from canedge_uploader.naming import make_artifact_plans


def context():
    return RecordingContext(
        source_path=Path("Logs/00000489/00000001.MF4"),
        source_root=Path("Logs"),
        source_digest="a" * 64,
        dbc_digest="b" * 64,
        start_time=datetime(2026, 8, 3, 23, 59, 58, tzinfo=ZoneInfo("America/New_York")),
        device_id="00000489",
    )


class NamingTests(unittest.TestCase):
    def test_name_is_deterministic_and_designators_are_sanitized(self):
        first = make_artifact_plans(context(), [Segment(designators=("STATIC", "Pit / Run"))])
        second = make_artifact_plans(context(), [Segment(designators=("STATIC", "Pit / Run"))])
        self.assertEqual(first, second)
        self.assertEqual(first[0].filename, "STATIC_Pit-Run_2026-08-03_23-59-58.mf4")

    def test_split_crossing_midnight_uses_each_segment_calendar_day(self):
        plans = make_artifact_plans(context(), [Segment(start=0, stop=3), Segment(start=3)])
        self.assertEqual([plan.calendar_date for plan in plans], ["2026-08-03", "2026-08-04"])
        self.assertEqual(plans[1].filename, "part-02_2026-08-04_00-00-01.mf4")

    def test_invalid_segment_rejected(self):
        with self.assertRaises(ValueError):
            make_artifact_plans(context(), [Segment(start=5, stop=5)])
