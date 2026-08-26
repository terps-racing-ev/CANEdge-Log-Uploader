from pathlib import Path
from types import SimpleNamespace
import unittest

from canedge_uploader.config import Settings
from canedge_uploader.pipeline import Processor, clean_can_source_names


def settings() -> Settings:
    return Settings("", "", "", "", "", Path("dbc"))


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


if __name__ == "__main__":
    unittest.main()
