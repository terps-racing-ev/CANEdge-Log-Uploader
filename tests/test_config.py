from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from canedge_uploader.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_decode_can_bus_defaults_to_all_buses(self):
        with patch.dict("os.environ", {}, clear=True):
            with tempfile.TemporaryDirectory() as directory:
                dbc_dir = Path(directory) / "dbc"
                dbc_dir.mkdir()
                settings = load_settings(env_file=Path(directory) / "missing.env", dbc_dir=dbc_dir)

        self.assertEqual(settings.decode_can_bus, 0)

    def test_decode_can_bus_can_be_configured(self):
        with patch.dict("os.environ", {}, clear=True):
            with tempfile.TemporaryDirectory() as directory:
                tmp_path = Path(directory)
                dbc_dir = tmp_path / "dbc"
                dbc_dir.mkdir()
                env_file = tmp_path / "env"
                env_file.write_text("DECODE_CAN_BUS=2\n", encoding="utf-8")

                settings = load_settings(env_file=env_file, dbc_dir=dbc_dir)

        self.assertEqual(settings.decode_can_bus, 2)
