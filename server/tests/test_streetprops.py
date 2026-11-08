"""Street props (streetprops.py): the mapped traffic-calming kinds that get a hump model and the model width chosen
for the carriageway."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import roads  # noqa: E402
import streetprops  # noqa: E402


class Fake:
    def __init__(self, tags=None):
        self.attrs = {} if tags is None else {"tags": [tags]}


class HumpTest(unittest.TestCase):
    def test_only_humps_and_bumps(self):
        self.assertTrue(streetprops._is_hump(Fake('{"traffic_calming": "hump"}'), 0))
        self.assertTrue(streetprops._is_hump(Fake('{"traffic_calming": "bump"}'), 0))
        self.assertFalse(streetprops._is_hump(Fake('{"traffic_calming": "table"}'), 0))
        self.assertFalse(streetprops._is_hump(Fake('{"traffic_calming": "chicane"}'), 0))
        self.assertFalse(streetprops._is_hump(Fake('{"traffic_calming": "cushion"}'), 0))
        self.assertTrue(streetprops._is_hump(Fake(), 0), "the layer's own traffic_calming class, no tag detail")

    def test_model_width_follows_the_carriageway(self):
        self.assertEqual(streetprops._hump_group(7.0), "speedhump")
        self.assertEqual(streetprops._hump_group(6.4), "speedhump")
        self.assertEqual(streetprops._hump_group(6.0), "speedhump6")
        self.assertEqual(streetprops._hump_group(5.1), "speedhump6")
        self.assertEqual(streetprops._hump_group(4.8), "speedhump5")
        self.assertEqual(streetprops.HEIGHTS[streetprops.HUMP], 0.075, "a UK hump is 75 mm high")

    def test_road_place_returns_its_width_and_centre(self):
        sp = roads.Spec(6.5, "paved", True, "residential", False, 2.0)
        pts = np.array([[-20.0, 0.0, 4.0], [20.0, 0.0, 4.0]])
        yaw, width, x, y, z, dist = streetprops._road_place(np.array([3.0, 1.0]), [("r", sp, pts, 0.0)])
        self.assertAlmostEqual(width, 6.5)
        self.assertAlmostEqual(yaw, 0.0)
        self.assertAlmostEqual(abs(y), 0.0, msg="snapped to the centreline")
        self.assertAlmostEqual(x, 3.0)
        self.assertAlmostEqual(z, 4.0)
        self.assertAlmostEqual(dist, 1.0)


if __name__ == "__main__":
    unittest.main()
