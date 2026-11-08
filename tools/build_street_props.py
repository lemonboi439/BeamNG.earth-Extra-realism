#!/usr/bin/env python3
"""The mod's small street props: manhole cover, gully grate, speed hump and speed camera.

    python tools/build_street_props.py

They use materials already loaded by the level (bne_lm_iron, bne_speed_hump, line_white, line_yellow), so no
texture or material file is written. Models are centred on the origin, Z up; the manhole/grate lie flat,
the hump's length runs along local x and spans local y (a low UK flat-top hump: 3.7 m long, 75 mm high,
white triangles on both ramps), the camera faces local +x. Three widths (speed_hump, _6, _5) match the
carriageway between kerbs; server streetprops.py picks one.
"""
import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth")
sys.path.insert(0, HERE)
from build_rail_crossing_props import Mesh, write_dae  # noqa: E402


def manhole():
    m = Mesh(20)
    m.cylinder("bne_lm_iron", 0.0, 0.0, 0.0, 0.025, 0.36)
    m.cylinder("bne_lm_iron", 0.0, 0.0, 0.025, 0.03, 0.31)
    return m


def grate():
    m = Mesh(6)
    m.box("bne_lm_iron", (0.0, 0.0, 0.018), (0.28, 0.46, 0.018))
    for y in np.linspace(-0.35, 0.35, 6):
        m.box("bne_lm_iron", (0.0, float(y), 0.04), (0.24, 0.025, 0.006))
    return m


HUMP_H = 0.075              # TSRGD's flat-top hump: 75 mm high (100 mm at most), 3.7 m long, 1 m ramps
HUMP_TOP_M = 0.85           # half the flat top (1.7 m), the ramps 1 m each side
HUMP_END_M = 1.85           # half the length (3.7 m)
HUMP_SKIRT_M = 0.12         # buried below the road's crowned centre so its edges never float
HUMP_MARK = "line_white"


def _up(m, mat, pts):
    """m.face with the winding turned to look up (a road marking seen from above)."""
    a = 0.5 * float(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                        for i in range(len(pts))))
    m.face(mat, pts if a >= 0.0 else list(reversed(pts)))


def hump(half_w, marks=True):
    """A UK flat-top hump half_w m across: a wedge (its bottom HUMP_SKIRT_M below the road) with white
    triangles on both ramps. The origin is at the road's centre, local x along the road."""
    m = Mesh(8)

    def z(x):
        ax = abs(x)
        return HUMP_H if ax <= HUMP_TOP_M else HUMP_H * max(0.0, (HUMP_END_M - ax) / (HUMP_END_M - HUMP_TOP_M))

    xs = (-HUMP_END_M, -HUMP_TOP_M, HUMP_TOP_M, HUMP_END_M)
    for xa, xb in zip(xs, xs[1:]):
        _up(m, "bne_speed_hump", [(xa, -half_w, z(xa)), (xb, -half_w, z(xb)),
                                  (xb, half_w, z(xb)), (xa, half_w, z(xa))])
    # the side faces reach below the asphalt (their visible part is the hump's edge; the ends meet the road itself)
    bottom = -HUMP_SKIRT_M
    for sgn in (-1, 1):
        y = sgn * half_w
        for xa, xb in zip(xs, xs[1:]):
            q = [(xa, y, bottom), (xb, y, bottom), (xb, y, z(xb)), (xa, y, z(xa))]
            m.face("bne_speed_hump", q if sgn < 0 else q[::-1])
    # white triangles on each ramp (three across, their points up the slope)
    if marks:
        for side in (-1, 1):
            xb, xa = sorted((side * (HUMP_END_M - 0.13), side * (HUMP_END_M - 0.82)))
            for yc in (-half_w + 0.6, 0.0, half_w - 0.6):
                _up(m, HUMP_MARK, [(xb, yc - 0.19, z(xb) + 0.004), (xa, yc, z(xa) + 0.004),
                                   (xb, yc + 0.19, z(xb) + 0.004)])
    return m


def camera():
    m = Mesh(8)
    m.box("bne_lm_iron", (0.0, 0.0, 2.0), (0.07, 0.07, 2.0))
    m.box("bne_lm_iron", (0.28, 0.0, 3.72), (0.34, 0.24, 0.22))
    m.box("line_yellow", (0.62, 0.0, 3.72), (0.03, 0.20, 0.18))
    m.box("bne_lm_iron", (0.0, 0.0, 0.15), (0.28, 0.28, 0.15))
    return m


def collision(name, mesh):
    c = Mesh(6)
    if name == "camera":
        c.box("bne_lm_iron", (0.0, 0.0, 2.0), (0.12, 0.12, 2.0))
        c.box("bne_lm_iron", (0.0, 0.0, 0.15), (0.28, 0.28, 0.15))
    return c


def main():
    props = (("manhole", lambda: manhole()), ("grate", lambda: grate()),
             ("speed_hump", lambda: hump(3.5)), ("speed_hump6", lambda: hump(2.9)),
             ("speed_hump5", lambda: hump(2.45)), ("speed_camera", camera))
    for name, fn in props:
        path = os.path.join(OUT, f"bne_{name}.dae")
        mesh = fn()
        if name.startswith("speed_hump"):
            half = 3.5 if name == "speed_hump" else (2.9 if name == "speed_hump6" else 2.45)
            col = hump(half, marks=False)
        else:
            col = collision(name, mesh)
        write_dae(path, f"bne_{name}", [(64, mesh)], col)
        print(path, os.path.getsize(path), "bytes", sum(len(t) for t in mesh.tri.values()), "triangles")


if __name__ == "__main__":
    main()
