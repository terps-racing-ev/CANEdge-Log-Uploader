from pathlib import Path
import unittest

from canedge_uploader.config import Settings
from canedge_uploader.pipeline import Processor


def settings() -> Settings:
    return Settings("", "", "", "", "", Path("dbc"))


class PipelineTests(unittest.TestCase):
    def test_common_root_uses_parent_for_single_file(self):
        processor = Processor(settings())

        self.assertEqual(processor._common_root([Path("C:/logs/one.MF4")]), Path("C:/logs"))


if __name__ == "__main__":
    unittest.main()
