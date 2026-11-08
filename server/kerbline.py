"""London's red-route kerb lines from TfL's Red Route Controls (Open Government Licence), matched to a road by
proximity: a control line within RED_REACH_M of the road turns its kerb lines red, double where the order is
"No Stopping At Any Time" and single where it is timed.

The controls are downloaded once into a compact grid (server/data/tfl_red_routes.npz: x, y in metres about
51.50 N, 0.12 W, double 1/2 for timed/any-time) by the data build; this module only reads it. Roads outside
London match nothing, so the rest of the world keeps its yellow/default policy.
"""
import math
import os
import threading

import numpy as np

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tfl_red_routes.npz")
LAT0, LON0 = 51.50, -0.12
COS = math.cos(math.radians(LAT0))
CELL_M = 40.0
RED_REACH_M = 10.0          # m from a control line to the road centreline that counts the road as a red route


class _Index:
    __slots__ = ("x", "y", "double", "keys", "order", "x0", "x1", "y0", "y1")

    def __init__(self):
        with np.load(DATA, allow_pickle=False) as a:
            x = np.asarray(a["x"], np.float64)
            y = np.asarray(a["y"], np.float64)
            d = np.asarray(a["double"], np.uint8)
        cx = np.floor(x / CELL_M).astype(np.int64) + 100000
        cy = np.floor(y / CELL_M).astype(np.int64) + 100000
        key = cx * 1000000 + cy
        order = np.argsort(key, kind="stable")
        self.x, self.y, self.double = x, y, d
        self.keys, self.order = key[order], order
        self.x0, self.x1, self.y0, self.y1 = float(x.min()), float(x.max()), float(y.min()), float(y.max())

    def style(self, x, y):
        """'double_red' / 'single_red' / None for a set of local metres points near the controls."""
        if not len(x):
            return None
        if float(np.max(x)) < self.x0 - RED_REACH_M or float(np.min(x)) > self.x1 + RED_REACH_M \
                or float(np.max(y)) < self.y0 - RED_REACH_M or float(np.min(y)) > self.y1 + RED_REACH_M:
            return None
        best = 0
        r2 = RED_REACH_M * RED_REACH_M
        for px, py in zip(np.asarray(x, np.float64), np.asarray(y, np.float64)):
            cx = int(math.floor(px / CELL_M)) + 100000
            cy = int(math.floor(py / CELL_M)) + 100000
            for gx in (cx - 1, cx, cx + 1):
                base = gx * 1000000
                for gy in (cy - 1, cy, cy + 1):
                    key = base + gy
                    i0 = int(np.searchsorted(self.keys, key, side="left"))
                    i1 = int(np.searchsorted(self.keys, key, side="right"))
                    if i0 == i1:
                        continue
                    idx = self.order[i0:i1]
                    d2 = (self.x[idx] - px) ** 2 + (self.y[idx] - py) ** 2
                    near = d2 <= r2
                    if near.any():
                        best = max(best, int(self.double[idx[near]].max()))
        return "double_red" if best >= 2 else ("single_red" if best >= 1 else None)


_index = None
_lock = threading.Lock()
_missing = False


def _get():
    global _index, _missing
    if _index is not None or _missing:
        return _index
    with _lock:
        if _index is None and not _missing:
            try:
                _index = _Index()
            except (OSError, ValueError, KeyError):
                _missing = True
    return _index


def red_style(frame, pts):
    """The TfL red-route style near a road centreline (ENU (k, 2+)), or None."""
    idx = _get()
    if idx is None or pts is None or len(pts) < 2:
        return None
    p = np.asarray(pts, np.float64)[:, :2]
    step = max(1, len(p) // 16)
    p = p[::step]
    la, lo, _ = frame.enu_to_geodetic(p[:, 0], p[:, 1], np.zeros(len(p)))
    x = (np.asarray(lo, np.float64) - LON0) * 111320.0 * COS
    y = (np.asarray(la, np.float64) - LAT0) * 111320.0
    return idx.style(x, y)
