#!/usr/bin/env python3
"""The mod's own fuel-station and pumpjack models (no West Coast / East Coast / Italy prop).

    python tools/build_fuel_props.py

Writes mod/.../art/shapes/beamngEarth/fuel/*.dae and fuel.materials.json: a canopy, its pillars, a pump, a
bollard, an awning and a sign for gas stations (gasstations.py places them) and one pumpjack (pumpjacks.lua).
Two levels of detail and a collision mesh each, Z up, standing on the ground at the origin.
"""
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth", "fuel")
sys.path.insert(0, HERE)
from build_rail_crossing_props import Mesh, write_dae  # noqa: E402

COLOURS = {
    "bne_fuel_white": ((0.90, 0.90, 0.87), 0.0, 0.55),
    "bne_fuel_red": ((0.55, 0.06, 0.05), 0.0, 0.45),
    "bne_fuel_metal": ((0.55, 0.57, 0.59), 0.7, 0.4),
    "bne_fuel_dark": ((0.08, 0.09, 0.10), 0.2, 0.5),
    "bne_fuel_screen": ((0.10, 0.20, 0.26), 0.0, 0.15),
    "bne_fuel_yellow": ((0.85, 0.64, 0.08), 0.0, 0.5),
    "bne_fuel_concrete": ((0.55, 0.54, 0.51), 0.0, 0.85),
    "bne_pumpjack_metal": ((0.30, 0.31, 0.33), 0.7, 0.55),
    "bne_pumpjack_dark": ((0.12, 0.12, 0.13), 0.3, 0.6),
    "bne_pumpjack_red": ((0.42, 0.08, 0.06), 0.1, 0.6),
}


def canopy():
    m = Mesh(8)
    m.box("bne_fuel_white", (0.0, 0.0, 4.8), (7.0, 8.6, 0.18))                   # roof slab
    m.box("bne_fuel_white", (0.0, 0.0, 4.55), (6.8, 8.4, 0.06))                  # underside
    for y in (-8.55, 8.55):
        m.box("bne_fuel_red", (0.0, y, 4.8), (7.08, 0.06, 0.30))                 # fascia bands
    for x in (-6.95, 6.95):
        m.box("bne_fuel_red", (x, 0.0, 4.8), (0.06, 8.6, 0.30))
    m.box("bne_fuel_red", (0.0, 0.0, 5.03), (3.2, 0.55, 0.06))                   # a brand board on the roof
    return m


def pillar():
    m = Mesh(8)
    m.box("bne_fuel_metal", (0.0, 0.0, 2.45), (0.20, 0.20, 2.45))
    m.box("bne_fuel_concrete", (0.0, 0.0, 0.06), (0.42, 0.42, 0.06))
    return m


def pump():
    m = Mesh(8)
    m.box("bne_fuel_concrete", (0.0, 0.0, 0.05), (0.55, 0.45, 0.05))             # island pad
    m.box("bne_fuel_white", (0.0, 0.0, 0.80), (0.36, 0.28, 0.75))                # body
    m.box("bne_fuel_dark", (0.0, -0.29, 1.18), (0.29, 0.02, 0.22))               # screens on both faces
    m.box("bne_fuel_screen", (0.0, -0.315, 1.18), (0.25, 0.01, 0.18))
    m.box("bne_fuel_dark", (0.0, 0.29, 1.18), (0.29, 0.02, 0.22))
    m.box("bne_fuel_screen", (0.0, 0.315, 1.18), (0.25, 0.01, 0.18))
    m.box("bne_fuel_red", (0.0, 0.0, 1.68), (0.38, 0.30, 0.14))                  # top sign
    m.box("bne_fuel_dark", (0.40, 0.0, 1.05), (0.03, 0.03, 0.55))                # hose
    return m


def bollard():
    m = Mesh(8)
    m.box("bne_fuel_yellow", (0.0, 0.0, 0.45), (0.12, 0.12, 0.45))
    m.box("bne_fuel_dark", (0.0, 0.0, 0.50), (0.125, 0.125, 0.06))
    return m


def sign():
    m = Mesh(8)
    m.box("bne_fuel_metal", (0.0, 0.0, 1.9), (0.06, 0.06, 1.9))
    m.box("bne_fuel_white", (0.0, 0.0, 3.35), (1.35, 0.07, 0.55))
    m.box("bne_fuel_red", (0.0, -0.08, 3.35), (1.35, 0.02, 0.55))
    return m


