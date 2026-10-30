from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from zoneinfo import ZoneInfo

from canedge_uploader.config import Settings
from canedge_uploader.models import RecordingContext, RunSummary, UploadPreview, UploadPreviewItem
from canedge_uploader.pipeline import CancelledError, Processor, clean_can_source_names

DEVICE = "9B3425A8"


def settings() -> Settings:
    return Settings("", "", "", "", "", Path(__file__).resolve().parent.parent / "dbc")


def make_card(root: Path, *logs: tuple[int, int]) -> None:
    for session, split in logs:
        path = root / "LOG" / DEVICE / f"{session:08d}" / f"{split:08d}.MF4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def session_start(source: Path) -> datetime:
    return datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("America/New_York")) + timedelta(hours=int(source.parent.name))


class FakeDestination:
    root_url = ""

    def __init__(self, raw_by_day=None, state=None):
        self.raw_by_day = raw_by_day or {}
        self.state = state or {}
        self.state_writes = []
        self.raw_name_checks = []
        self.listed_date_folders = False

    def prepare(self):
        pass

    def read_upload_state(self):
        return deepcopy(self.state)

    def write_upload_state(self, state):
        self.state = deepcopy(state)
        self.state_writes.append(deepcopy(state))

    def list_date_folders(self):
        self.listed_date_folders = True
        return sorted(self.raw_by_day, reverse=True)

    def raw_items_in_date(self, day):
        return [{"name": name} for name in self.raw_by_day.get(day, [])]

    def names_in_date(self, calendar_date, subfolder=None):
        self.raw_name_checks.append((calendar_date, subfolder))
        return set()


