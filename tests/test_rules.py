import unittest
from types import SimpleNamespace

from canedge_uploader import rules


class Signal:
    def __init__(self, samples):
        self.timestamps = list(range(len(samples)))
        self.samples = samples


class Decoded:
    def __init__(self, samples):
        self.signal = Signal(samples)

    def get(self, signal_name):
        if signal_name != "VCU_Speed_MPH":
            raise KeyError(signal_name)
        return self.signal


class MultiBusDecoded:
    def __init__(self):
        self.signals = {
            (0, 1): Signal([2]),
            (1, 1): Signal([0]),
        }
        self.groups = [
            SimpleNamespace(
                channels=[
                    None,
                    SimpleNamespace(source=SimpleNamespace(comment='<e name="ChannelNo" type="integer">2</e>')),
                ]
            ),
            SimpleNamespace(
                channels=[
                    None,
                    SimpleNamespace(source=SimpleNamespace(comment='<e name="ChannelNo" type="integer">1</e>')),
                ]
            ),
        ]

    def whereis(self, signal_name):
        if signal_name != "VCU_Speed_MPH":
            return ()
        return ((0, 1), (1, 1))

    def get(self, signal_name, group=None, index=None):
        if signal_name != "VCU_Speed_MPH":
            raise KeyError(signal_name)
        return self.signals[(group, index)]


class RuleTests(unittest.TestCase):
    def test_rules_require_decoded_speed_signal(self):
        self.assertTrue(rules.requires_decoded_data())

    def test_static_suffix_when_speed_never_exceeds_one_mph(self):
        segments = rules.build_segments(Decoded([0, 0.5, 1.0]), context=None)
        self.assertEqual(segments[0].suffixes, ("STATIC",))

    def test_no_static_suffix_when_speed_exceeds_one_mph(self):
        segments = rules.build_segments(Decoded([0, 1.01, 0.5]), context=None)
        self.assertEqual(segments[0].suffixes, ())

    def test_multiple_occurrences_use_lower_bus(self):
        segments = rules.build_segments(MultiBusDecoded(), context=None)
        self.assertEqual(segments[0].suffixes, ("STATIC",))


if __name__ == "__main__":
    unittest.main()