def awning():
    m = Mesh(8)
    m.box("bne_fuel_white", (0.0, 0.0, 3.6), (6.0, 6.53, 0.16))
    m.box("bne_fuel_red", (0.0, 0.0, 3.74), (6.05, 6.58, 0.06))
    for x in (-5.6, 5.6):
        m.box("bne_fuel_metal", (x, 0.0, 1.8), (0.12, 0.12, 1.8))
    return m


def eca_unit():
    """East Coast's forecourt: one roof over three islands (the dimensions gasstations.ECA_* lays)."""
    m = Mesh(8)
    cx, cy = 1.285, -6.74
    m.box("bne_fuel_white", (cx, cy, 4.8), (8.815, 3.3, 0.18))
    m.box("bne_fuel_red", (cx, cy - 3.25, 4.8), (8.86, 0.06, 0.30))
    m.box("bne_fuel_red", (cx, cy + 3.25, 4.8), (8.86, 0.06, 0.30))
    for x in (-5.215, 1.24, 7.74):
        m.box("bne_fuel_concrete", (x, -6.865, 0.05), (1.25, 0.55, 0.05))       # island
        m.box("bne_fuel_white", (x, -7.46, 0.80), (0.36, 0.28, 0.75))           # dispenser
        m.box("bne_fuel_screen", (x, -7.75, 1.18), (0.25, 0.01, 0.18))
        m.box("bne_fuel_red", (x, -7.46, 1.68), (0.38, 0.30, 0.14))
        m.box("bne_fuel_metal", (x, -6.5, 2.45), (0.16, 0.16, 2.35))            # pillar
    return m


def pumpjack():
    m = Mesh(8)
    m.box("bne_fuel_concrete", (0.0, 0.0, 0.20), (1.20, 2.00, 0.20))             # skid
    m.box("bne_pumpjack_metal", (0.0, 0.30, 2.00), (0.26, 0.26, 1.80))           # A-frame tower
    m.box("bne_pumpjack_metal", (0.0, 0.30, 3.75), (0.20, 1.65, 0.16))           # walking beam
    m.box("bne_pumpjack_red", (0.0, -1.65, 3.55), (0.26, 0.28, 0.55))            # horsehead
    m.box("bne_pumpjack_dark", (0.0, 1.55, 3.75), (0.22, 0.35, 0.26))            # counterweight
    m.box("bne_pumpjack_dark", (0.0, 1.10, 0.75), (0.45, 0.50, 0.45))            # motor
    m.box("bne_pumpjack_red", (0.0, -1.65, 0.55), (0.18, 0.18, 1.45))            # horsehead hanger
    return m


def collision_box(half, centre=(0.0, 0.0, 0.0)):
    m = Mesh(4)
    m.box("bne_fuel_concrete", centre, half)
    return m


def materials():
    out = {}
    pid = lambda n: str(uuid.uuid5(uuid.NAMESPACE_URL, "beamng-earth/" + n))
    for k, (col, met, rough) in COLOURS.items():
        out[k] = {"name": k, "mapTo": k, "class": "Material", "version": 1.5, "persistentId": pid(k),
                  "Stages": [{"baseColorFactor": [*col, 1.0], "metallicFactor": met, "roughnessFactor": rough},
                             {}, {}, {}], "translucent": False, "castShadows": True}
    with open(os.path.join(OUT, "fuel.materials.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")


def main():
    os.makedirs(OUT, exist_ok=True)
    materials()
    models = (("bne_fuel_canopy", canopy, collision_box((7.0, 8.6, 0.35), (0.0, 0.0, 4.8))),
              ("bne_fuel_pillar", pillar, collision_box((0.25, 0.25, 2.45), (0.0, 0.0, 2.45))),
              ("bne_fuel_pump", pump, collision_box((0.40, 0.33, 0.95), (0.0, 0.0, 0.95))),
              ("bne_fuel_bollard", bollard, collision_box((0.14, 0.14, 0.45), (0.0, 0.0, 0.45))),
              ("bne_fuel_sign", sign, collision_box((1.35, 0.10, 0.55), (0.0, 0.0, 3.35))),
              ("bne_fuel_awning", awning, collision_box((6.0, 6.53, 0.2), (0.0, 0.0, 3.6))),
              ("bne_fuel_unit", eca_unit, collision_box((8.8, 3.3, 0.2), (1.285, -6.74, 4.8))),
              ("bne_pumpjack", pumpjack, collision_box((1.2, 2.0, 1.8), (0.0, 0.0, 1.8))))
    for name, fn, col in models:
        m = fn()
        far = Mesh(0)
        # a far level: the same boxes with every second triangle dropped would be more code than it saves; the models
        # are small and near only (the server places them within road tiles)
        path = os.path.join(OUT, name + ".dae")
        write_dae(path, name, [(64, m), (8, m)], col)
        print(path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