class SessionClockProcessor(Processor):
    """Derives each log's start time from its session number instead of reading MF4 headers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inspected = []

    def _filesystem_time(self, source):
        return session_start(source)

    def _inspect_context(self, source, source_root):
        self.inspected.append(source.parent.name)
        return RecordingContext(
            source_path=source,
            source_root=source_root,
            source_digest="",
            dbc_digest="",
            start_time=session_start(source),
            device_id="device",
            duration_seconds=12,
        )


def record(session, split=1):
    return {"devices": {DEVICE: {"session": session, "split": split}}}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.card = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_common_root_uses_parent_for_single_file(self):
        processor = Processor(settings())

        self.assertEqual(processor._common_root([Path("C:/logs/one.MF4")]), Path("C:/logs"))

    def test_clean_can_source_names_uses_message_name_from_comment(self):
        source = SimpleNamespace(
            name="CAN1 message ID=0x67 EXT=False",
            path="CAN1.CAN_DataFrame.ID=0x67 EXT=False",
            comment="<SIcomment><TX>CAN1 data frame 0x67 EXT=False - GnssPos</TX></SIcomment>",
        )
        mdf = SimpleNamespace(groups=[SimpleNamespace(channels=[SimpleNamespace(source=source)])])

        clean_can_source_names(mdf)

        self.assertEqual(source.name, "GnssPos")
        self.assertEqual(source.path, "GnssPos")

    def test_clean_can_source_names_ignores_sources_without_message_name(self):
        source = SimpleNamespace(name="original", path="original.path", comment="<SIcomment />")
        mdf = SimpleNamespace(groups=[SimpleNamespace(channels=[SimpleNamespace(source=source)])])

        clean_can_source_names(mdf)

        self.assertEqual(source.name, "original")
        self.assertEqual(source.path, "original.path")

    def test_preview_upload_reports_per_file_progress(self):
        make_card(self.card, (1, 1), (2, 1))
        events = []
        processor = SessionClockProcessor(settings(), destination=FakeDestination(), progress=events.append)

        processor.preview_upload_from_sd(self.card)

        stages = [event.stage for event in events]
        self.assertIn("preview", stages)
        self.assertIn("check", stages)
        self.assertIn("file_complete", stages)

    def test_preview_upload_honors_cancel_before_inspecting_files(self):
        from threading import Event

        make_card(self.card, (1, 1))
        cancel = Event()
        cancel.set()
        processor = SessionClockProcessor(settings(), destination=FakeDestination(), cancel=cancel)

        with self.assertRaises(CancelledError):
            processor.preview_upload_from_sd(self.card)
        self.assertEqual(processor.inspected, [])

    def test_preview_skips_logs_at_or_before_upload_record_without_reading_them(self):
        make_card(self.card, (1, 1), (2, 1), (2, 2), (3, 1))
        destination = FakeDestination(state=record(2, 1))
        processor = SessionClockProcessor(settings(), destination=destination)

        preview = processor.preview_upload_from_sd(self.card)

        self.assertEqual([item.raw_exists for item in preview.items], [True, True, False, False])
        self.assertEqual([(item.session, item.split) for item in preview.upload_items], [(2, 2), (3, 1)])
        self.assertEqual(processor.inspected, ["00000002", "00000003"])
        self.assertFalse(destination.listed_date_folders)
        self.assertEqual(preview.notes, [f"{DEVICE}: 2 skipped (session 2 split 1 and earlier already uploaded)."])

    def test_preview_without_upload_record_falls_back_to_latest_sharepoint_raw(self):
        make_card(self.card, (1, 1), (2, 1))
        destination = FakeDestination(
            {
                "2026-09-07": ["2026-09-07_13-00-00_12s_RAW.mf4"],
                "2026-09-06": ["2026-09-06_18-00-00_12s_RAW.mf4"],
            }
        )
        processor = SessionClockProcessor(settings(), destination=destination)

        preview = processor.preview_upload_from_sd(self.card)

        self.assertEqual([item.raw_exists for item in preview.items], [True, False])
        self.assertEqual(processor.inspected, ["00000002"])
        self.assertEqual(destination.raw_name_checks, [])
        self.assertIn("no upload record", preview.notes[0])
        self.assertEqual(preview.notes[1], "1 skipped (at or before latest SharePoint raw: 2026-09-07_13-00-00_12s_RAW.mf4).")

    def test_preview_falls_back_when_card_is_behind_upload_record(self):
        make_card(self.card, (1, 1), (2, 1))
        destination = FakeDestination(state=record(50))
        processor = SessionClockProcessor(settings(), destination=destination)

        preview = processor.preview_upload_from_sd(self.card)

        self.assertEqual(preview.upload_count, 2)
        self.assertTrue(destination.listed_date_folders)
        self.assertIn("may have been reformatted", preview.notes[0])

    def test_upload_from_preview_processes_only_files_not_marked_uploaded(self):
        processed = []

        class UploadProcessor(Processor):
            def _process_files(self, files, source_root, work_dir, summary, **kwargs):
                processed.extend(files)

            def _complete(self, summary):
                return summary

        first = Path("C:/logs/old.MF4")
        second = Path("C:/logs/new.MF4")
        preview = UploadPreview(
            Path("C:/logs"),
            [
                UploadPreviewItem(first, "2026-09-06", "2026-09-06_18-00-00_12s_RAW.mf4", raw_exists=True),
                UploadPreviewItem(second, "2026-09-07", "2026-09-07_13-00-00_12s_RAW.mf4", raw_exists=False),
            ],
            decoded_filenames_known=True,
        )
        processor = UploadProcessor(settings(), destination=FakeDestination())

        summary = processor.upload_from_sd(Path("C:/logs"), preview)

        self.assertEqual(processed, [second])
        self.assertEqual(summary, RunSummary(discovered=1))

    def test_upload_record_stops_advancing_at_first_failed_log(self):
        make_card(self.card, (3, 1), (4, 1), (5, 1))

        class FailingProcessor(SessionClockProcessor):
            def _process_one(self, source, *args, **kwargs):
                if source.parent.name == "00000004":
                    raise RuntimeError("upload failed")

        destination = FakeDestination(state=record(2))
        processor = FailingProcessor(settings(), destination=destination)
        preview = processor.preview_upload_from_sd(self.card)

        summary = processor.upload_from_sd(self.card, preview)

        self.assertEqual(summary.failed, 1)
        self.assertEqual(len(destination.state_writes), 1)
        self.assertEqual(destination.state["devices"][DEVICE]["session"], 3)
        self.assertEqual(destination.state["devices"][DEVICE]["raw"], "2026-09-07_15-00-00_12s_RAW.mf4")

    def test_upload_record_never_moves_backwards(self):
        item = UploadPreviewItem(Path("x.MF4"), "", "", device_id=DEVICE, session=5, split=1)
        destination = FakeDestination(state=record(9))
        processor = Processor(settings(), destination=destination)

        processor._upload_state_recorder([item])(item.source_path, True)

        self.assertEqual(destination.state_writes, [])


if __name__ == "__main__":
    unittest.main()
