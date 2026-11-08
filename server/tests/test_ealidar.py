"""England's EA LiDAR DSM reader (ealidar.py): blocks, bilinear sampling, and measured building heights."""
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import buildings  # noqa: E402
import ealidar  # noqa: E402
from test_roads import FRAME  # noqa: E402


class SampleTest(unittest.TestCase):
    def test_england_envelope(self):
        self.assertTrue(ealidar.covers(51.5033, -0.1196))
        self.assertFalse(ealidar.covers(48.8566, 2.3522))        # Paris
        self.assertFalse(ealidar.covers(40.0, -3.0))

    def test_bilinear_sample_of_a_block(self):
        with tempfile.TemporaryDirectory() as d:
            p = ealidar.EaLidar(d, None, block_px=4)
            lat, lon = 51.5033, -0.1196
            x, y = ealidar._merc(lat, lon)
            bx, by = p._key(x, y)
            z = np.arange(16, dtype=np.float64).reshape(4, 4) + 10.0
            p._blocks[(bx, by)] = ealidar.Block(bx, by, z)
            got = p.surface(lat, lon)
            self.assertIsNotNone(got)
            self.assertGreater(got, 10.0)
            self.assertLess(got, 26.0)
            # the block's own corners: the sample stays inside its grid
            la, lo = ealidar._unmerc(bx + 1.0, by + 1.0)
            self.assertGreaterEqual(p.surface(la, lo), 10.0)

    def test_off_england_needs_no_block(self):
        p = ealidar.EaLidar(tempfile.gettempdir(), None)
        self.assertIsNone(p.surface(48.8566, 2.3522))


class HeightTest(unittest.TestCase):
    def ring(self, half=10.0):
        return np.array([(-half, -half), (half, -half), (half, half), (-half, half)], np.float64)

    def test_measured_height_when_overture_has_none(self):
        with mock.patch.object(ealidar, "available", return_value=True), \
                mock.patch.object(ealidar, "surface", side_effect=lambda la, lo: 12.0):
            at = buildings._ea_height({"class": "house"}, self.ring(), np.zeros(4), FRAME, 0.0, 0.0)
        self.assertAlmostEqual(at["height"], 12.0, places=3)
        self.assertEqual(at["num_floors"], 4)

    def test_a_mapped_height_is_kept(self):
        with mock.patch.object(ealidar, "available", return_value=True), \
                mock.patch.object(ealidar, "surface", side_effect=lambda la, lo: 30.0):
            at = buildings._ea_height({"height": 7.5}, self.ring(), np.zeros(4), FRAME, 0.0, 0.0)
        self.assertEqual(at["height"], 7.5)

    def test_a_roof_below_the_ground_is_no_height(self):
        with mock.patch.object(ealidar, "available", return_value=True), \
                mock.patch.object(ealidar, "surface", side_effect=lambda la, lo: 1.0):
            at = buildings._ea_height({}, self.ring(), np.zeros(4), FRAME, 0.0, 0.0)
        self.assertNotIn("height", at)


if __name__ == "__main__":
    unittest.main()
