#!/usr/bin/env python3
"""The mod's brick garden wall (fences.py places it along OpenStreetMap's barrier=wall lines).

    python tools/build_garden_wall.py

A 2 m panel, 0.9 m high: brick body and a stone coping, Z up, centred at the origin, length along local x.
It uses the level's existing bne_bld_brick / bne_bld_stone materials (buildings.materials.json, loaded by
roads.lua), so no new textures or materials are written.
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


def wall():
    m = Mesh(6)
    body = HEIGHT - COPING_H
    m.box("bne_garden_brick", (0.0, 0.0, body / 2), (LENGTH / 2, THICK / 2, body / 2))
    m.box("bne_garden_stone", (0.0, 0.0, body + COPING_H / 2), (LENGTH / 2 + 0.02, THICK / 2 + 0.025, COPING_H / 2))
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
    print(materials())


if __name__ == "__main__":
    main()
