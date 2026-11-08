#!/usr/bin/env python3
"""The mod's brick garden walls and their gate piers (fences.py places the panels along OpenStreetMap's
barrier=wall lines, roads.py / furniture.py a pier at each side of a dropped-kerb driveway).

    python tools/build_garden_wall.py

A 2 m panel, 0.9 m high: brick body and a stone coping, Z up, centred at the origin, length along local x;
and a 0.46 m square gate pier, 1.1 m high (the CC0 ambientCG brick of tools/import_brick_texture.py, the
stone coping of the buildings' palette).
"""
import copy
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth")
sys.path.insert(0, HERE)
from build_rail_crossing_props import Mesh, write_dae  # noqa: E402

LENGTH, THICK, HEIGHT = 2.0, 0.23, 0.90
COPING_H = 0.08
PIER_W, PIER_H = 0.46, 1.02
PIER_CAP = 0.09


def wall():
    m = Mesh(6)
    body = HEIGHT - COPING_H
    m.box("bne_garden_brick", (0.0, 0.0, body / 2), (LENGTH / 2, THICK / 2, body / 2))
    m.box("bne_garden_stone", (0.0, 0.0, body + COPING_H / 2), (LENGTH / 2 + 0.02, THICK / 2 + 0.025, COPING_H / 2))
    return m


def pier():
    """A square brick pier with a stone cap: the wall's end at a driveway mouth (the map has the gap, not a pier)."""
    m = Mesh(6)
    m.box("bne_garden_brick", (0.0, 0.0, PIER_H / 2), (PIER_W / 2, PIER_W / 2, PIER_H / 2))
    m.box("bne_garden_stone", (0.0, 0.0, PIER_H + PIER_CAP / 2),
          (PIER_W / 2 + 0.04, PIER_W / 2 + 0.04, PIER_CAP / 2))
    return m


def materials():
    """The brick and stone materials of buildings.materials.json under the wall's own names (the DAE's)."""
    with open(os.path.join(OUT, "buildings.materials.json"), encoding="utf-8") as f:
        src = json.load(f)
    pid = lambda n: str(uuid.uuid5(uuid.NAMESPACE_URL, "beamng-earth/" + n))   # noqa: E731
    out = {}
    for new, old in (("bne_garden_brick", "bne_bld_brick"), ("bne_garden_stone", "bne_bld_stone")):
        d = copy.deepcopy(src[old])
        d["name"] = d["mapTo"] = new
        d["persistentId"] = pid(new)
        if new == "bne_garden_brick":       # the CC0 ambientCG Bricks075A set (tools/import_brick_texture.py)
            d["Stages"][0].update({
                "baseColorMap": "/levels/beamng_earth/art/shapes/beamngEarth/bne_tex/"
                                "t_bne_garden_brick_b.color.dds",
                "normalMap": "/levels/beamng_earth/art/shapes/beamngEarth/bne_tex/"
                             "t_bne_garden_brick_nm.normal.dds",
                "roughnessMap": "/levels/beamng_earth/art/shapes/beamngEarth/bne_tex/"
                                "t_bne_garden_brick_r.data.dds",
            })
        out[new] = d
    path = os.path.join(OUT, "garden.materials.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return path


def main():
    path = os.path.join(OUT, "bne_garden_wall.dae")
    mesh = wall()
    write_dae(path, "bne_garden_wall", [(64, mesh)], Mesh(4))
    print(path, os.path.getsize(path), "bytes", sum(len(t) for t in mesh.tri.values()), "triangles")
    ppath = os.path.join(OUT, "bne_gate_pier.dae")
    pm = pier()
    write_dae(ppath, "bne_gate_pier", [(64, pm)], Mesh(4))
    print(ppath, os.path.getsize(ppath), "bytes", sum(len(t) for t in pm.tri.values()), "triangles")
    print(materials())


if __name__ == "__main__":
    main()
