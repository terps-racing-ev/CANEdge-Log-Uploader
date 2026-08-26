from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from canedge_uploader.files import discover_mf4_files, eastern_time, infer_device_id, mf4_inputs


class FileTests(unittest.TestCase):
    def test_discovery_is_recursive_and_case_insensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            nested = tmp_path / "Logs" / "123"
            nested.mkdir(parents=True)
            (nested / "one.MF4").write_bytes(b"one")
            (nested / "two.mf4").write_bytes(b"two")
            (nested / "no.txt").write_bytes(b"no")
            self.assertEqual(len(discover_mf4_files(tmp_path)), 2)

    def test_utc_to_eastern_is_dst_aware(self):
        summer = eastern_time(datetime(2026, 8, 4, 3, tzinfo=timezone.utc), "America/New_York")
        winter = eastern_time(datetime(2026, 1, 4, 3, tzinfo=timezone.utc), "America/New_York")
        self.assertEqual(summer.utcoffset().total_seconds(), -4 * 3600)
        self.assertEqual(winter.utcoffset().total_seconds(), -5 * 3600)

    def test_device_id_comes_from_session_directory(self):
        root = Path("C:/data")
        source = root / "Logs" / "00000489" / "00000001.MF4"
        self.assertEqual(infer_device_id(source, root), "00000489")

    def test_mf4_inputs_accept_files_and_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            folder = tmp_path / "folder"
            folder.mkdir()
            first = folder / "one.MF4"
            second = tmp_path / "two.mf4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")

            self.assertEqual(mf4_inputs([folder, second]), sorted([first.resolve(), second.resolve()]))
