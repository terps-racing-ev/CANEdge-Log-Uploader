import unittest

from pathlib import Path

from canedge_uploader.gui import _device_login_details, _overall_percent
from canedge_uploader.models import ProgressEvent


class GuiHelperTests(unittest.TestCase):
    def test_parses_msal_device_message(self):
        message = "To sign in, use a web browser to open the page https://login.microsoft.com/device and enter the code ABC123XYZ to authenticate."
        self.assertEqual(_device_login_details(message), ("https://login.microsoft.com/device", "ABC123XYZ"))

    def test_total_progress_combines_files_and_current_stage(self):
        halfway_second = ProgressEvent(
            "decode", "Decoding", 50, 100, Path("two.mf4"), file_index=2, file_total=4
        )
        completed_second = ProgressEvent(
            "file_complete", "Finished", 2, 4, Path("two.mf4"), file_index=2, file_total=4
        )
        self.assertAlmostEqual(_overall_percent(halfway_second), 37.25)
        self.assertEqual(_overall_percent(completed_second), 50.0)
