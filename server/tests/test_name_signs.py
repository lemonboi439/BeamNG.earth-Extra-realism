"""Street-name plates (namesigns.py, signstream's names field)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import geodesy
import namesigns
import signroads
import signstream


class FakeContext:
    def __init__(self, rows):
        self.rows = rows

    def pavements(self, frame, points, left_hand=False):
        return self.rows


class NameSignTest(unittest.TestCase):
    def setUp(self):
        self.frame = geodesy.EnuFrame(51.5074, -0.1278)
        self.line = np.c_[np.linspace(40.0, 220.0, 20), np.full(20, 100.0), np.zeros(20)]

    def row(self, name, line=None, width=6.0):
        return (1, signroads.Spec(width, "residential", False, None, False, name), self.line if line is None else line,
                width)

    def build(self, rows, country="GB"):
        return namesigns.build_tile(self.frame, 0, 0, lambda la, lo: np.full(np.shape(la), 12.0),
                                    FakeContext(rows), True, country)

    def test_one_plate_per_street_name(self):
        mesh = self.build([self.row("Test Street"), self.row("Test Street"), self.row("Other Road")])
        self.assertIsNotNone(mesh)
        P, T, UV, mats, M = mesh
        self.assertEqual(sum(1 for m in M if m == 6) // 2, 2)   # two names -> two black plates
        self.assertEqual(set(M.tolist()), {1, 2, 6, 7})  # white border, metal post, black face, letters
        self.assertTrue(np.isfinite(P).all())
        self.assertGreaterEqual(P[:, 2].min(), 12.0)
        self.assertLessEqual(P[:, 2].max(), 14.5)

    def test_the_sign_tile_packages_the_names_field(self):
        self.assertEqual(signstream.shape_files(1, 0, 0)["names"], "names_1_0_0.cdae")

    def test_uk_only(self):
        rows = [self.row("Test Street")]
        self.assertIsNone(self.build(rows, "US"))
        self.assertIsNone(self.build(rows, None))

    def test_short_or_unnamed_roads_are_left_out(self):
        short = np.c_[np.linspace(100.0, 106.0, 4), np.full(4, 100.0), np.zeros(4)]
        self.assertIsNone(self.build([self.row("Short Lane", short)]))
        self.assertIsNone(self.build([self.row("")]))

    def test_the_plate_stands_on_the_footway_facing_the_road(self):
        P = self.build([self.row("Test Street")])[0]
        # the run is along +x at y=100; the left footway is at y = 100 + 3 + 0.85
        self.assertAlmostEqual(float(np.median(P[:, 1])), 103.85, delta=0.2)
        self.assertGreater(float(np.max(P[:, 2])), 12.0 + namesigns.PLATE_BOTTOM)


if __name__ == "__main__":
    unittest.main()
