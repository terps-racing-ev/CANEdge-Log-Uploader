from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from canedge_uploader.config import Settings
from canedge_uploader.models import RecordingContext, RunSummary, UploadPreview, UploadPreviewItem
from canedge_uploader.pipeline import CancelledError, Processor, clean_can_source_names


def settings() -> Settings:
    return Settings("", "", "", "", "", Path("dbc"))


class FakeDestination:
    root_url = ""

    def __init__(self, raw_by_day=None):
        self.raw_by_day = raw_by_day or {}
        self.raw_name_checks = []

    def prepare(self):
        pass

    def list_date_folders(self):
        return sorted(self.raw_by_day, reverse=True)

    def raw_items_in_date(self, day):
        return [{"name": name} for name in self.raw_by_day.get(day, [])]

    def names_in_date(self, calendar_date, subfolder=None):
        self.raw_name_checks.append((calendar_date, subfolder))
        return set()


class PipelineTests(unittest.TestCase):
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
        events = []

        class PreviewProcessor(Processor):
            def prepare_destination(self):
                self._emit("sharepoint", "connected")

            def _inspect_context(self, source, source_root):
                return RecordingContext(
                    source_path=source,
                    source_root=source_root,
                    source_digest="",
                    dbc_digest="",
                    start_time=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
                    device_id="device",
                    duration_seconds=12,
                )

        processor = PreviewProcessor(settings(), destination=FakeDestination(), progress=events.append)
        processor.preview_upload_from_sd(Path(__file__).parent / "fixtures")

        stages = [event.stage for event in events]
        self.assertIn("preview", stages)
        self.assertIn("check", stages)
        self.assertIn("file_complete", stages)

    def test_preview_upload_honors_cancel_before_inspecting_files(self):
        from threading import Event

        cancel = Event()
        cancel.set()

        class PreviewProcessor(Processor):
            def prepare_destination(self):
                pass

        processor = PreviewProcessor(settings(), destination=FakeDestination(), cancel=cancel)

        with self.assertRaises(CancelledError):
            processor.preview_upload_from_sd(Path(__file__).parent / "fixtures")

    def test_preview_upload_uses_latest_sharepoint_raw_log_as_watermark(self):
        inspected = []

        class PreviewProcessor(Processor):
            def _filesystem_time(self, source):
                hour = 11 if source.stem.endswith("1") else 13
                return datetime(2026, 9, 7, hour, 0, tzinfo=ZoneInfo("America/New_York"))

            def _inspect_context(self, source, source_root):
                inspected.append(source.name)
                start = datetime(2026, 9, 7, 13, 0, tzinfo=ZoneInfo("America/New_York"))
                return RecordingContext(
                    source_path=source,
                    source_root=source_root,
                    source_digest="",
                    dbc_digest="",
                    start_time=start,
                    device_id="device",
                    duration_seconds=12,
                )

        destination = FakeDestination(
            {
                "2026-09-07": ["2026-09-07_12-00-00_12s_RAW.mf4"],
                "2026-09-06": ["2026-09-06_18-00-00_12s_RAW.mf4"],
            }
        )
        processor = PreviewProcessor(settings(), destination=destination)

        preview = processor.preview_upload_from_sd(Path(__file__).parent / "fixtures")

        self.assertEqual([item.raw_exists for item in preview.items], [True, False])
        self.assertEqual(inspected, ["00000002.MF4"])
        self.assertEqual(destination.raw_name_checks, [])

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


if __name__ == "__main__":
    unittest.main()
