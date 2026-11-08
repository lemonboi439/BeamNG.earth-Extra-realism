"""TfL red-route kerb lines (kerbline.py): control geometry near a road turns its kerb lines red."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import geodesy  # noqa: E402
import kerbline  # noqa: E402


class RedRouteTest(unittest.TestCase):
    def test_a_controlled_road_is_red(self):
        idx = kerbline._get()
        self.assertIsNotNone(idx, "the TfL control index is shipped")
        k = len(idx.x) // 2
        lon = float(idx.x[k]) / (111320.0 * kerbline.COS) + kerbline.LON0
        lat = float(idx.y[k]) / 111320.0 + kerbline.LAT0
        frame = geodesy.EnuFrame(lat, lon, 0.0)
        pts = np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.0]])
        self.assertIn(kerbline.red_style(frame, pts), ("single_red", "double_red"))

    def test_uncontrolled_roads_are_not_red(self):
        frame = geodesy.EnuFrame(48.8566, 2.3522, 0.0)      # Paris
        self.assertIsNone(kerbline.red_style(frame, np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.0]])))


if __name__ == "__main__":
    unittest.main()
