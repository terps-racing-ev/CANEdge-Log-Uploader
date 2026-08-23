import unittest

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


class RuleTests(unittest.TestCase):
    def test_rules_require_decoded_speed_signal(self):
        self.assertTrue(rules.requires_decoded_data())

    def test_static_suffix_when_speed_never_exceeds_one_mph(self):
        segments = rules.build_segments(Decoded([0, 0.5, 1.0]), context=None)
        self.assertEqual(segments[0].suffixes, ("STATIC",))

    def test_no_static_suffix_when_speed_exceeds_one_mph(self):
        segments = rules.build_segments(Decoded([0, 1.01, 0.5]), context=None)
        self.assertEqual(segments[0].suffixes, ())


if __name__ == "__main__":
    unittest.main()
